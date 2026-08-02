import copy
import logging
from functools import partial
from logging import Logger
from typing import Callable, Any, Generator

from pynetdicom import evt
from pynetdicom.apps.qrscp import db as _qrdb
from pynetdicom.pdu_primitives import A_ASSOCIATE, ImplementationVersionNameNotification
from pydicom.dataset import Dataset
from pynetdicom.events import Event, EventHandlerType, EventType

from .status import QRStatus
from .repository import Repository, INDEX_REQUIRED_KEYS
from .storage import ArtifactSink, SubmittedArtifact
from .bus import (
    InteractionEvent,
    SessionCache,
    _query_level,
    _extract_params,
)

logger = logging.getLogger(__name__)

type EventHandler = Callable[
    [
        Repository,  # instance of the repo, to store and retrieve
        Logger,  # a bus to push logs
        SessionCache,  # session/version cache for this server instance
        Event,  # the event
    ],
    Any,
]
type QRResult = Generator[tuple[int, Dataset | None], None, None]

# NOTE: private pynetdicom internals — AttributeError guard degrades to no-op rather than crash.
try:
    _QR_MODEL_ATTRS: dict = {**_qrdb._PATIENT_ROOT, **_qrdb._STUDY_ROOT}
except AttributeError:
    _QR_MODEL_ATTRS: dict = {}

_QR_EVENTS: frozenset[EventType] = frozenset(
    {
        evt.EVT_C_FIND,
        evt.EVT_C_GET,
        evt.EVT_C_MOVE,
    }
)

_ACSE_EVENTS: frozenset[EventType] = frozenset(
    {
        evt.EVT_ACSE_RECV,
        evt.EVT_ACSE_SENT,
        evt.EVT_RELEASED,
        evt.EVT_ABORTED,
    }
)

# C-FIND deduplication column; IMAGE remains one response per instance.
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
        kw for lvl in levels[levels.index(ql) + 1 :] for kw in attr[lvl]
    )
    stripped = [kw for kw in sublevel_tags if kw in ds]
    if not stripped:
        return ds, []
    filtered = copy.deepcopy(ds)
    for kw in stripped:
        delattr(filtered, kw)
    return filtered, stripped


# --- ACSE handlers (plain functions, no yield) ---


def handle_associate(
    repo: Repository, bus: Logger, cache: SessionCache, event: Event
) -> None:
    # EVT_ACSE_RECV also fires for release and abort PDUs.
    if not isinstance(event.primitive, A_ASSOCIATE):
        return
    if _mark_healthcheck(event, event.primitive):
        return
    # Negotiation overwrites this primitive before DIMSE handlers run.
    for item in event.primitive.user_information:
        if isinstance(item, ImplementationVersionNameNotification):
            v = item.implementation_version_name
            if v:
                cache.cache_version(event.assoc, v.strip())
            break
    # Logged before AE-title auth can reject the association — only place these are seen.
    params = [
        f"Called: {event.primitive.called_ae_title}",
        f"Calling: {event.primitive.calling_ae_title}",
    ]
    bus.info(
        InteractionEvent(
            event, cache, "Association Requested", session_parameters=params
        )
    )


def handle_release(
    repo: Repository, bus: Logger, cache: SessionCache, event: Event
) -> None:
    if _is_healthcheck(event):
        cache.clear(event.assoc)
        return
    bus.info(InteractionEvent(event, cache, "Association Released"))
    cache.clear(event.assoc)


def handle_abort(
    repo: Repository, bus: Logger, cache: SessionCache, event: Event
) -> None:
    if _is_healthcheck(event):
        cache.clear(event.assoc)
        return
    bus.warning(
        InteractionEvent(event, cache, "Association Aborted", log_level="WARNING")
    )
    cache.clear(event.assoc)


