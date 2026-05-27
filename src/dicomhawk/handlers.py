import logging
from logging import Logger
from typing import Callable, Any, Generator, Optional

from pynetdicom import evt
from pydicom.dataset import Dataset
from pynetdicom.events import Event, EventHandlerType, EventType

from .status import QRStatus
from .repository import Repository
from .bus import new_response, new_request

logger = logging.getLogger(__name__)

type EventHandler = Callable[
    [
        Repository, # instance of the repo, to store and retrieve
        Logger, # a bus to push logs
        Event, # the event
    ],
    Any,
]
type QRResult = Generator[tuple[int, Optional[Dataset]], None, None]

# Only Q/R operations get eval_qr validation; C-STORE and C-ECHO do not carry
# QueryRetrieveLevel and must bypass that check entirely.
_QR_EVENTS: frozenset[EventType] = frozenset({
    evt.EVT_C_FIND,
    evt.EVT_C_GET,
    evt.EVT_C_MOVE,
})


def handle_echo(
        repo: Repository,
        bus: Logger,
        event: Event
    ) -> QRResult:
    bus.info(new_response(event, QRStatus.SUCCESS))
    yield (QRStatus.SUCCESS, None)


def handle_find(
        repo: Repository,
        bus: Logger,
        event: Event
    ) -> QRResult:

    # NOTE: the ev will log this
    idt = event.identifier
    model = event.request.AffectedSOPClassUID

    result = repo.find(idt, model, inject=True)
    if (err := result.error) is not None:
        bus.info(new_response(event, err.status, error=err.error))
        yield (err.status, None)
        return

    bus.info(new_response(event, QRStatus.PENDING, data={"matches": len(result.matches)}))
    for m in result.matches:
        if event.is_cancelled:
            bus.info(new_response(event, QRStatus.CANCEL))
            yield (QRStatus.CANCEL, None)
            return

        res = m.as_identifier(idt, model)
        res.RetrieveAETitle = event.assoc.ae.ae_title
        yield (QRStatus.PENDING, res)

    bus.info(new_response(event, QRStatus.SUCCESS))
    yield (QRStatus.SUCCESS, None)


def handle_get(
        repo: Repository,
        bus: Logger,
        event: Event
    ) -> QRResult:

    try:
        idt = event.identifier
    except AttributeError as e:
        s = QRStatus.FAILURE
        bus.info(new_response(event, s, error=str(e)))
        yield (s, None)
        return

    model = event.request.AffectedSOPClassUID
    result = repo.find(idt, model)
    if (err := result.error) is not None:
        bus.info(new_response(event, err.status, error=err.error))
        yield (err.status, None)
        return

    bus.info(new_response(event, QRStatus.PENDING, data={"matches": len(result.matches)}))
    yield len(result.matches) # type: ignore
    for m in result.matches:
        if event.is_cancelled:
            bus.info(new_response(event, QRStatus.CANCEL))
            yield (QRStatus.CANCEL, None)
            return

        res = repo.find_instance(m, decompress=True)
        if (err := res.error) is not None:
            bus.info(new_response(event, err.status, error=err.error))
            yield (err.status, None)

        yield (QRStatus.PENDING, res.dataset)

    bus.info(new_response(event, QRStatus.SUCCESS))
    yield (QRStatus.SUCCESS, None)


def handle_move(
        repo: Repository,
        bus: Logger,
        event: Event
    ) -> QRResult:
    # NOTE: honeypot-correct — log the destination AE title (attacker infrastructure)
    # then yield (None, None); pynetdicom auto-responds 0xA801 "Move Destination Unknown"
    # so no data ever leaves the honeypot.
    bus.info(new_response(event, QRStatus.FAILURE, data={"destination": str(event.move_destination).strip()}))
    yield (None, None)


def handle_store(
        repo: Repository,
        bus: Logger,
        event: Event
    ) -> QRResult:

    try:
        ds = event.dataset
    except Exception as exc:
        bus.info(new_response(event, QRStatus.FAILURE, error=str(exc)))
        yield (QRStatus.FAILURE, None)
        return

    if (err := repo.store(ds)) is not None:
        bus.info(new_response(event, err.status, error=err.error))
        yield (err.status, None)
        return

    bus.info(new_response(event, QRStatus.SUCCESS))
    yield (QRStatus.SUCCESS, None)


def bind(repo: Repository, bus: Logger, handler: EventHandler, event_type: EventType) -> Callable:
    # NOTE: if this binder grows with weird functionality before passing
    # the event to the handler, we may want to have a pre-handler,
    # middlewares, and post-handler effects?
    _eval_qr = event_type in _QR_EVENTS

    if _eval_qr:
        # C-FIND, C-GET, C-MOVE: pynetdicom drives these as iterators
        def binder(event: Event, *args):
            bus.info(new_request(event))
            if err := repo.eval_qr(event):
                logger.error(err.error)
                yield (err.status, None)
                return
            yield from handler(repo, bus, event, *args)
        return binder
    else:
        # C-ECHO, C-STORE: pynetdicom expects a plain int status return value,
        # not an iterator. Advance the handler generator once to get the status.
        def binder(event: Event, *args):
            bus.info(new_request(event))
            gen = handler(repo, bus, event, *args)
            try:
                status, _ = next(gen)
                return int(status)
            except StopIteration:
                return int(QRStatus.SUCCESS)
        return binder


_handlers: list[tuple[str, EventType, EventHandler]] = [
    ("echo", evt.EVT_C_ECHO, handle_echo),
    ("get", evt.EVT_C_GET, handle_get),
    ("store", evt.EVT_C_STORE, handle_store),
    ("find", evt.EVT_C_FIND, handle_find),
    ("move", evt.EVT_C_MOVE, handle_move),
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
