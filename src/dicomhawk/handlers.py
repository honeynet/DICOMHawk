import copy
import logging
from logging import Logger
from typing import Callable, Any, Generator

from pynetdicom import evt
from pynetdicom.apps.qrscp import db as _qrdb
from pynetdicom.pdu_primitives import A_ASSOCIATE, ImplementationVersionNameNotification
from pydicom.dataset import Dataset
from pynetdicom.events import Event, EventHandlerType, EventType

from .status import QRStatus
from .repository import Repository, INDEX_REQUIRED_KEYS
from .bus import InteractionEvent, SessionCache, hash_request, _query_level, _extract_params

logger = logging.getLogger(__name__)

type EventHandler = Callable[
    [
        Repository,   # instance of the repo, to store and retrieve
        Logger,       # a bus to push logs
        SessionCache, # session/version cache for this server instance
        Event,        # the event
    ],
    Any,
]
type QRResult = Generator[tuple[int, Dataset | None], None, None]

# NOTE: private pynetdicom internals — AttributeError guard degrades to no-op rather than crash.
try:
    _QR_MODEL_ATTRS: dict = {**_qrdb._PATIENT_ROOT, **_qrdb._STUDY_ROOT}
except AttributeError:
    _QR_MODEL_ATTRS: dict = {}

_QR_EVENTS: frozenset[EventType] = frozenset({
    evt.EVT_C_FIND,
    evt.EVT_C_GET,
    evt.EVT_C_MOVE,
})

_ACSE_EVENTS: frozenset[EventType] = frozenset({
    evt.EVT_ACSE_RECV,
    evt.EVT_RELEASED,
    evt.EVT_ABORTED,
})

# Level -> Instance column for collapsing C-FIND to one match per entity.
# IMAGE absent: one response per instance is correct there.
_FIND_LEVEL_UID: dict[str, str] = {
    "PATIENT": "patient_id",
    "STUDY": "study_instance_uid",
    "SERIES": "series_instance_uid",
}


def _strip_sublevel_tags(ds: Dataset, model) -> tuple[Dataset, list[str]]:
    """Remove tags belonging to levels below QueryRetrieveLevel; return (filtered_ds, stripped)."""
    attr = _QR_MODEL_ATTRS.get(model)
    if attr is None:
        return ds, []
    ql = getattr(ds, "QueryRetrieveLevel", None)
    if ql not in attr:
        return ds, []
    levels = list(attr.keys())
    sublevel_tags: frozenset[str] = frozenset(
        kw for lvl in levels[levels.index(ql) + 1:] for kw in attr[lvl]
    )
    stripped = [kw for kw in sublevel_tags if kw in ds]
    if not stripped:
        return ds, []
    filtered = copy.deepcopy(ds)
    for kw in stripped:
        delattr(filtered, kw)
    return filtered, stripped


# --- ACSE handlers (plain functions, no yield) ---

def handle_associate(repo: Repository, bus: Logger, cache: SessionCache, event: Event) -> None:
    # EVT_ACSE_RECV fires for every received ACSE PDU (A-RELEASE-RQ, A-ABORT, etc.).
    # Only log "Association Requested" for the initial A-ASSOCIATE-RQ.
    if not isinstance(event.primitive, A_ASSOCIATE):
        return
    # Cache version now: assoc.requestor.primitive is overwritten during
    # negotiate_association() so the peer's version would be lost by the time
    # any DIMSE handler runs.
    for item in event.primitive.user_information:
        if isinstance(item, ImplementationVersionNameNotification):
            v = item.implementation_version_name
            if v:
                cache.cache_version(event.assoc, v.strip())
            break
    bus.info(InteractionEvent(event, cache, "Association Requested"))


def handle_release(repo: Repository, bus: Logger, cache: SessionCache, event: Event) -> None:
    bus.info(InteractionEvent(event, cache, "Association Released"))
    cache.clear(event.assoc)


def handle_abort(repo: Repository, bus: Logger, cache: SessionCache, event: Event) -> None:
    bus.warning(InteractionEvent(event, cache, "Association Aborted", log_level="WARNING"))
    cache.clear(event.assoc)


_LOOPBACK: frozenset[str] = frozenset({"127.0.0.1", "::1"})


def handle_connect(repo: Repository, bus: Logger, cache: SessionCache, event: Event) -> None:
    # Fires on TCP accept — captures probes that never send a valid A-ASSOCIATE-RQ & skips loopback probes.
    addr = getattr(event, "address", None)
    if addr and addr[0] in _LOOPBACK:
        return
    bus.info(InteractionEvent(event, cache, "Connection Opened"))