def handle_reject(
    repo: Repository, bus: Logger, cache: SessionCache, event: Event
) -> None:
    # EVT_ACSE_SENT covers all ACSE PDUs; only 0x01/0x02 are rejections.
    prim = event.primitive
    if not isinstance(prim, A_ASSOCIATE) or prim.result not in (0x01, 0x02):
        return
    if _is_healthcheck(event):
        cache.clear(event.assoc)
        return
    params = [
        f"Result: {prim.result_str}",
        f"Source: {prim.result_source}",
        f"Reason: {prim.diagnostic}",
    ]
    bus.warning(
        InteractionEvent(
            event,
            cache,
            "Association Rejected",
            session_parameters=params,
            log_level="WARNING",
        )
    )
    cache.clear(event.assoc)


_LOOPBACK: frozenset[str] = frozenset({"127.0.0.1", "::1"})
_HEALTHCHECK_VERSION = "DICOMHAWK_HC"
_HEALTHCHECK_ATTR = "_dicomhawk_healthcheck"


def _mark_healthcheck(event: Event, primitive: A_ASSOCIATE) -> bool:
    """Mark the loopback probe without depending on profile AE-title policy."""
    requestor = getattr(getattr(event, "assoc", None), "requestor", None)
    if getattr(requestor, "address", None) not in _LOOPBACK:
        return False
    for item in primitive.user_information:
        if (
            isinstance(item, ImplementationVersionNameNotification)
            and str(item.implementation_version_name or "").strip()
            == _HEALTHCHECK_VERSION
        ):
            setattr(event.assoc, _HEALTHCHECK_ATTR, True)
            return True
    return False


def _is_healthcheck(event: Event) -> bool:
    return bool(getattr(getattr(event, "assoc", None), _HEALTHCHECK_ATTR, False))


def handle_connect(
    repo: Repository, bus: Logger, cache: SessionCache, event: Event
) -> None:
    # Fires on TCP accept — captures probes that never send a valid A-ASSOCIATE-RQ & skips loopback probes.
    addr = getattr(event, "address", None)
    if addr and addr[0] in _LOOPBACK:
        return
    bus.info(InteractionEvent(event, cache, "Connection Opened"))


# --- DIMSE handlers (generators) ---


def handle_echo(
    repo: Repository, bus: Logger, cache: SessionCache, event: Event
) -> QRResult:
    # Always respond; only the healthcheck's own loopback C-ECHO is kept out of the intel log.
    if not _is_healthcheck(event):
        bus.info(InteractionEvent(event, cache, "C-ECHO", status=QRStatus.SUCCESS))
    yield (QRStatus.SUCCESS, None)


def handle_find(
    repo: Repository, bus: Logger, cache: SessionCache, event: Event
) -> QRResult:
    if err := repo.eval_qr(event):
        bus.error(
            InteractionEvent(
                event, cache, "C-FIND", matches=0, status=err.status, log_level="ERROR"
            )
        )
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
        bus.error(
            InteractionEvent(
                event,
                cache,
                "C-FIND",
                query_level=ql,
                session_parameters=params,
                matches=0,
                status=err.status,
                log_level="ERROR",
            )
        )
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

    bus.info(
        InteractionEvent(
            event,
            cache,
            "C-FIND",
            query_level=ql,
            session_parameters=params,
            matches=len(matches),
            status=QRStatus.SUCCESS,
        )
    )
    for m in matches:
        if event.is_cancelled:
            yield (QRStatus.CANCEL, None)
            return
        res = m.as_identifier(idt, model)
        res.RetrieveAETitle = event.assoc.ae.ae_title
        yield (QRStatus.PENDING, res)

    yield (QRStatus.SUCCESS, None)


