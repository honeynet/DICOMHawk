import hashlib
import logging
import weakref
import ujson

from datetime import datetime, timezone
from logging import Logger
from logging.handlers import TimedRotatingFileHandler, RotatingFileHandler
from pathlib import Path
from typing import Optional

from pynetdicom.events import Event
from pydicom.dataset import Dataset

logger = logging.getLogger(__name__)

_BULK_DATA_KEYWORDS: frozenset[str] = frozenset({
    "PixelData",
    "FloatPixelData",
    "DoubleFloatPixelData",
    "OverlayData",
    "WaveformData",
    "SpectroscopyData",
    "EncapsulatedDocument",
})


class SessionCache:
    """Tracks session IDs and requestor version names per live association.

    Uses weakref.finalize so entries are purged automatically when the
    association object is GC'd — important because attackers often drop TCP
    connections without sending EVT_RELEASED or EVT_ABORTED, which means
    clear() may never be called and entries would otherwise leak indefinitely.
    """

    def __init__(self) -> None:
        self._sessions: dict[int, str] = {}
        self._versions: dict[int, str] = {}

    def get_session_id(self, assoc) -> str:
        key = id(assoc)
        if key not in self._sessions:
            self._sessions[key] = str(int(datetime.now(timezone.utc).timestamp() * 1000))
            weakref.finalize(assoc, self._cleanup, key)
        return self._sessions[key]

    def _cleanup(self, key: int) -> None:
        self._sessions.pop(key, None)
        self._versions.pop(key, None)

    def cache_version(self, assoc, version: str) -> None:
        """Cache the requestor's implementation version name from the A-ASSOCIATE-RQ."""
        self._versions[id(assoc)] = version

    def clear(self, assoc) -> None:
        """Remove session entry for a closed association. Called by release/abort handlers."""
        key = id(assoc)
        self._sessions.pop(key, None)
        self._versions.pop(key, None)

    def get_version(self, assoc) -> str | None:
        return self._versions.get(id(assoc))


def _query_level(ds: Dataset) -> str | None:
    try:
        val = str(ds.QueryRetrieveLevel).strip()
        return val or None
    except AttributeError:
        return None


def _extract_params(ds: Dataset) -> list[str] | None:
    """Return 'Key: Value' strings for non-empty, non-bulk dataset fields."""
    params = []
    for elem in ds:
        if elem.keyword in _BULK_DATA_KEYWORDS or not elem.keyword:
            continue
        if elem.keyword == "QueryRetrieveLevel":
            continue  # captured separately in the query_level field
        try:
            val = str(elem.value).strip()
            if val:
                params.append(f"{elem.keyword}: {val}")
        except Exception:
            logger.debug("Failed to stringify element %s", elem.keyword, exc_info=True)
    return params if params else None


def hash_request(evt: Event) -> str:
    """SHA-256 of the raw C-STORE request bytes as received over the wire."""
    raw = evt.request.DataSet
    pos = raw.tell()
    raw.seek(0)
    digest = hashlib.sha256(raw.read()).hexdigest()
    raw.seek(pos)
    return digest


class InteractionEvent:
    """One structured JSON line in the honeypot interaction log."""

    def __init__(
        self,
        evt: Event,
        cache: SessionCache,
        request_type: str,
        *,
        query_level: str | None = None,
        session_parameters: list[str] | None = None,
        matches: int | None = None,
        log_level: str = "INFO",
    ) -> None:
        assoc = evt.assoc
        self.session_id = cache.get_session_id(assoc)
        self.request_type = request_type
        self.query_level = query_level
        self.session_parameters = session_parameters
        self.matches = matches
        self.log_level = log_level
        self.version = cache.get_version(assoc)
        self.ip = assoc.requestor.address
        self.port = assoc.requestor.port
        self.timestamp = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    def __str__(self) -> str:
        return ujson.dumps({
            "session_id": self.session_id,
            "request_type": self.request_type,
            "query_level": self.query_level,
            "session_parameters": self.session_parameters,
            "log_level": self.log_level,
            "version": self.version,
            "ip": self.ip,
            "port": self.port,
            "matches": self.matches,
            "timestamp": self.timestamp,
        })


def _add_file_handler(
    lg: Logger,
    stdout: str,
    when: Optional[str],
    interval: int,
    size: Optional[int],
) -> None:
    Path(stdout).parent.mkdir(parents=True, exist_ok=True)
    if when:
        h = TimedRotatingFileHandler(stdout, when=when, interval=interval)
    elif size:
        h = RotatingFileHandler(stdout, maxBytes=size)
    else:
        h = logging.FileHandler(stdout)
    lg.addHandler(h)


def new_bus(
    stdout: Optional[str] = None,
    when: Optional[str] = None,
    interval: int = 1,
    size: Optional[int] = None,
) -> Logger:
    lg = logging.getLogger("bus")
    lg.setLevel(logging.INFO)
    lg.propagate = False  # prevent JSON lines leaking into the root/dev logger
    if stdout:
        _add_file_handler(lg, stdout, when, interval, size)
    return lg


def new_dev_log(
    stdout: str,
    when: Optional[str] = None,
    interval: int = 1,
    size: Optional[int] = None,
) -> None:
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    _add_file_handler(root, stdout, when, interval, size)
