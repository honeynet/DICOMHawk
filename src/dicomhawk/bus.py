import hashlib
import logging
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

# Maps id(assoc) → session_id (epoch-ms string). Cleared on release/abort.
_sessions: dict[int, str] = {}
# Maps id(assoc) → requestor's implementation_version_name from A-ASSOCIATE-RQ.
# Cached in handle_associate because assoc.requestor.primitive is overwritten
# by pynetdicom during negotiation and loses the original DCMTK/peer version.
_versions: dict[int, str] = {}


def _get_session_id(assoc) -> str:
    key = id(assoc)
    if key not in _sessions:
        _sessions[key] = str(int(datetime.now(timezone.utc).timestamp() * 1000))
    return _sessions[key]


def cache_version(assoc, version: str) -> None:
    """Cache the requestor's implementation version name from the A-ASSOCIATE-RQ."""
    _versions[id(assoc)] = version


def clear_session(assoc) -> None:
    """Remove session entry for a closed association. Called by release/abort handlers."""
    key = id(assoc)
    _sessions.pop(key, None)
    _versions.pop(key, None)


def _version(assoc) -> str:
    return _versions.get(id(assoc), "N/A")


def _query_level(ds: Dataset) -> str:
    try:
        return str(ds.QueryRetrieveLevel).strip() or "N/A"
    except AttributeError:
        return "N/A"


def _extract_params(ds: Dataset) -> list[str] | str:
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
            pass
    return params if params else "N/A"


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
        request_type: str,
        *,
        query_level: str = "N/A",
        session_parameters: list[str] | str = "N/A",
        matches: int | str = "N/A",
        log_level: str = "INFO",
    ) -> None:
        assoc = evt.assoc
        self.session_id = _get_session_id(assoc)
        self.request_type = request_type
        self.query_level = query_level
        self.session_parameters = session_parameters
        self.matches = matches
        self.log_level = log_level
        self.version = _version(assoc)
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


def new_interaction(
    evt: Event,
    request_type: str,
    *,
    query_level: str = "N/A",
    session_parameters: list[str] | str = "N/A",
    matches: int | str = "N/A",
    log_level: str = "INFO",
) -> InteractionEvent:
    return InteractionEvent(
        evt, request_type,
        query_level=query_level,
        session_parameters=session_parameters,
        matches=matches,
        log_level=log_level,
    )


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
    """Route internal Python logger output (warnings, errors, exceptions) to a file."""
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    _add_file_handler(root, stdout, when, interval, size)