def handle_get(
    repo: Repository, bus: Logger, cache: SessionCache, event: Event
) -> QRResult:
    if err := repo.eval_qr(event):
        bus.error(
            InteractionEvent(
                event, cache, "C-GET", matches=0, status=err.status, log_level="ERROR"
            )
        )
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
        bus.error(
            InteractionEvent(
                event,
                cache,
                "C-GET",
                query_level=ql,
                session_parameters=params,
                matches=0,
                status=err.status,
                log_level="ERROR",
            )
        )
        yield (err.status, None)
        return

    matches = len(result.matches)
    bus.info(
        InteractionEvent(
            event,
            cache,
            "C-GET",
            query_level=ql,
            session_parameters=params,
            matches=matches,
            status=QRStatus.SUCCESS,
        )
    )
    yield matches  # type: ignore

    for m in result.matches:
        if event.is_cancelled:
            yield (QRStatus.CANCEL, None)
            return
        res = repo.find_instance(m, decompress=True)
        if (err := res.error) is not None:
            bus.error(
                InteractionEvent(
                    event,
                    cache,
                    "C-GET",
                    session_parameters=[err.error],
                    status=err.status,
                    log_level="ERROR",
                )
            )
            yield (err.status, None)
            continue
        yield (QRStatus.PENDING, res.dataset)
        # C-GET completes when yielded sub-operations reach the declared count.


def handle_move(
    repo: Repository, bus: Logger, cache: SessionCache, event: Event
) -> QRResult:
    if err := repo.eval_qr(event, missing_level_status=QRStatus.INVALID_REQUEST):
        bus.error(
            InteractionEvent(
                event, cache, "C-MOVE", matches=0, status=err.status, log_level="ERROR"
            )
        )
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
    bus.warning(
        InteractionEvent(
            event,
            cache,
            "C-MOVE",
            query_level=ql,
            session_parameters=params,
            matches=haul,
            status=QRStatus.MOVE_DESTINATION_UNKNOWN,
            log_level="WARNING",
        )
    )
    yield (None, None)


def _submit_artifact(
    sink: ArtifactSink,
    event: Event,
    cache: SessionCache,
    capture,
    *,
    request_type: str,
    disposition: str,
    sop_class_uid: str | None = None,
    sop_instance_uid: str | None = None,
) -> None:
    assoc = event.assoc
    addr = getattr(event, "address", None)
    ip, _port = (
        (addr[0], addr[1])
        if addr is not None
        else (assoc.requestor.address, assoc.requestor.port)
    )
    context = getattr(event, "context", None)
    try:
        sink(
            SubmittedArtifact(
                capture,
                channel="DIMSE",
                request_type=request_type,
                disposition=disposition,
                source_encoding="dimse-dataset",
                session_id=cache.get_session_id(assoc),
                ip=ip,
                local_port=getattr(assoc.acceptor, "port", None),
                sop_class_uid=sop_class_uid,
                sop_instance_uid=sop_instance_uid,
                # The association already negotiated this — pass it through instead of guessing.
                transfer_syntax_uid=str(context.transfer_syntax) if context else None,
            )
        )
    except Exception:
        # Analysis must never change what the peer sees; the payload is already captured.
        logger.exception("Artifact sink failed for %s", capture.artifact_id)


