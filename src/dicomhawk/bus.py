import copy
import logging
import os
import sys
import threading
import time
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

try:
    import fcntl
except ImportError:
    fcntl = None

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
_CONNECTION_PREFIX_LIMIT = 4096


class _MultiprocessFileMixin:
    """Serialize writes and rollover across independent worker processes.

    logging's rotating handlers only protect threads in one process. The analysis
    analysis worker configures its own interaction logger, so independent handlers can
    otherwise rename and continue writing different generations of the same file.
    A stable sidecar lock coordinates those handlers, and the inode check reopens
    the active file after another process rotates it.
    """

    _lock_stream = None
    _lock_pid = None

    def _acquire_process_lock(self) -> bool:
        if fcntl is None:
            return False
        pid = os.getpid()
        if self._lock_pid != pid:
            inherited, self._lock_stream = self._lock_stream, None
            self._lock_pid = None
            if inherited is not None:
                try:
                    inherited.close()
                except (OSError, ValueError):
                    pass
            stream = open(f"{self.baseFilename}.lock", "a", encoding="utf-8")
            self._lock_stream = stream
            self._lock_pid = pid
        fcntl.flock(self._lock_stream.fileno(), fcntl.LOCK_EX)
        return True

    def _release_process_lock(self) -> None:
        if fcntl is None or self._lock_stream is None:
            return
        try:
            fcntl.flock(self._lock_stream.fileno(), fcntl.LOCK_UN)
        except (OSError, ValueError):
            pass

    def _reopen_after_external_rollover(self) -> None:
        if self.stream is None:
            self.stream = self._open()
            return
        try:
            current = os.stat(self.baseFilename)
            opened = os.fstat(self.stream.fileno())
            unchanged = (current.st_dev, current.st_ino) == (
                opened.st_dev,
                opened.st_ino,
            )
        except (FileNotFoundError, OSError, ValueError):
            unchanged = False
        if not unchanged:
            self.stream.close()
            self.stream = self._open()

    def emit(self, record: logging.LogRecord) -> None:
        acquired = False
        try:
            acquired = self._acquire_process_lock()
            self._reopen_after_external_rollover()
            should_rollover = getattr(self, "shouldRollover", None)
            if should_rollover is not None and should_rollover(record):
                self.doRollover()
            logging.FileHandler.emit(self, record)
        except Exception:
            self.handleError(record)
        finally:
            if acquired:
                self._release_process_lock()

    def close(self) -> None:
        try:
            super().close()
        finally:
            if self._lock_stream is not None:
                try:
                    self._lock_stream.close()
                except (OSError, ValueError):
                    pass
                self._lock_stream = None
                self._lock_pid = None


class MultiprocessRotatingFileHandler(_MultiprocessFileMixin, RotatingFileHandler):
    pass


class MultiprocessTimedRotatingFileHandler(
    _MultiprocessFileMixin, TimedRotatingFileHandler
):
    pass


class MultiprocessFileHandler(_MultiprocessFileMixin, logging.FileHandler):
    pass


