import logging
from logging import Logger
from typing import Callable, Any, Generator, Optional

from pynetdicom import evt
from pynetdicom.pdu_primitives import A_ASSOCIATE, ImplementationVersionNameNotification
from pydicom.dataset import Dataset
from pynetdicom.events import Event, EventHandlerType, EventType

from .status import QRStatus
from .repository import Repository
from .bus import new_interaction, clear_session, cache_version, hash_request, _query_level, _extract_params

logger = logging.getLogger(__name__)

type EventHandler = Callable[
    [
        Repository,
        Logger,
        Event,
    ],
    Any,
]
type QRResult = Generator[tuple[int, Optional[Dataset]], None, None]

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

# Used by the Q/R binder to log eval_qr failures before the handler runs.
_EVENT_TO_REQUEST: dict[EventType, str] = {
    evt.EVT_C_FIND:    "C_FIND",
    evt.EVT_C_GET:     "C_GET",
    evt.EVT_C_MOVE:    "C_MOVE",
    evt.EVT_C_ECHO:    "C_ECHO",
    evt.EVT_C_STORE:   "C_STORE",
    evt.EVT_ACSE_RECV: "Association Requested",
    evt.EVT_RELEASED:  "Association Released",
    evt.EVT_ABORTED:   "Association Aborted",
}


# --- ACSE handlers (plain functions, no yield) ---

def handle_associate(repo: Repository, bus: Logger, event: Event) -> None:
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
                cache_version(event.assoc, v.strip())
            break
    bus.info(new_interaction(event, "Association Requested"))


def handle_release(repo: Repository, bus: Logger, event: Event) -> None:
    bus.info(new_interaction(event, "Association Released"))
    clear_session(event.assoc)


def handle_abort(repo: Repository, bus: Logger, event: Event) -> None:
    bus.info(new_interaction(event, "Association Aborted"))
    clear_session(event.assoc)


# --- DIMSE handlers (generators) ---

def handle_echo(repo: Repository, bus: Logger, event: Event) -> QRResult:
    bus.info(new_interaction(event, "C_ECHO"))
    yield (QRStatus.SUCCESS, None)


def handle_find(repo: Repository, bus: Logger, event: Event) -> QRResult:
    idt = event.identifier
    model = event.request.AffectedSOPClassUID
    ql = _query_level(idt)
    params = _extract_params(idt)

    result = repo.find(idt, model, inject=True)
    if (err := result.error) is not None:
        bus.info(new_interaction(event, "C_FIND", query_level=ql, session_parameters=params, matches=0, log_level="WARNING"))
        yield (err.status, None)
        return

    bus.info(new_interaction(event, "C_FIND", query_level=ql, session_parameters=params, matches=len(result.matches)))
    for m in result.matches:
        if event.is_cancelled:
            yield (QRStatus.CANCEL, None)
            return
        res = m.as_identifier(idt, model)
        res.RetrieveAETitle = event.assoc.ae.ae_title
        yield (QRStatus.PENDING, res)

    yield (QRStatus.SUCCESS, None)


def handle_get(repo: Repository, bus: Logger, event: Event) -> QRResult:
    try:
        idt = event.identifier
    except AttributeError as e:
        bus.info(new_interaction(event, "C_GET", session_parameters=[f"Error: {e}"], log_level="WARNING"))
        yield (QRStatus.FAILURE, None)
        return

    model = event.request.AffectedSOPClassUID
    ql = _query_level(idt)
    params = _extract_params(idt)

    result = repo.find(idt, model)
    if (err := result.error) is not None:
        bus.info(new_interaction(event, "C_GET", query_level=ql, session_parameters=params, matches=0, log_level="WARNING"))
        yield (err.status, None)
        return

    matches = len(result.matches)
    bus.info(new_interaction(event, "C_GET", query_level=ql, session_parameters=params, matches=matches))
    yield matches  # type: ignore

    for m in result.matches:
        if event.is_cancelled:
            yield (QRStatus.CANCEL, None)
            return
        res = repo.find_instance(m, decompress=True)
        if (err := res.error) is not None:
            yield (err.status, None)
        yield (QRStatus.PENDING, res.dataset)

    yield (QRStatus.SUCCESS, None)


