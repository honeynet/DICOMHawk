from typing import Callable, Any, Generator, Optional
from logging import Logger

from pynetdicom import evt
from pydicom.dataset import Dataset
from pynetdicom.events import Event, EventHandlerType, EventType

from .status import QRStatus
from .repository import Repository
from .bus import new_response, new_request

import logging

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

def handle_find(
        repo: Repository, # This is an interface to the repo
        bus: Logger,
        event: Event
    ) -> QRResult:

    # NOTE: the ev will log this
    idt = event.identifier
    model = event.request.AffectedSOPClassUID

    result = repo.find(idt, model, inject=True)
    if (err:=result.error) is not None:
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
    if (err:=result.error) is not None:
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
        if (err:=res.error) is not None:
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

    idt = event.identifier
    model = event.request.AffectedSOPClassUID

    result = repo.find(idt, model)
    if (err:=result.error) is not None:
        bus.info(new_response(event, err.status, error=err.error))
        yield (err.status, None)
        return

    # TODO: need destinations
    # try:
    #     addr, port = destinations[event.move_destination]
    # except KeyError:
    #     logger.info("No matching move destination in the configuration")
    #     yield None, None
    #     return
    # contexts = list(set([ii.context for ii in matches]))
    # yield addr, port, {"contexts": contexts[:128]}
    
    yield len(result.matches) # type: ignore
    for m in result.matches:
        if event.is_cancelled:
            bus.info(new_response(event, QRStatus.CANCEL))
            yield (QRStatus.CANCEL, None)
            return
        
        res = repo.find_instance(m, decompress=True)
        if (err:=res.error) is not None:
            bus.info(new_response(event, err.status, error=err.error))
            yield (err.status, None)

        yield (QRStatus.PENDING, res.dataset)

    bus.info(new_response(event, QRStatus.SUCCESS))
    yield (QRStatus.SUCCESS, None)


def handle_store(
        repo: Repository,
        bus: Logger,
        event: Event
    ) -> QRResult:

    repo.store(event.identifier)
    yield (QRStatus.SUCCESS, None)


def bind(repo: Repository, bus: Logger, handler: EventHandler) -> Callable:
    def binder(evt: Event, *args):
        # NOTE: if this binder grows with weird functionality before passing
        # the event to the handler, we may want to have a pre-handler, 
        # middlewares, and post-handler effects?
        bus.info(new_request(evt))
        if err := repo.eval_qr(evt):
            logger.error(err.error)
            yield (err.status, None)

        return handler(repo, bus, evt, *args)
    return binder


_handlers: list[tuple[str, EventType, EventHandler]] = [
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
        handler = bind(repo, bus, h)
        factory.register(n, (t, handler))

    return factory