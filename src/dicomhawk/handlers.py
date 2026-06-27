import logging
from logging import Logger
from typing import Callable, Any, Generator, Optional

from pynetdicom import evt
from pynetdicom.pdu_primitives import A_ASSOCIATE, ImplementationVersionNameNotification
from pydicom.dataset import Dataset
from pynetdicom.events import Event, EventHandlerType, EventType

from .status import QRStatus
from .repository import Repository
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


# --- DIMSE handlers (generators) ---

def handle_echo(repo: Repository, bus: Logger, cache: SessionCache, event: Event) -> QRResult:
    bus.info(InteractionEvent(event, cache, "C-ECHO"))
    yield (QRStatus.SUCCESS, None)


def handle_find(repo: Repository, bus: Logger, cache: SessionCache, event: Event) -> QRResult:
    if err := repo.eval_qr(event):
        bus.error(InteractionEvent(event, cache, "C-FIND", matches=0, log_level="ERROR"))
        yield (err.status, None)
        return

    idt = event.identifier
    model = event.request.AffectedSOPClassUID
    ql = _query_level(idt)
    params = _extract_params(idt)

    result = repo.find(idt, model, inject=True)
    if (err := result.error) is not None:
        bus.error(InteractionEvent(event, cache, "C-FIND", query_level=ql, session_parameters=params, matches=0, log_level="ERROR"))
        yield (err.status, None)
        return

    bus.info(InteractionEvent(event, cache, "C-FIND", query_level=ql, session_parameters=params, matches=len(result.matches)))
    for m in result.matches:
        if event.is_cancelled:
            yield (QRStatus.CANCEL, None)
            return
        res = m.as_identifier(idt, model)
        res.RetrieveAETitle = event.assoc.ae.ae_title
        yield (QRStatus.PENDING, res)

    yield (QRStatus.SUCCESS, None)


def handle_get(repo: Repository, bus: Logger, cache: SessionCache, event: Event) -> QRResult:
    if err := repo.eval_qr(event):
        bus.error(InteractionEvent(event, cache, "C-GET", matches=0, log_level="ERROR"))
        yield (err.status, None)
        return

    try:
        idt = event.identifier
    except AttributeError as e:
        bus.error(InteractionEvent(event, cache, "C-GET", session_parameters=[f"Error: {e}"], log_level="ERROR"))
        yield (QRStatus.FAILURE, None)
        return

    model = event.request.AffectedSOPClassUID
    ql = _query_level(idt)
    params = _extract_params(idt)

    result = repo.find(idt, model)
    if (err := result.error) is not None:
        bus.error(InteractionEvent(event, cache, "C-GET", query_level=ql, session_parameters=params, matches=0, log_level="ERROR"))
        yield (err.status, None)
        return

    matches = len(result.matches)
    bus.info(InteractionEvent(event, cache, "C-GET", query_level=ql, session_parameters=params, matches=matches))
    yield matches  # type: ignore

    for m in result.matches:
        if event.is_cancelled:
            yield (QRStatus.CANCEL, None)
            return
        res = repo.find_instance(m, decompress=True)
        if (err := res.error) is not None:
            bus.error(InteractionEvent(event, cache, "C-GET", session_parameters=[err.error], log_level="ERROR"))
            yield (err.status, None)
        yield (QRStatus.PENDING, res.dataset)

    yield (QRStatus.SUCCESS, None)


def handle_move(repo: Repository, bus: Logger, cache: SessionCache, event: Event) -> QRResult:
    if err := repo.eval_qr(event):
        bus.error(InteractionEvent(event, cache, "C-MOVE", matches=0, log_level="ERROR"))
        yield (err.status, None)
        return

    destination = str(event.move_destination).strip()
    ql = _query_level(event.identifier)

    # TODO: need destinations
    # try:
    #     addr, port = destinations[event.move_destination]
    # except KeyError:
    #     logger.info("No matching move destination in the configuration")
    #     yield None, None
    #     return
    # contexts = list(set([ii.context for ii in matches]))
    # yield addr, port, {"contexts": contexts[:128]}

    bus.warning(InteractionEvent(event, cache, "C-MOVE", query_level=ql, session_parameters=[f"Destination: {destination}"], log_level="WARNING"))
    yield (None, None)


def handle_store(repo: Repository, bus: Logger, cache: SessionCache, event: Event) -> QRResult:
    try:
        file_hash = hash_request(event)
    except Exception as exc:
        bus.error(InteractionEvent(event, cache, "C-STORE", session_parameters=[f"Hash error: {exc}"], log_level="ERROR"))
        yield (QRStatus.FAILURE, None)
        return

    try:
        ds = event.dataset
    except Exception as exc:
        bus.error(InteractionEvent(event, cache, "C-STORE", session_parameters=[f"Dataset error: {exc}"], log_level="ERROR"))
        yield (QRStatus.FAILURE, None)
        return

    if (err := repo.store(ds)) is not None:
        bus.error(InteractionEvent(event, cache, "C-STORE", session_parameters=[
            f"SHA256: {file_hash}",
            f"Error: {err.error}",
        ], log_level="ERROR"))
        yield (err.status, None)
        return

    bus.info(InteractionEvent(event, cache, "C-STORE", session_parameters=[
        f"SHA256: {file_hash}",
        f"SOPInstanceUID: {ds.SOPInstanceUID}",
    ]))
    yield (QRStatus.SUCCESS, None)


def bind_acse(handler: EventHandler, repo: Repository, bus: Logger, cache: SessionCache) -> Callable:
    # NOTE: if this binder grows with weird functionality before passing
    # the event to the handler, we may want to have a pre-handler,
    # middlewares, and post-handler effects?
    def binder(event: Event, *args):
        handler(repo, bus, cache, event, *args)
    return binder


def bind_dimse_qr(handler: EventHandler, repo: Repository, bus: Logger, cache: SessionCache) -> Callable:
    # NOTE: if this binder grows with weird functionality before passing
    # the event to the handler, we may want to have a pre-handler,
    # middlewares, and post-handler effects?
    def binder(event: Event, *args):
        yield from handler(repo, bus, cache, event, *args)
    return binder


def bind_dimse_simple(handler: EventHandler, repo: Repository, bus: Logger, cache: SessionCache) -> Callable:
    # NOTE: if this binder grows with weird functionality before passing
    # the event to the handler, we may want to have a pre-handler,
    # middlewares, and post-handler effects?
    def binder(event: Event, *args):
        gen = handler(repo, bus, cache, event, *args)
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
        self.cache: SessionCache = SessionCache()

    def get(self, name: str) -> EventHandlerType | None:
        if handler := self.handlers.get(name):
            return handler

    def register(self, name: str, handler: EventHandlerType) -> 'DIMSEFactory':
        self.handlers[name] = handler
        return self


def new_dimse_factory(repo: Repository, bus: Logger) -> DIMSEFactory:
    factory = DIMSEFactory()
    cache = factory.cache
    for n, t, h in _handlers:
        if t in _ACSE_EVENTS:
            binder = bind_acse(h, repo, bus, cache)
        elif t in _QR_EVENTS:
            binder = bind_dimse_qr(h, repo, bus, cache)
        else:
            binder = bind_dimse_simple(h, repo, bus, cache)
        factory.register(n, (t, binder))
    return factory