class SessionCache:
    """Track associations and purge peers that disconnect without ACSE cleanup."""

    def __init__(self) -> None:
        self._sessions: dict[int, str] = {}
        self._versions: dict[int, str] = {}
        self._connections: dict[int, dict] = {}
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
            self._connections.pop(key, None)

    def connection_opened(self, assoc) -> None:
        key = id(assoc)
        with self._lock:
            self._connections[key] = {
                "opened": time.monotonic(),
                "bytes": 0,
                "prefix": bytearray(),
                "association_seen": False,
            }

    def connection_data(self, assoc, data: bytes) -> None:
        key = id(assoc)
        with self._lock:
            observation = self._connections.setdefault(
                key,
                {
                    "opened": time.monotonic(),
                    "bytes": 0,
                    "prefix": bytearray(),
                    "association_seen": False,
                },
            )
            observation["bytes"] += len(data)
            remaining = _CONNECTION_PREFIX_LIMIT - len(observation["prefix"])
            if remaining > 0:
                observation["prefix"].extend(data[:remaining])

    def association_seen(self, assoc) -> None:
        with self._lock:
            observation = self._connections.get(id(assoc))
            if observation is not None:
                observation["association_seen"] = True

    def connection_closed(self, assoc) -> dict:
        with self._lock:
            observation = self._connections.pop(id(assoc), None)
        if observation is None:
            return {
                "bytes": 0,
                "prefix": b"",
                "association_seen": False,
                "duration": 0.0,
            }
        observation["prefix"] = bytes(observation["prefix"])
        observation["duration"] = max(0.0, time.monotonic() - observation.pop("opened"))
        return observation

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
        artifact: dict | None = None,
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
            artifact=artifact,
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
        artifact: dict | None = None,
        fingerprint_hash: str | None = None,
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
            artifact=artifact,
            fingerprint_hash=fingerprint_hash,
        )
        return self

    @classmethod
    def background(
        cls,
        channel: str,
        request_type: str,
        *,
        session_id: str | None,
        artifact_id: str | None = None,
        analysis: dict | None = None,
        session_parameters: list[str] | None = None,
        log_level: str = "INFO",
    ) -> "InteractionEvent":
        """Build a log line with no live request/association context, such as an async analysis result."""
        self = cls.__new__(cls)
        self._populate(
            channel=channel,
            session_id=session_id,
            request_type=request_type,
            query_level=None,
            session_parameters=session_parameters,
            matches=None,
            status=None,
            log_level=log_level,
            version=None,
            ip=None,
            port=None,
            local_port=None,
            artifact_id=artifact_id,
            analysis=analysis,
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
        artifact=None,
        artifact_id=None,
        analysis=None,
        fingerprint_hash=None,
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
        # Structured payload metadata avoids parsing display strings in the operator API.
        self.artifact = artifact
        # Correlates an async ANALYSIS_RESULT/FAILED/TIMEOUT event back to its originating artifact.
        self.artifact_id = artifact_id
        self.analysis = analysis
        # Links a web event to the browser fingerprint collected for that session.
        self.fingerprint_hash = fingerprint_hash
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
                "artifact_id": self.artifact_id,
                "analysis": self.analysis,
                "artifact": self.artifact,
                "fingerprint_hash": self.fingerprint_hash,
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
            msg = _terminal_safe(record.getMessage())
            if self._color and record.levelno >= logging.WARNING:
                c = _LEVEL_COLORS.get(record.levelname, "")
                return f"{c}{record.levelname}{_RESET}  {msg}"
            return f"{record.levelname}  {msg}"

        c = _LEVEL_COLORS.get(ie.log_level, "") if self._color else ""
        reset = _RESET if self._color else ""
        dim = _DIM if self._color else ""

        ts = ie.timestamp[11:19]  # HH:MM:SS from ISO string
        parts = [f"{dim}{ts}{reset}", f"{dim}{ie.channel}{reset}"]
        # Background events (async analysis results) have no live peer to report.
        if ie.ip is not None:
            parts.append(f"{_terminal_safe(ie.ip)}:{_terminal_safe(ie.port)}")
            parts.append(f":{ie.local_port}")
        elif ie.session_id:
            parts.append(f"session={_terminal_safe(ie.session_id)}")
        parts.append(f"{c}{ie.request_type:<22}{reset}")
        if ie.query_level:
            parts.append(_terminal_safe(ie.query_level))
        if ie.matches is not None:
            parts.append(f"matches={ie.matches}")
        if ie.status:
            parts.append(f"{dim}->{reset} {ie.status}")
        if ie.artifact_id:
            parts.append(f"artifact={ie.artifact_id[:12]}")
        if ie.session_parameters:
            parts.append(
                "  ".join(_terminal_safe(p)[:60] for p in ie.session_parameters)
            )
        if ie.version:
            parts.append(f"{dim}[{_terminal_safe(ie.version)}]{reset}")
        return "  ".join(parts)


def _terminal_safe(value) -> str:
    """Escape terminal controls while leaving the durable JSON event untouched."""
    return "".join(
        character if character.isprintable() else repr(character)[1:-1]
        for character in str(value)
    )


def _safe_text_record(record: logging.LogRecord) -> logging.LogRecord:
    safe = copy.copy(record)
    safe.msg = _terminal_safe(record.getMessage())
    safe.args = ()
    return safe


class SafeTextFormatter(logging.Formatter):
    """Escape controls in a log message without flattening genuine traceback lines."""

    def format(self, record: logging.LogRecord) -> str:
        return super().format(_safe_text_record(record))


class LevelColorFormatter(SafeTextFormatter):
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

    # pynetdicom's own exception logging is redundant with our EVT_ABORTED handler; quiet by default, --dev-log-path opts back in.
    pn = logging.getLogger("pynetdicom")
    pn.setLevel(logging.CRITICAL)
    pn.propagate = False
    _remove_owned_handlers(pn)
    pn.addHandler(_owned(logging.NullHandler()))
    return lg


def worker_bus_config(logger: Logger) -> dict | None:
    """Serializable file/console settings for a spawned analysis worker."""
    handler = next(
        (
            candidate
            for candidate in logger.handlers
            if isinstance(candidate, logging.FileHandler)
        ),
        None,
    )
    if handler is None:
        return None
    verbose = any(
        isinstance(candidate, logging.StreamHandler)
        and not isinstance(candidate, logging.FileHandler)
        for candidate in logger.handlers
    )
    return {
        "stdout": handler.baseFilename,
        "when": getattr(handler, "_dicomhawk_when", None),
        "interval": getattr(handler, "_dicomhawk_interval", 1),
        "size": getattr(handler, "maxBytes", None) or None,
        "backups": getattr(handler, "backupCount", _DEFAULT_LOG_BACKUPS),
        "verbose": verbose,
    }


class _InnerFrameFormatter(SafeTextFormatter):
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

    # Keep negotiation detail without enabling full PDU debug logs.
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
        handler = MultiprocessTimedRotatingFileHandler(
            stdout, when=when, interval=interval, backupCount=backups, delay=True
        )
        handler._dicomhawk_when = when  # type: ignore[attr-defined]
        handler._dicomhawk_interval = interval  # type: ignore[attr-defined]
        return handler
    if size:
        return MultiprocessRotatingFileHandler(
            stdout, maxBytes=size, backupCount=backups, delay=True
        )
    return MultiprocessFileHandler(stdout, delay=True)


def _owned(handler: logging.Handler) -> logging.Handler:
    handler._dicomhawk_owned = True  # type: ignore[attr-defined]
    return handler


def _remove_owned_handlers(target: logging.Logger) -> None:
    for handler in list(target.handlers):
        if getattr(handler, "_dicomhawk_owned", False):
            target.removeHandler(handler)
            handler.close()
