
import logging
from logging import Logger
from copy import copy
from dataclasses import dataclass
from itertools import chain
from pynetdicom import (
    evt,
    AE,
)

from pynetdicom.events import EventHandlerType
from pynetdicom.transport import ThreadedAssociationServer
from pynetdicom.presentation import (
    AllStoragePresentationContexts,
    StoragePresentationContexts,
    build_context,
)

from pynetdicom.sop_class import (
    _QR_CLASSES,
    _VERIFICATION_CLASSES
)

from .handlers import DIMSEFactory

logger = logging.getLogger(__name__)

@dataclass
class ServerConfig:
    HOST: str
    PORTS: list[int]

    AE_TITLE: str # AE title
    # UserInfo (application identity)
    IMPLEMENTATION_UID: str
    IMPLEMENTATION_NAME: str
    MAX_ASSOC: int = 65536  # Maximum number of associations (min: 1, max: 65536)
    presentation_contexts: list | None = None

def new_config(host: str, ports: list[int], ae_title: str, impl_uid: str, impl_name: str, presentation_contexts: list = None) -> ServerConfig:
    return ServerConfig(host, ports, ae_title, impl_uid, impl_name, presentation_contexts=presentation_contexts)

class Server:
    listeners: list[ThreadedAssociationServer]

    def __init__(
            self, 
            bus: Logger,
            config: ServerConfig,
            handlers: list[EventHandlerType],
        ):

        self.logger = bus
        self.config = config
        self.handlers = handlers

    def make_handlers(self, handlers: DIMSEFactory):
        # TODO: the config should have a list of supported operations
        return [
            (evt.EVT_ACSE_RECV, handlers.get("associate")),
            (evt.EVT_RELEASED, handlers.get("release")),
            (evt.EVT_C_FIND, handlers.get("find")),
            (evt.EVT_C_STORE, handlers.get("store")),
            (evt.EVT_C_ECHO, handlers.get("echo")),
            (evt.EVT_C_MOVE, handlers.get("move")),
            (evt.EVT_C_GET, handlers.get("get")),
            (evt.EVT_ABORTED, handlers.get("abort")),
        ]

    def init(self) -> AE:
        logger.debug("Initializing AE")

        # Titles
        ae = AE(ae_title=self.config.AE_TITLE)

        # Implementation identification
        ae.implementation_class_uid = self.config.IMPLEMENTATION_UID
        ae.implementation_version_name = self.config.IMPLEMENTATION_NAME

        # Other config
        ae.maximum_associations = self.config.MAX_ASSOC
        # NOTE: this is an annoying setting. Default value refuses connections
        # from greedy malware
        ae.maximum_pdu_size = 65536

        if self.config.presentation_contexts is not None:
            ctx_list = copy(self.config.presentation_contexts)
        else:
            # Set supported operations
            ctx_list = copy(StoragePresentationContexts)
            for qr in chain(_QR_CLASSES.values(), _VERIFICATION_CLASSES.values()):
                ctx_list.append(build_context(qr))

        # Configure SCP/SCU roles for all contexts
        for ctx in ctx_list:
            # TODO: this could come from some sort of setting to decide what we are
            ctx._as_scp = True
            ctx._as_scu = True
            ctx.scp_role = True
            ctx.scu_role = True

        # Max 128 contexts per association per standard
        ae.requested_contexts = ctx_list[:128]
        ae.supported_contexts = ctx_list

        return ae
    
    def run(self):        
        # Start server on each port
        app = self.init()

        threads: list[ThreadedAssociationServer] = []
        for port in self.config.PORTS:
            if worker := app.start_server(
                (self.config.HOST, port),
                evt_handlers=self.handlers,
                block=False 
            ):
                threads.append(worker)
            
        for th in threads:
            th.serve_forever()
        self.logger.info(f"Listening in {self.config.PORTS}")

    def stop(self):
        for srv in self.listeners:
            srv.shutdown()

def new_server(bus: Logger, config: ServerConfig, handlers: list[EventHandlerType]) -> Server:
    return Server(bus, config, handlers)