# --- DIMSE handlers (generators) ---

def handle_echo(repo: Repository, bus: Logger, cache: SessionCache, event: Event) -> QRResult:
    bus.info(InteractionEvent(event, cache, "C-ECHO", status=QRStatus.SUCCESS))
    yield (QRStatus.SUCCESS, None)


def handle_find(repo: Repository, bus: Logger, cache: SessionCache, event: Event) -> QRResult:
    if err := repo.eval_qr(event):
        bus.error(InteractionEvent(event, cache, "C-FIND", matches=0, status=err.status, log_level="ERROR"))
        yield (err.status, None)
        return

    model = event.request.AffectedSOPClassUID
    idt, stripped = _strip_sublevel_tags(event.identifier, model)
    ql = _query_level(idt)
    params = _extract_params(idt)
    if stripped:
        params = (params or []) + [f"Stripped: {', '.join(stripped)}"]

    result = repo.find(idt, model, inject=True)
    if (err := result.error) is not None:
        bus.error(InteractionEvent(event, cache, "C-FIND", query_level=ql, session_parameters=params, matches=0, status=err.status, log_level="ERROR"))
        yield (err.status, None)
        return

    matches = result.matches
    # One match per entity at the query level (IMAGE stays per-instance).
    if dedup_attr := _FIND_LEVEL_UID.get((ql or "").upper()):
        seen = set()
        deduped = []
        for m in matches:
            uid = getattr(m, dedup_attr, None)
            if uid not in seen:
                seen.add(uid)
                deduped.append(m)
        matches = deduped

    bus.info(InteractionEvent(event, cache, "C-FIND", query_level=ql, session_parameters=params, matches=len(matches), status=QRStatus.SUCCESS))
    for m in matches:
        if event.is_cancelled:
            yield (QRStatus.CANCEL, None)
            return
        res = m.as_identifier(idt, model)
        res.RetrieveAETitle = event.assoc.ae.ae_title
        yield (QRStatus.PENDING, res)

    yield (QRStatus.SUCCESS, None)


def handle_get(repo: Repository, bus: Logger, cache: SessionCache, event: Event) -> QRResult:
    if err := repo.eval_qr(event):
        bus.error(InteractionEvent(event, cache, "C-GET", matches=0, status=err.status, log_level="ERROR"))
        yield (err.status, None)
        return

    model = event.request.AffectedSOPClassUID
    idt, stripped = _strip_sublevel_tags(event.identifier, model)
    ql = _query_level(idt)
    params = _extract_params(idt)
    if stripped:
        params = (params or []) + [f"Stripped: {', '.join(stripped)}"]

    result = repo.find(idt, model)
    if (err := result.error) is not None:
        bus.error(InteractionEvent(event, cache, "C-GET", query_level=ql, session_parameters=params, matches=0, status=err.status, log_level="ERROR"))
        yield (err.status, None)
        return

    matches = len(result.matches)
    bus.info(InteractionEvent(event, cache, "C-GET", query_level=ql, session_parameters=params, matches=matches, status=QRStatus.SUCCESS))
    yield matches  # type: ignore

    for m in result.matches:
        if event.is_cancelled:
            yield (QRStatus.CANCEL, None)
            return
        res = repo.find_instance(m, decompress=True)
        if (err := res.error) is not None:
            bus.error(InteractionEvent(event, cache, "C-GET", session_parameters=[err.error], status=err.status, log_level="ERROR"))
            yield (err.status, None)
            continue
        yield (QRStatus.PENDING, res.dataset)
        # No trailing (SUCCESS, None): C-GET is count-based — it completes when the yielded
        # sub-operations match the count, and an extra yield only logs a warning.


def handle_move(repo: Repository, bus: Logger, cache: SessionCache, event: Event) -> QRResult:
    if err := repo.eval_qr(event):
        bus.error(InteractionEvent(event, cache, "C-MOVE", matches=0, status=err.status, log_level="ERROR"))
        yield (err.status, None)
        return

    destination = str(event.move_destination).strip()
    model = event.request.AffectedSOPClassUID
    idt, stripped = _strip_sublevel_tags(event.identifier, model)
    ql = _query_level(idt)
    params: list[str] = [f"Destination: {destination}"]
    if stripped:
        params.append(f"Stripped: {', '.join(stripped)}")

    # Capture & reject: log the intended haul; pynetdicom auto-responds 0xA801 to (None, None).
    result = repo.find(idt, model)
    haul = len(result.matches) if result.error is None else 0
    bus.warning(InteractionEvent(
        event, cache, "C-MOVE", query_level=ql, session_parameters=params, matches=haul,
        status=QRStatus.MOVE_DESTINATION_UNKNOWN, log_level="WARNING",
    ))
    yield (None, None)


