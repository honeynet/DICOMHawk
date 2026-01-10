"""

A `DicomStarter` class to initialize and launch the DICOM server.
It sets up the Application Entity (AE), registers event handlers,
and verifies port availability before starting the server

"""

import socket
from pynetdicom import evt
from pynetdicom.sop_class import (
    PatientRootQueryRetrieveInformationModelFind,
    Verification,
    StudyRootQueryRetrieveInformationModelMove,
    PatientRootQueryRetrieveInformationModelGet,
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelGet,
    PatientRootQueryRetrieveInformationModelMove,
)
from pynetdicom import (
    AE,
    AllStoragePresentationContexts,
    StoragePresentationContexts,
)


class DicomStarter:

    def __init__(self, app_logger, exceptions_logger, port, ip, handlers):
        """

        Constructor for DicomStarter.
        Parameters:
        ----------
        exceptions_logger : Logger
            A previously sat logger to handle exceptions.
        port : int
            DICOM server port.
        ip : str
            DICOM host IP address .
        handlers : object
            Assoc, C-FIND, C-GET, C-MOVE, Release, Abort  handler methods.

        """
        self.logger = app_logger
        self.port = port
        self.ip = ip
        self.handlers = handlers
        self.exceptions_logger = exceptions_logger

    def register_dicom_handlers(self):
        """
        List of event-handler tuples for the DICOM server.

        """
        try:
            handlers = [
                (evt.EVT_ACSE_RECV, self.handlers.handle_assoc),
                (evt.EVT_RELEASED, self.handlers.handle_release),
                (evt.EVT_C_FIND, self.handlers.handle_find),
                (evt.EVT_C_STORE, self.handlers.handle_store),
                (evt.EVT_C_ECHO, self.handlers.handle_echo),
                (evt.EVT_C_MOVE, self.handlers.handle_move),
                (evt.EVT_C_GET, self.handlers.handle_get),
                (evt.EVT_ABORTED, self.handlers.handle_abort),
            ]

            return handlers

        except Exception as e:
            self.exceptions_logger.exception(
                "Unexpected error while registering the DICOM handlers"
            )

    def start_the_application(self):
        """
        Start the DICOM server.
        Sets up the Application Entity and registers event handlers if the port is not already used.

        """
        try:
            ae = self.initialize_application_entity()
            handlers = self.register_dicom_handlers()
            if not self.is_port_in_use():
                self.logger.info("DICOM Server Started")
                ae.start_server(
                    (self.ip, self.port),
                    evt_handlers=handlers,
                )
            else:
                self.logger.debug(f"port {self.port} is in use")
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error starting the application"
            )

    def initialize_application_entity(self):
        """
        Create and configure the Application Entity (AE).
        Registers supported and requested presentation contexts
        for storage, query/retrieve, and verification services.
        """
        self.ae.maximum_associations = 50
        try:
            ae = AE()
            ae.supported_contexts = AllStoragePresentationContexts
            ae.requested_contexts = StoragePresentationContexts
            ae.add_supported_context(PatientRootQueryRetrieveInformationModelFind)
            ae.add_supported_context(PatientRootQueryRetrieveInformationModelGet)
            ae.add_supported_context(StudyRootQueryRetrieveInformationModelGet)
            ae.add_supported_context(StudyRootQueryRetrieveInformationModelFind)
            ae.add_supported_context(StudyRootQueryRetrieveInformationModelMove)
            ae.add_supported_context(PatientRootQueryRetrieveInformationModelMove)
            ae.add_supported_context(Verification)
            self.initialize_storage_contexts(StoragePresentationContexts)
            self.logger.debug("Application entity initialized")
            return ae
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error while initializing the application entity object"
            )

    # Ensure the presentation context used when initializing the server can act as SCU to handle STORE operation

    def initialize_storage_contexts(self, StoragePresentationContexts):
        """
        Configure the roles (SCP/SCU) for each Storage Presentation Context.

        """
        for context in StoragePresentationContexts:
            context._as_scp = True
            context._as_scu = True
            context.scp_role = True
            context.scu_role = True

    def is_port_in_use(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex((self.ip, self.port)) == 0
