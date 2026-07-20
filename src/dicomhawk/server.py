import logging
import threading
from logging import Logger
from dataclasses import dataclass
from pynetdicom import AE

from pynetdicom.events import EventHandlerType
from pynetdicom.transport import ThreadedAssociationServer

logger = logging.getLogger(__name__)

# (abstract_syntax_uid, [transfer_syntax_uids]) — plain tuple so this module never imports profiles.
type SopClass = tuple[str, list[str]]


@dataclass
class ServerConfig:
    HOST: str
    PORTS: list[int]

    AE_TITLE: str  # AE title
    IMPLEMENTATION_UID: (
        str | None
    )  # None -> pynetdicom's own PYNETDICOM_IMPLEMENTATION_* defaults
    IMPLEMENTATION_NAME: str | None

    # OPERATIONS gates both handler registration (serve.py) and contexts below.
    OPERATIONS: list[str]
    VERIFICATION: SopClass
    STORAGE_CLASSES: list[SopClass]
    QR_CLASSES: dict[str, list[SopClass]]

    MAX_ASSOC: int
    MAX_PDU_SIZE: int | None  # None -> pynetdicom's own default
    REQUIRE_CALLED_AET: bool = False
    REQUIRE_CALLING_AET: list[str] | None = None
    # None -> pynetdicom's own defaults (30s/60s/30s); tighter values shrink a garbage-connection's DoS window.
    ACSE_TIMEOUT: float | None = None
    NETWORK_TIMEOUT: float | None = None
    DIMSE_TIMEOUT: float | None = None


def new_config(
    host: str,
    ports: list[int],
    ae_title: str,
    impl_uid: str | None,
    impl_name: str | None,
    operations: list[str],
    verification: SopClass,
    storage_classes: list[SopClass],
    qr_classes: dict[str, list[SopClass]],
    max_associations: int,
    max_pdu_size: int | None,
    *,
    require_called_aet: bool = False,
    require_calling_aet: list[str] | None = None,
    acse_timeout: float | None = None,
    network_timeout: float | None = None,
    dimse_timeout: float | None = None,
) -> ServerConfig:
    return ServerConfig(
        host,
        ports,
        ae_title,
        impl_uid,
        impl_name,
        operations,
        verification,
        storage_classes,
        qr_classes,
        max_associations,
        max_pdu_size,
        REQUIRE_CALLED_AET=require_called_aet,
        REQUIRE_CALLING_AET=require_calling_aet,
        ACSE_TIMEOUT=acse_timeout,
        NETWORK_TIMEOUT=network_timeout,
        DIMSE_TIMEOUT=dimse_timeout,
    )


class Server:
    def __init__(
        self,
        bus: Logger,
        config: ServerConfig,
        handlers: list[EventHandlerType],
    ):

        self.logger = bus
        self.config = config
        self.handlers = handlers
        self.listeners: list[ThreadedAssociationServer] = []
        self._stopped = threading.Event()
        self._stop_lock = threading.Lock()

    def init(self) -> AE:
        logger.debug("Initializing AE")

        ae = AE(ae_title=self.config.AE_TITLE)

        if self.config.IMPLEMENTATION_UID:
            ae.implementation_class_uid = self.config.IMPLEMENTATION_UID
        if self.config.IMPLEMENTATION_NAME:
            ae.implementation_version_name = self.config.IMPLEMENTATION_NAME

        ae.maximum_associations = self.config.MAX_ASSOC
        if self.config.MAX_PDU_SIZE is not None:
            ae.maximum_pdu_size = self.config.MAX_PDU_SIZE

        ops = self.config.OPERATIONS
        if "echo" in ops:
            uid, ts = self.config.VERIFICATION
            ae.add_supported_context(uid, ts)

        # Storage contexts carry incoming C-STORE and C-GET's outgoing sub-operations.
        if "store" in ops or "get" in ops:
            for uid, ts in self.config.STORAGE_CLASSES:
                ae.add_supported_context(
                    uid,
                    ts,
                    scu_role="get" in ops,
                    scp_role="store" in ops,
                )

        for op in ("find", "move", "get"):
            if op not in ops:
                continue
            for uid, ts in self.config.QR_CLASSES.get(op, []):
                ae.add_supported_context(uid, ts, scu_role=True, scp_role=True)

        if self.config.REQUIRE_CALLED_AET:
            ae.require_called_aet = True
        if self.config.REQUIRE_CALLING_AET:
            ae.require_calling_aet = self.config.REQUIRE_CALLING_AET

        if self.config.ACSE_TIMEOUT is not None:
            ae.acse_timeout = self.config.ACSE_TIMEOUT
        if self.config.NETWORK_TIMEOUT is not None:
            ae.network_timeout = self.config.NETWORK_TIMEOUT
        if self.config.DIMSE_TIMEOUT is not None:
            ae.dimse_timeout = self.config.DIMSE_TIMEOUT

        return ae

    def run(self):
        app = self.init()

        try:
            for port in self.config.PORTS:
                if self._stopped.is_set():
                    break
                if worker := app.start_server(
                    (self.config.HOST, port), evt_handlers=self.handlers, block=False
                ):
                    with self._stop_lock:
                        if self._stopped.is_set():
                            worker.shutdown()
                            break
                        self.listeners.append(worker)
                    logger.info(f"Listening on {self.config.HOST}:{port}")
        except Exception:
            self.stop()
            raise

        # start_server(block=False) owns the listener threads.
        self._stopped.wait()

    def stop(self):
        with self._stop_lock:
            listeners, self.listeners = self.listeners, []
            for srv in listeners:
                srv.shutdown()
            self._stopped.set()


def new_server(
    bus: Logger, config: ServerConfig, handlers: list[EventHandlerType]
) -> Server:
    return Server(bus, config, handlers)