def handle_store(
    repo: Repository,
    bus: Logger,
    cache: SessionCache,
    event: Event,
    *,
    max_store_bytes: int | None = None,
    sink: ArtifactSink | None = None,
) -> QRResult:
    try:
        raw = event.request.DataSet
        position = raw.tell()
        raw.seek(0, 2)
        request_bytes = raw.tell()
        raw.seek(position)
    except Exception as exc:
        bus.error(
            InteractionEvent(
                event,
                cache,
                "C-STORE",
                session_parameters=[f"Size check error: {exc}"],
                status=QRStatus.FAILURE,
                log_level="ERROR",
                artifact={
                    "filename": None,
                    "bytes": None,
                    "sha256": None,
                    "artifact_id": None,
                    "sop_instance_uid": None,
                    "sop_class_uid": None,
                    "captured": False,
                    "disposition": "rejected",
                    "reject_reason": f"Size check error: {exc}",
                },
            )
        )
        yield (QRStatus.FAILURE, None)
        return
    if max_store_bytes is not None and request_bytes > max_store_bytes:
        bus.error(
            InteractionEvent(
                event,
                cache,
                "C-STORE",
                session_parameters=[f"Rejected size: {request_bytes} bytes"],
                status=QRStatus.STORE_ERROR,
                log_level="ERROR",
                artifact={
                    "filename": None,
                    "bytes": request_bytes,
                    "sha256": None,
                    "artifact_id": None,
                    "sop_instance_uid": None,
                    "sop_class_uid": str(event.request.AffectedSOPClassUID),
                    "captured": False,
                    "disposition": "rejected",
                    "reject_reason": "Configured size limit exceeded",
                },
            )
        )
        yield (QRStatus.STORE_ERROR, None)
        return
    try:
        capture = repo.storage.capture_fileobj(raw)
    except Exception as exc:
        bus.error(
            InteractionEvent(
                event,
                cache,
                "C-STORE",
                session_parameters=[f"Capture failure: {exc}"],
                status=QRStatus.STORE_ERROR,
                log_level="ERROR",
                artifact={
                    "filename": None,
                    "bytes": request_bytes,
                    "sha256": None,
                    "artifact_id": None,
                    "sop_instance_uid": None,
                    "sop_class_uid": str(event.request.AffectedSOPClassUID),
                    "captured": False,
                    "disposition": "rejected",
                    "reject_reason": f"Failed to quarantine incoming payload: {exc}",
                },
            )
        )
        yield (QRStatus.STORE_ERROR, None)
        return

    try:
        ds = event.dataset
    except Exception as exc:
        bus.error(
            InteractionEvent(
                event,
                cache,
                "C-STORE",
                session_parameters=[f"Dataset error: {exc}"],
                status=QRStatus.FAILURE,
                log_level="ERROR",
                artifact={
                    "filename": capture.path.name,
                    "bytes": request_bytes,
                    "sha256": capture.sha256,
                    "artifact_id": capture.artifact_id,
                    "sop_instance_uid": None,
                    "sop_class_uid": str(event.request.AffectedSOPClassUID),
                    "captured": True,
                    "disposition": "rejected",
                    "reject_reason": f"Dataset error: {exc}",
                },
            )
        )
        if sink is not None:
            _submit_artifact(
                sink,
                event,
                cache,
                capture,
                request_type="C-STORE",
                disposition="rejected",
                sop_class_uid=str(event.request.AffectedSOPClassUID),
            )
        yield (QRStatus.FAILURE, None)
        return

    if (err := repo.store(ds, capture=False)) is not None:
        bus.error(
            InteractionEvent(
                event,
                cache,
                "C-STORE",
                session_parameters=[
                    f"SHA256: {capture.sha256}",
                    f"Error: {err.error}",
                ],
                status=err.status,
                log_level="ERROR",
                artifact={
                    "filename": capture.path.name,
                    "bytes": request_bytes,
                    "sha256": capture.sha256,
                    "artifact_id": capture.artifact_id,
                    "sop_instance_uid": str(getattr(ds, "SOPInstanceUID", "")) or None,
                    "sop_class_uid": str(getattr(ds, "SOPClassUID", ""))
                    or str(event.request.AffectedSOPClassUID),
                    "captured": not err.error.startswith(
                        "Failed to quarantine incoming payload"
                    ),
                    "disposition": "rejected",
                    "reject_reason": err.error,
                },
            )
        )
        if sink is not None:
            _submit_artifact(
                sink,
                event,
                cache,
                capture,
                request_type="C-STORE",
                disposition="rejected",
                sop_class_uid=str(getattr(ds, "SOPClassUID", ""))
                or str(event.request.AffectedSOPClassUID),
                sop_instance_uid=str(getattr(ds, "SOPInstanceUID", "")) or None,
            )
        yield (err.status, None)
        return

    params = [
        f"SHA256: {capture.sha256}",
        f"SOPInstanceUID: {ds.SOPInstanceUID}",
    ]
    # Missing identity keys: quarantined but unindexed — surface it, it's a signal.
    missing = [kw for kw in INDEX_REQUIRED_KEYS if kw not in ds]
    if missing:
        params.append(f"Not indexed (missing {', '.join(missing)})")
        bus.warning(
            InteractionEvent(
                event,
                cache,
                "C-STORE",
                session_parameters=params,
                status=QRStatus.SUCCESS,
                log_level="WARNING",
                artifact={
                    "filename": capture.path.name,
                    "bytes": request_bytes,
                    "sha256": capture.sha256,
                    "artifact_id": capture.artifact_id,
                    "sop_instance_uid": str(ds.SOPInstanceUID),
                    "sop_class_uid": str(getattr(ds, "SOPClassUID", ""))
                    or str(event.request.AffectedSOPClassUID),
                    "captured": True,
                    "disposition": "stored-unindexed",
                    "reject_reason": None,
                },
            )
        )
    else:
        bus.info(
            InteractionEvent(
                event,
                cache,
                "C-STORE",
                session_parameters=params,
                status=QRStatus.SUCCESS,
                artifact={
                    "filename": capture.path.name,
                    "bytes": request_bytes,
                    "sha256": capture.sha256,
                    "artifact_id": capture.artifact_id,
                    "sop_instance_uid": str(ds.SOPInstanceUID),
                    "sop_class_uid": str(getattr(ds, "SOPClassUID", ""))
                    or str(event.request.AffectedSOPClassUID),
                    "captured": True,
                    "disposition": "stored",
                    "reject_reason": None,
                },
            )
        )
    if sink is not None:
        _submit_artifact(
            sink,
            event,
            cache,
            capture,
            request_type="C-STORE",
            disposition="stored-unindexed" if missing else "stored",
            sop_class_uid=str(getattr(ds, "SOPClassUID", ""))
            or str(event.request.AffectedSOPClassUID),
            sop_instance_uid=str(ds.SOPInstanceUID),
        )
    yield (QRStatus.SUCCESS, None)


