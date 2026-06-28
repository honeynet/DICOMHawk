
import logging
from logging import Logger
from dataclasses import dataclass
from itertools import chain
from pynetdicom import AE

from pynetdicom.events import EventHandlerType
from pynetdicom.transport import ThreadedAssociationServer
from pynetdicom.presentation import AllStoragePresentationContexts

from pynetdicom.sop_class import (
    _QR_CLASSES,
    _VERIFICATION_CLASSES
)

logger = logging.getLogger(__name__)

@dataclass
class ServerConfig:
    HOST: str
    PORTS: list[int]

    AE_TITLE: str # AE title
    # UserInfo (application identity)
    IMPLEMENTATION_UID: str
    IMPLEMENTATION_NAME: str

    # Maximum number of associations (min: 1, max: 65536)
    MAX_ASSOC: int = 65536

def new_config(host: str, ports: list[int], ae_title: str, impl_uid: str, impl_name: str) -> ServerConfig:
    return ServerConfig(host, ports, ae_title, impl_uid, impl_name)

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

        # scu_role lets the server SEND instances during C-GET/C-MOVE sub-operations; scp_role
        # lets it RECEIVE C-STORE. Without scu_role, C-GET sub-operations are rejected.
        for ctx in AllStoragePresentationContexts:
            ae.add_supported_context(ctx.abstract_syntax, scu_role=True, scp_role=True)

        for qr in chain(_QR_CLASSES.values(), _VERIFICATION_CLASSES.values()):
            ae.add_supported_context(qr)

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
                logger.info(f"Listening on {self.config.HOST}:{port}")

        for th in threads:
            th.serve_forever()

    def stop(self):
        for srv in self.listeners:
            srv.shutdown()

def new_server(bus: Logger, config: ServerConfig, handlers: list[EventHandlerType]) -> Server:
    return Server(bus, config, handlers)