import hashlib
import logging
import sys
import threading
import weakref
from collections import deque

from datetime import datetime, timezone
from logging import Logger
from logging.handlers import TimedRotatingFileHandler, RotatingFileHandler
from pathlib import Path

import ujson

from pydicom.dataset import Dataset
from pynetdicom.events import Event

from .status import QRStatus

logger = logging.getLogger(__name__)

_BULK_DATA_KEYWORDS: frozenset[str] = frozenset(
    {
        "PixelData",
        "FloatPixelData",
        "DoubleFloatPixelData",
        "OverlayData",
        "WaveformData",
        "SpectroscopyData",
        "EncapsulatedDocument",
    }
)
_PARAM_VALUE_LIMIT = 4096
_PARAM_COUNT_LIMIT = 128
_DEFAULT_LOG_SIZE = 50 * 1024 * 1024
_DEFAULT_LOG_BACKUPS = 5


class SessionCache:
    """Tracks session IDs/versions per live association; weakref.finalize purges entries on
    GC since attackers often drop the TCP connection without EVT_RELEASED/EVT_ABORTED.
    """

    def __init__(self) -> None:
        self._sessions: dict[int, str] = {}
        self._versions: dict[int, str] = {}
        self._lock = threading.Lock()
        self._last_id = 0

    def get_session_id(self, assoc) -> str:
        key = id(assoc)
        with self._lock:
            sid = self._sessions.get(key)
            if sid is None:
                ms = int(datetime.now(timezone.utc).timestamp() * 1000)
                # Same-millisecond associations would collide; force monotonic uniqueness.
                if ms <= self._last_id:
                    ms = self._last_id + 1
                self._last_id = ms
                sid = str(ms)
                self._sessions[key] = sid
                weakref.finalize(assoc, self._cleanup, key)
        return sid

    def _cleanup(self, key: int) -> None:
        with self._lock:
            self._sessions.pop(key, None)
            self._versions.pop(key, None)

    def cache_version(self, assoc, version: str) -> None:
        """Cache the requestor's implementation version name from the A-ASSOCIATE-RQ."""
        with self._lock:
            self._versions[id(assoc)] = version

    def clear(self, assoc) -> None:
        """Remove session entry for a closed association. Called by release/abort handlers."""
        key = id(assoc)
        with self._lock:
            self._sessions.pop(key, None)
            self._versions.pop(key, None)

    def get_version(self, assoc) -> str | None:
        with self._lock:
            return self._versions.get(id(assoc))


def _query_level(ds: Dataset) -> str | None:
    try:
        val = str(ds.QueryRetrieveLevel).strip()
        return val or None
    except AttributeError:
        return None


def _extract_params(ds: Dataset) -> list[str] | None:
    """'Key: Value' for filter fields; empty (universal) keys summarized as 'Requested: ...'."""
    params = []
    requested = []
    for elem in ds:
        if len(params) + len(requested) >= _PARAM_COUNT_LIMIT:
            params.append("Additional query keys omitted")
            break
        if elem.keyword in _BULK_DATA_KEYWORDS or not elem.keyword:
            continue
        if elem.keyword == "QueryRetrieveLevel":
            continue  # captured separately in the query_level field
        try:
            val = "" if elem.value is None else str(elem.value).strip()
        except Exception:
            logger.debug("Failed to stringify element %s", elem.keyword, exc_info=True)
            continue
        if val:
            if len(val) > _PARAM_VALUE_LIMIT:
                val = val[:_PARAM_VALUE_LIMIT] + "...[truncated]"
            params.append(f"{elem.keyword}: {val}")
        else:
            requested.append(elem.keyword)
    if requested:
        params.append(f"Requested: {', '.join(requested)}")
    return params if params else None


def hash_request(evt: Event) -> str:
    """SHA-256 of the raw C-STORE request bytes as received over the wire."""
    raw = evt.request.DataSet
    pos = raw.tell()
    raw.seek(0)
    digest = hashlib.sha256()
    try:
        while chunk := raw.read(1024 * 1024):
            digest.update(chunk)
        return digest.hexdigest()
    finally:
        raw.seek(pos)


