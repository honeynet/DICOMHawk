import hashlib
import logging
import sys
import weakref

from datetime import datetime, timezone
from logging import Logger
from logging.handlers import TimedRotatingFileHandler, RotatingFileHandler
from pathlib import Path

import ujson

from pydicom.dataset import Dataset
from pynetdicom.events import Event

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
        self.local_port = assoc.acceptor.port
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
            "local_port": self.local_port,
            "matches": self.matches,
            "timestamp": self.timestamp,
        })


_LEVEL_COLORS: dict[str, str] = {
    "INFO":    "\033[92m",  # green
    "WARNING": "\033[93m",  # yellow
    "ERROR":   "\033[91m",  # red
}
_RESET = "\033[0m"
_DIM   = "\033[2m"


class _ConsoleFormatter(logging.Formatter):
    """One-liner per event for the terminal; reads InteractionEvent directly, no JSON round-trip."""

    def __init__(self, use_color: bool) -> None:
        super().__init__()
        self._color = use_color

    def format(self, record: logging.LogRecord) -> str:
        ie = record.msg if isinstance(record.msg, InteractionEvent) else None
        if ie is None:
            msg = record.getMessage()
            if self._color and record.levelno >= logging.WARNING:
                c = _LEVEL_COLORS.get(record.levelname, "")
                return f"{c}{record.levelname}{_RESET}  {msg}"
            return f"{record.levelname}  {msg}"

        c     = _LEVEL_COLORS.get(ie.log_level, "") if self._color else ""
        reset = _RESET if self._color else ""
        dim   = _DIM   if self._color else ""

        ts    = ie.timestamp[11:19]  # HH:MM:SS from ISO string
        parts = [
            f"{dim}{ts}{reset}",
            f"{ie.ip}:{ie.port}",
            f":{ie.local_port}",
            f"{c}{ie.request_type:<22}{reset}",
        ]
        if ie.query_level:
            parts.append(ie.query_level)
        if ie.matches is not None:
            parts.append(f"matches={ie.matches}")
        if ie.session_parameters:
            parts.append(ie.session_parameters[0][:60])
        if ie.version:
            parts.append(f"{dim}[{ie.version}]{reset}")
        return "  ".join(parts)


class LevelColorFormatter(logging.Formatter):
    """Colors the whole record by level (green/yellow/red) for terminal output."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        c = _LEVEL_COLORS.get(record.levelname)
        return f"{c}{line}{_RESET}" if c else line


def new_bus(
    stdout: str | None = None,
    when: str | None = None,
    interval: int = 1,
    size: int | None = None,
    verbose: bool = False,
) -> Logger:
    lg = logging.getLogger("bus")
    lg.setLevel(logging.INFO)
    lg.propagate = False  # prevent JSON lines leaking into the root/dev logger
    if stdout:
        lg.addHandler(_build_handler(stdout, when, interval, size))
    if verbose or sys.stdout.isatty():
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(_ConsoleFormatter(use_color=sys.stdout.isatty()))
        lg.addHandler(h)
    return lg


class _InnerFrameFormatter(logging.Formatter):
    """Prepends '[in module]' from the innermost traceback frame, not the caller."""

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        if record.exc_info:
            tb = record.exc_info[2]
            if tb:
                while tb.tb_next:
                    tb = tb.tb_next
                module = tb.tb_frame.f_globals.get("__name__", "?")
                base = f"[in {module}] {base}"
        return base


def new_dev_log(
    stdout: str,
    when: str | None = None,
    interval: int = 1,
    size: int | None = None,
) -> None:
    fmt = _InnerFrameFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    h = _build_handler(stdout, when, interval, size)
    h.setFormatter(fmt)
    root.addHandler(h)

    # Attach pynetdicom's own logger at INFO to the same file so association
    # negotiation detail is available without the full DEBUG PDU dump.
    pn = logging.getLogger("pynetdicom")
    pn.setLevel(logging.INFO)
    pn.propagate = False  # keep pynetdicom INFO out of the root WARNING stream
    h2 = _build_handler(stdout, when, interval, size)
    h2.setFormatter(fmt)
    pn.addHandler(h2)


def _build_handler(
    stdout: str,
    when: str | None,
    interval: int,
    size: int | None,
) -> logging.Handler:
    Path(stdout).parent.mkdir(parents=True, exist_ok=True)
    if when:
        return TimedRotatingFileHandler(stdout, when=when, interval=interval)
    if size:
        return RotatingFileHandler(stdout, maxBytes=size)
    return logging.FileHandler(stdout)