def handle_store(repo: Repository, bus: Logger, cache: SessionCache, event: Event) -> QRResult:
    try:
        file_hash = hash_request(event)
    except Exception as exc:
        bus.error(InteractionEvent(event, cache, "C-STORE", session_parameters=[f"Hash error: {exc}"], status=QRStatus.FAILURE, log_level="ERROR"))
        yield (QRStatus.FAILURE, None)
        return

    try:
        ds = event.dataset
    except Exception as exc:
        bus.error(InteractionEvent(event, cache, "C-STORE", session_parameters=[f"Dataset error: {exc}"], status=QRStatus.FAILURE, log_level="ERROR"))
        yield (QRStatus.FAILURE, None)
        return

    if (err := repo.store(ds)) is not None:
        bus.error(InteractionEvent(event, cache, "C-STORE", session_parameters=[
            f"SHA256: {file_hash}",
            f"Error: {err.error}",
        ], status=err.status, log_level="ERROR"))
        yield (err.status, None)
        return

    params = [
        f"SHA256: {file_hash}",
        f"SOPInstanceUID: {ds.SOPInstanceUID}",
    ]
    # Missing identity keys: quarantined but unindexed — surface it, it's a signal.
    missing = [kw for kw in INDEX_REQUIRED_KEYS if kw not in ds]
    if missing:
        params.append(f"Not indexed (missing {', '.join(missing)})")
        bus.warning(InteractionEvent(event, cache, "C-STORE", session_parameters=params, status=QRStatus.SUCCESS, log_level="WARNING"))
    else:
        bus.info(InteractionEvent(event, cache, "C-STORE", session_parameters=params, status=QRStatus.SUCCESS))
    yield (QRStatus.SUCCESS, None)


# The binders below adapt our (repo, bus, cache, event) handlers to pynetdicom's
# (event, *args) callback signature, splitting on how pynetdicom consumes the result:
# ACSE callbacks return nothing, QR callbacks are generators, simple DIMSE return a status int.

def bind_acse(handler: EventHandler, repo: Repository, bus: Logger, cache: SessionCache) -> Callable:
    def binder(event: Event, *args):
        handler(repo, bus, cache, event, *args)
    return binder


def bind_dimse_qr(handler: EventHandler, repo: Repository, bus: Logger, cache: SessionCache) -> Callable:
    def binder(event: Event, *args):
        yield from handler(repo, bus, cache, event, *args)
    return binder


def bind_dimse_simple(handler: EventHandler, repo: Repository, bus: Logger, cache: SessionCache) -> Callable:
    def binder(event: Event, *args):
        gen = handler(repo, bus, cache, event, *args)
        try:
            status, _ = next(gen)
            return int(status)
        except StopIteration:
            return int(QRStatus.SUCCESS)
    return binder


_handlers: list[tuple[str, EventType, EventHandler]] = [
    ("connect",   evt.EVT_CONN_OPEN, handle_connect),
    ("associate", evt.EVT_ACSE_RECV, handle_associate),
    ("release",   evt.EVT_RELEASED,  handle_release),
    ("abort",     evt.EVT_ABORTED,   handle_abort),
    ("echo",      evt.EVT_C_ECHO,    handle_echo),
    ("get",       evt.EVT_C_GET,     handle_get),
    ("store",     evt.EVT_C_STORE,   handle_store),
    ("find",      evt.EVT_C_FIND,    handle_find),
    ("move",      evt.EVT_C_MOVE,    handle_move),
]

def new_dimse_factory(repo: Repository, bus: Logger) -> dict[str, EventHandlerType]:
    """Map handler name -> (event type, pynetdicom callback). One SessionCache shared by all."""
    cache = SessionCache()
    handlers: dict[str, EventHandlerType] = {}
    for n, t, h in _handlers:
        if t in _ACSE_EVENTS or t == evt.EVT_CONN_OPEN:
            binder = bind_acse(h, repo, bus, cache)
        elif t in _QR_EVENTS:
            binder = bind_dimse_qr(h, repo, bus, cache)
        else:
            binder = bind_dimse_simple(h, repo, bus, cache)
        handlers[n] = (t, binder)
    return handlers