class InteractionEvent:
    """One JSON line in the interaction log; `channel` tags the protocol (DIMSE/WEB/DICOMWEB)."""

    def __init__(
        self,
        evt: Event,
        cache: SessionCache,
        request_type: str,
        *,
        query_level: str | None = None,
        session_parameters: list[str] | None = None,
        matches: int | None = None,
        status: "QRStatus | None" = None,
        log_level: str = "INFO",
    ) -> None:
        assoc = evt.assoc
        # Connection events carry the peer in event.address; DIMSE/ACSE use the requestor.
        addr = getattr(evt, "address", None)
        ip, port = (
            (addr[0], addr[1])
            if addr is not None
            else (assoc.requestor.address, assoc.requestor.port)
        )
        self._populate(
            channel="DIMSE",
            session_id=cache.get_session_id(assoc),
            request_type=request_type,
            query_level=query_level,
            session_parameters=session_parameters,
            matches=matches,
            status=status,
            log_level=log_level,
            version=cache.get_version(assoc),
            ip=ip,
            port=port,
            local_port=getattr(assoc.acceptor, "port", None),
        )

    @classmethod
    def from_http(
        cls,
        channel: str,
        request_type: str,
        *,
        session_id: str,
        ip: str | None,
        port: int | None,
        local_port: int | None = None,
        session_parameters: list[str] | None = None,
        matches: int | None = None,
        log_level: str = "INFO",
        method: str | None = None,
        path: str | None = None,
        user_agent: str | None = None,
    ) -> "InteractionEvent":
        """Build the same log line from an HTTP request (web / DICOMweb), no pynetdicom Event."""
        self = cls.__new__(cls)
        self._populate(
            channel=channel,
            session_id=session_id,
            request_type=request_type,
            query_level=None,
            session_parameters=session_parameters,
            matches=matches,
            status=None,
            log_level=log_level,
            version=None,
            ip=ip,
            port=port,
            local_port=local_port,
            method=method,
            path=path,
            user_agent=user_agent,
        )
        return self

    def _populate(
        self,
        *,
        channel,
        session_id,
        request_type,
        query_level,
        session_parameters,
        matches,
        status,
        log_level,
        version,
        ip,
        port,
        local_port,
        method=None,
        path=None,
        user_agent=None,
    ) -> None:
        self.channel = channel
        self.session_id = session_id
        self.request_type = request_type
        self.query_level = query_level
        self.session_parameters = session_parameters
        self.matches = matches
        # DIMSE outcome returned to the peer; None for non-DIMSE events.
        self.status: str | None = (
            f"{status.name} (0x{int(status):04X})" if status is not None else None
        )
        self.log_level = log_level
        self.version = version
        self.ip = ip
        self.port = port
        self.local_port = local_port
        # HTTP-only; None for DIMSE.
        self.method = method
        self.path = path
        self.user_agent = user_agent
        self.timestamp = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    def __str__(self) -> str:
        return ujson.dumps(
            {
                "session_id": self.session_id,
                "channel": self.channel,
                "request_type": self.request_type,
                "query_level": self.query_level,
                "session_parameters": self.session_parameters,
                "status": self.status,
                "log_level": self.log_level,
                "version": self.version,
                "ip": self.ip,
                "port": self.port,
                "local_port": self.local_port,
                "matches": self.matches,
                "method": self.method,
                "path": self.path,
                "user_agent": self.user_agent,
                "timestamp": self.timestamp,
            },
            escape_forward_slashes=False,
        )


_LEVEL_COLORS: dict[str, str] = {
    "INFO": "\033[92m",  # green
    "WARNING": "\033[93m",  # yellow
    "ERROR": "\033[91m",  # red
}
_RESET = "\033[0m"
_DIM = "\033[2m"


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

        c = _LEVEL_COLORS.get(ie.log_level, "") if self._color else ""
        reset = _RESET if self._color else ""
        dim = _DIM if self._color else ""

        ts = ie.timestamp[11:19]  # HH:MM:SS from ISO string
        parts = [
            f"{dim}{ts}{reset}",
            f"{dim}{ie.channel}{reset}",
            f"{ie.ip}:{ie.port}",
            f":{ie.local_port}",
            f"{c}{ie.request_type:<22}{reset}",
        ]
        if ie.query_level:
            parts.append(ie.query_level)
        if ie.matches is not None:
            parts.append(f"matches={ie.matches}")
        if ie.status:
            parts.append(f"{dim}->{reset} {ie.status}")
        if ie.session_parameters:
            parts.append("  ".join(p[:60] for p in ie.session_parameters))
        if ie.version:
            parts.append(f"{dim}[{ie.version}]{reset}")
        return "  ".join(parts)


