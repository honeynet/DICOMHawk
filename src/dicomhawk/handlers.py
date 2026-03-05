from typing import Callable, Any, Generator, Optional

from pydicom.dataset import Dataset
from pynetdicom.events import Event

from .status import QRStatus
from .repository import Repository
from . import event_logger

import logging

logger = logging.getLogger(__name__)

type EventHandler = Callable[[Repository, Event], Any]
type QRResult = Generator[tuple[int, Optional[Dataset]], None, None]

# def middlewhare(event: Event)
# take the event and send it together with the event manager to the handler

def _caller_info(event: Event) -> dict:
    """Pull the requesting side's address and AE title out of an event."""
    requestor = event.assoc.requestor
    ae_title = requestor.ae_title
    if isinstance(ae_title, bytes):
        ae_title = ae_title.decode("ascii", errors="replace")
    return {
        "src_ip": requestor.address,
        "src_port": requestor.port,
        "ae_title_calling": str(ae_title).strip(),
    }


def default_handler(**kwargs):
    pass


def handle_find(
        repo: Repository, # This is an interface to the repo
        event: Event
    ) -> QRResult:

    sop = event.request.AffectedSOPClassUID
    qr_level = getattr(event.identifier, "QueryRetrieveLevel", "")

    event_logger.log_event({
        **_caller_info(event),
        "dimse_operation": "C-FIND",
        "sop_class_uid": str(sop) if sop else None,
        "query_retrieve_level": str(qr_level),
        "honeytoken_injected": True,
        "connection_result": "accepted",
    })

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
        event: Event
    ) -> QRResult:

    sop = event.request.AffectedSOPClassUID
    qr_level = getattr(event.identifier, "QueryRetrieveLevel", "")

    event_logger.log_event({
        **_caller_info(event),
        "dimse_operation": "C-GET",
        "sop_class_uid": str(sop) if sop else None,
        "query_retrieve_level": str(qr_level),
        "honeytoken_injected": False,
        "connection_result": "accepted",
    })

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
        event: Event
    ) -> QRResult:

    sop = event.request.AffectedSOPClassUID
    qr_level = getattr(event.identifier, "QueryRetrieveLevel", "")
    destination = event.move_destination
    if isinstance(destination, bytes):
        destination = destination.decode("ascii", errors="replace")

    event_logger.log_event({
        **_caller_info(event),
        "dimse_operation": "C-MOVE",
        "sop_class_uid": str(sop) if sop else None,
        "query_retrieve_level": str(qr_level),
        "move_destination": str(destination).strip() if destination else None,
        "honeytoken_injected": False,
        "connection_result": "accepted",
    })

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
        event: Event
    ) -> QRResult:

    ds = event.identifier
    sop_class = getattr(ds, "SOPClassUID", None)
    sop_instance = getattr(ds, "SOPInstanceUID", None)

    event_logger.log_event({
        **_caller_info(event),
        "dimse_operation": "C-STORE",
        "sop_class_uid": str(sop_class) if sop_class else None,
        "sop_instance_uid": str(sop_instance) if sop_instance else None,
        "honeytoken_injected": False,
        "connection_result": "accepted",
    })

    repo.store(event.identifier)
    yield (QRStatus.SUCCESS, None)


class DIMSEFactory:
    handlers: dict[str, EventHandler]

    def get(self, name: str) -> EventHandler | None:
        return self.handlers[name]
    
    def register(self, name: str, handler: EventHandler) -> 'DIMSEFactory':
        self.handlers[name] = handler
        return self


def new_dimse_factory() -> DIMSEFactory:
    factory = DIMSEFactory()
    # TODO: register handlers

    return factory