# Adapt internal handlers to pynetdicom's callback signatures.


def bind_acse(
    handler: EventHandler, repo: Repository, bus: Logger, cache: SessionCache
) -> Callable:
    def binder(event: Event, *args):
        handler(repo, bus, cache, event, *args)

    return binder


def bind_dimse_qr(
    handler: EventHandler, repo: Repository, bus: Logger, cache: SessionCache
) -> Callable:
    def binder(event: Event, *args):
        yield from handler(repo, bus, cache, event, *args)

    return binder


def bind_dimse_simple(
    handler: EventHandler, repo: Repository, bus: Logger, cache: SessionCache
) -> Callable:
    def binder(event: Event, *args):
        gen = handler(repo, bus, cache, event, *args)
        try:
            status, _ = next(gen)
            return int(status)
        except StopIteration:
            return int(QRStatus.SUCCESS)

    return binder


_handlers: list[tuple[str, EventType, EventHandler]] = [
    ("connect", evt.EVT_CONN_OPEN, handle_connect),
    ("associate", evt.EVT_ACSE_RECV, handle_associate),
    ("reject", evt.EVT_ACSE_SENT, handle_reject),
    ("release", evt.EVT_RELEASED, handle_release),
    ("abort", evt.EVT_ABORTED, handle_abort),
    ("echo", evt.EVT_C_ECHO, handle_echo),
    ("get", evt.EVT_C_GET, handle_get),
    ("store", evt.EVT_C_STORE, handle_store),
    ("find", evt.EVT_C_FIND, handle_find),
    ("move", evt.EVT_C_MOVE, handle_move),
]


def new_dimse_factory(
    repo: Repository,
    bus: Logger,
    max_store_bytes: int | None = None,
    sink: ArtifactSink | None = None,
) -> dict[str, EventHandlerType]:
    """Map handler name -> (event type, pynetdicom callback). One SessionCache shared by all."""
    cache = SessionCache()
    handlers: dict[str, EventHandlerType] = {}
    for n, t, h in _handlers:
        if n == "store":
            h = partial(h, max_store_bytes=max_store_bytes, sink=sink)
        if t in _ACSE_EVENTS or t == evt.EVT_CONN_OPEN:
            binder = bind_acse(h, repo, bus, cache)
        elif t in _QR_EVENTS:
            binder = bind_dimse_qr(h, repo, bus, cache)
        else:
            binder = bind_dimse_simple(h, repo, bus, cache)
        handlers[n] = (t, binder)
    return handlers