class LevelColorFormatter(logging.Formatter):
    """Colors the whole record by level (green/yellow/red) for terminal output."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        c = _LEVEL_COLORS.get(record.levelname)
        return f"{c}{line}{_RESET}" if c else line


class RecentEventsHandler(logging.Handler):
    """Bounded in-memory history of InteractionEvents, for the operator API to query."""

    def __init__(self, maxlen: int = 500) -> None:
        super().__init__()
        self.events: deque[InteractionEvent] = deque(maxlen=maxlen)
        self._events_lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        if isinstance(record.msg, InteractionEvent):
            with self._events_lock:
                self.events.append(record.msg)

    def snapshot(self) -> list[InteractionEvent]:
        with self._events_lock:
            return list(self.events)


def recent_events(logger: Logger) -> RecentEventsHandler | None:
    """Find the RecentEventsHandler new_bus() attached, if any."""
    for h in logger.handlers:
        if isinstance(h, RecentEventsHandler):
            return h
    return None


def new_bus(
    stdout: str | None = None,
    when: str | None = None,
    interval: int = 1,
    size: int | None = _DEFAULT_LOG_SIZE,
    backups: int = _DEFAULT_LOG_BACKUPS,
    verbose: bool = False,
) -> Logger:
    if size is not None and size < 1:
        raise ValueError("log rotation size must be positive or None")
    if (size is not None or when) and backups < 1:
        raise ValueError("rotating logs require at least one backup")
    lg = logging.getLogger("bus")
    lg.setLevel(logging.INFO)
    lg.propagate = False  # prevent JSON lines leaking into the root/dev logger
    _remove_owned_handlers(lg)
    lg.addHandler(_owned(RecentEventsHandler()))
    if stdout:
        lg.addHandler(_owned(_build_handler(stdout, when, interval, size, backups)))
    if verbose or sys.stdout.isatty():
        h = logging.StreamHandler(sys.stdout)
        h.setFormatter(_ConsoleFormatter(use_color=sys.stdout.isatty()))
        lg.addHandler(_owned(h))
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
    size: int | None = _DEFAULT_LOG_SIZE,
    backups: int = _DEFAULT_LOG_BACKUPS,
) -> None:
    if size is not None and size < 1:
        raise ValueError("log rotation size must be positive or None")
    if (size is not None or when) and backups < 1:
        raise ValueError("rotating logs require at least one backup")
    fmt = _InnerFrameFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    _remove_owned_handlers(root)
    h = _owned(_build_handler(stdout, when, interval, size, backups))
    h.setFormatter(fmt)
    root.addHandler(h)

    # Attach pynetdicom's own logger at INFO to the same file so association
    # negotiation detail is available without the full DEBUG PDU dump.
    pn = logging.getLogger("pynetdicom")
    pn.setLevel(logging.INFO)
    pn.propagate = False  # keep pynetdicom INFO out of the root WARNING stream
    _remove_owned_handlers(pn)
    pn.addHandler(h)  # one shared rotating handler avoids writers racing a rollover


def _build_handler(
    stdout: str,
    when: str | None,
    interval: int,
    size: int | None,
    backups: int,
) -> logging.Handler:
    Path(stdout).parent.mkdir(parents=True, exist_ok=True)
    if when:
        return TimedRotatingFileHandler(
            stdout, when=when, interval=interval, backupCount=backups
        )
    if size:
        return RotatingFileHandler(stdout, maxBytes=size, backupCount=backups)
    return logging.FileHandler(stdout)


def _owned(handler: logging.Handler) -> logging.Handler:
    handler._dicomhawk_owned = True  # type: ignore[attr-defined]
    return handler


def _remove_owned_handlers(target: logging.Logger) -> None:
    for handler in list(target.handlers):
        if getattr(handler, "_dicomhawk_owned", False):
            target.removeHandler(handler)
            handler.close()
