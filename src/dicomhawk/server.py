from copy import copy
from logging import Logger
from itertools import chain
from pynetdicom import (
    evt,
    AE,
)

from pynetdicom.transport import ThreadedAssociationServer
from pynetdicom.presentation import (
    AllStoragePresentationContexts,
    StoragePresentationContexts,
)

from pynetdicom.sop_class import (
    _QR_CLASSES,
    _VERIFICATION_CLASSES
)

class ServerConfig:
    PORTS: list[int]
    HOST: str
    
    STORAGE_DIR: str
    C_STORE_DIR: str
    DATABASE: str
    HASH_STORE: str
    CANARY_PDF: str
    INTEGRITY_CHECK: bool

    # AE title
    AE_TITLE: str

    # UserInfo (application identity)
    IMPLEMENTATION_NAME: str
    IMPLEMENTATION_UID: str

    # Maximum number of associations (min: 1, max: 65536)
    MAX_ASSOC: int

class Server:
    listeners: list[ThreadedAssociationServer]

    def __init__(
            self, 
            logger: Logger,
            config: ServerConfig,
        ):

        self.logger = logger
        self.config = config

        # TODO: we have a whole class to make handlers, use that
        handler_factory = new_handler_factory()
        self.handlers = self.make_handlers(handler_factory)

    def make_handlers(self, handlers: dict):
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
        self.logger.debug("Initializing AE")

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

        # Set supported operations 
        store_ctx = copy(StoragePresentationContexts)
        for ctx in store_ctx:
            ctx._as_scp = True
            ctx._as_scu = True
            ctx.scp_role = True
            ctx.scu_role = True

        ae.requested_contexts = store_ctx
        ae.supported_contexts = AllStoragePresentationContexts

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
            
        for th in threads:
            th.serve_forever()
        self.logger.info(f"Listening in {self.config.PORTS}")

    def stop(self):
        for srv in self.listeners:
            srv.shutdown()

def new_server(logger: Logger, config: ServerConfig) -> Server:
    return Server(logger, config)