def handle_move(repo: Repository, bus: Logger, event: Event) -> QRResult:
    destination = str(event.move_destination).strip()
    try:
        ql = _query_level(event.identifier)
    except AttributeError:
        ql = "N/A"
    bus.info(new_interaction(event, "C_MOVE", query_level=ql, session_parameters=[f"Destination: {destination}"]))
    yield (None, None)


def handle_store(repo: Repository, bus: Logger, event: Event) -> QRResult:
    try:
        file_hash = hash_request(event)
        ds = event.dataset
    except Exception as exc:
        bus.info(new_interaction(event, "C_STORE", session_parameters=[f"Error: {exc}"], log_level="WARNING"))
        yield (QRStatus.FAILURE, None)
        return

    if (err := repo.store(ds)) is not None:
        bus.info(new_interaction(event, "C_STORE", session_parameters=[
            f"SHA256: {file_hash}",
            f"Error: {err.error}",
        ], log_level="WARNING"))
        yield (err.status, None)
        return

    bus.info(new_interaction(event, "C_STORE", session_parameters=[
        f"SHA256: {file_hash}",
        f"SOPInstanceUID: {ds.SOPInstanceUID}",
    ]))
    yield (QRStatus.SUCCESS, None)


def bind(repo: Repository, bus: Logger, handler: EventHandler, event_type: EventType) -> Callable:
    _is_acse = event_type in _ACSE_EVENTS
    _eval_qr = event_type in _QR_EVENTS

    if _is_acse:
        # ACSE events: plain function, no status return, no yield.
        def binder(event: Event, *args):
            handler(repo, bus, event, *args)
        return binder

    elif _eval_qr:
        # C-FIND, C-GET, C-MOVE: pynetdicom drives these as iterators.
        # eval_qr failures are logged here before the handler gets a chance to run.
        _request_type = _EVENT_TO_REQUEST.get(event_type, "UNKNOWN")
        def binder(event: Event, *args):
            if err := repo.eval_qr(event):
                logger.error(err.error)
                try:
                    idt = event.identifier
                    ql = _query_level(idt)
                    params = _extract_params(idt)
                except Exception:
                    ql, params = "N/A", "N/A"
                bus.info(new_interaction(event, _request_type, query_level=ql, session_parameters=params, matches=0, log_level="WARNING"))
                yield (err.status, None)
                return
            yield from handler(repo, bus, event, *args)
        return binder

    else:
        # C-ECHO, C-STORE: pynetdicom expects a plain int status return, not an iterator.
        def binder(event: Event, *args):
            gen = handler(repo, bus, event, *args)
            try:
                status, _ = next(gen)
                return int(status)
            except StopIteration:
                return int(QRStatus.SUCCESS)
        return binder


_handlers: list[tuple[str, EventType, EventHandler]] = [
    ("associate", evt.EVT_ACSE_RECV, handle_associate),
    ("release",   evt.EVT_RELEASED,  handle_release),
    ("abort",     evt.EVT_ABORTED,   handle_abort),
    ("echo",      evt.EVT_C_ECHO,    handle_echo),
    ("get",       evt.EVT_C_GET,     handle_get),
    ("store",     evt.EVT_C_STORE,   handle_store),
    ("find",      evt.EVT_C_FIND,    handle_find),
    ("move",      evt.EVT_C_MOVE,    handle_move),
]


class DIMSEFactory:

    def __init__(self) -> None:
        self.handlers: dict[str, EventHandlerType] = {}

    def get(self, name: str) -> EventHandlerType | None:
        if handler := self.handlers.get(name):
            return handler

    def register(self, name: str, handler: EventHandlerType) -> 'DIMSEFactory':
        self.handlers[name] = handler
        return self


def new_dimse_factory(repo: Repository, bus: Logger) -> DIMSEFactory:
    factory = DIMSEFactory()
    for n, t, h in _handlers:
        handler = bind(repo, bus, h, t)
        factory.register(n, (t, handler))
    return factory
