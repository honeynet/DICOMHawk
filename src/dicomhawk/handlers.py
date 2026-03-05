from typing import Callable, Any, Generator, Optional

from pynetdicom import evt
from pydicom.dataset import Dataset
from pynetdicom.events import Event, EventHandlerType, EventType

from .status import QRStatus
from .repository import Repository

import logging
from logging import Logger

logger = logging.getLogger(__name__)

type EventHandler = Callable[[
    Repository, # instance of the repo, to store and retrieve
    Logger, # a bus to push logs
    Event, # the event
], Any]
type QRResult = Generator[tuple[int, Optional[Dataset]], None, None]

# def middlewhare(event: Event)
# take the event and send it together with the event manager to the handler

def default_handler(**kwargs):
    pass

def handle_find(
        repo: Repository, # This is an interface to the repo
        bus: Logger,
        event: Event
    ) -> QRResult:

    if err := repo.eval_qr(event):
        # NOTE: we may want a separate logger for this?
        logger.error(err.error)
        yield (err.status, None)

    # NOTE: the ev will log this
    idt = event.identifier
    model = event.request.AffectedSOPClassUID

    result = repo.find(idt, model, inject=True)
    if (err:=result.error) is not None:
        logger.error(err.error)
        yield (err.status, None)
        return
    
    for m in result.matches:
        if event.is_cancelled:
            yield (QRStatus.CANCEL, None)
            return

        res = m.as_identifier(idt, model)
        res.RetrieveAETitle = event.assoc.ae.ae_title

        yield (QRStatus.PENDING, res)
    yield (QRStatus.SUCCESS, None)

def handle_get(
        repo: Repository,
        bus: Logger,
        event: Event
    ) -> QRResult:

    if err := repo.eval_qr(event):
        logger.error(err.error)
        yield (err.status, None)

    idt = event.identifier
    model = event.request.AffectedSOPClassUID

    result = repo.find(idt, model)
    if (err:=result.error) is not None:
        logger.error(err.error)
        yield (err.status, None)
        return

    yield len(result.matches) # type: ignore
    for m in result.matches:
        if event.is_cancelled:
            yield (QRStatus.CANCEL, None)
            return
        
        res = repo.find_instance(m, decompress=True)
        if (err:=res.error) is not None:
            logger.error(err.error)
            yield (err.status, None)

        yield (QRStatus.PENDING, res.dataset)
    yield (QRStatus.SUCCESS, None)

def handle_move(
        repo: Repository,
        bus: Logger,
        event: Event
    ) -> QRResult:

    if err := repo.eval_qr(event):
        logger.error(err.error)
        yield (err.status, None)

    idt = event.identifier
    model = event.request.AffectedSOPClassUID

    result = repo.find(idt, model)
    if (err:=result.error) is not None:
        logger.error(err.error)
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
            yield (QRStatus.CANCEL, None)
            return
        
        res = repo.find_instance(m, decompress=True)
        if (err:=res.error) is not None:
            logger.error(err.error)
            yield (err.status, None)

        yield (QRStatus.PENDING, res.dataset)

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
        if handler := self.handlers[name]:
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