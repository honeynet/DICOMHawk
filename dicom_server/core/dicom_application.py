"""

A `DicomStarter` class to initialize and launch the DICOM server.
It sets up the Application Entity (AE), registers event handlers,
and verifies port availability before starting the server

"""

import socket
import sys
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
from vendor_personas import get_persona


class DicomStarter:

    def __init__(self, app_logger, exceptions_logger, port, ip, handlers, vendor_persona="default"):
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
        vendor_persona : str
            Name of a vendor persona to emulate (e.g. 'ge_ct', 'siemens_ct',
            'philips_mr').  Use 'default' for original behaviour.

        """
        self.logger = app_logger
        self.port = port
        self.ip = ip
        self.handlers = handlers
        self.exceptions_logger = exceptions_logger
        self.persona = get_persona(vendor_persona)

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
                self.logger.error(f"Port {self.port} is already in use. Please free the port or configure a different one.")
                sys.exit(1)
        except Exception:
            self.exceptions_logger.exception(
                "Unexpected error starting the application"
            )

    def initialize_application_entity(self):
        """
        Create and configure the Application Entity (AE).
        Registers supported and requested presentation contexts
        for storage, query/retrieve, and verification services.

        When a vendor persona is active, the AE title, implementation
        identifiers, and the set of advertised SOP classes / transfer
        syntaxes are restricted to match that persona, making the
        honeypot harder to fingerprint.
        """
        try:
            ae = AE()

            if self.persona:
                # -- Vendor persona mode --
                ae.ae_title = self.persona["ae_title"]
                ae.implementation_class_uid = self.persona["implementation_class_uid"]
                ae.implementation_version_name = self.persona["implementation_version_name"]

                transfer_syntaxes = self.persona["transfer_syntaxes"]

                for sop_class in self.persona["supported_sop_classes"]:
                    ae.add_supported_context(sop_class, transfer_syntaxes)
                    ae.add_requested_context(sop_class, transfer_syntaxes)

                self.logger.info(
                    f"Vendor persona active: {self.persona['description']} "
                    f"(AE title={self.persona['ae_title']}, "
                    f"SOP classes={len(self.persona['supported_sop_classes'])})"
                )
            else:
                # -- Default mode (original behaviour) --
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
