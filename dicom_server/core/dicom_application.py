"""

A `DicomStarter` class to initialize and launch the DICOM server.
It sets up the Application Entity (AE), registers event handlers,
and verifies port availability before starting the server

"""
from pynetdicom import AE, evt
from pynetdicom.sop_class import Verification


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
                (evt.EVT_C_ECHO, self.handlers.handle_echo),
                (evt.EVT_C_STORE, self.handlers.handle_store),
                (evt.EVT_ACCEPTED, self.handlers.handle_assoc_accepted),
                (evt.EVT_REJECTED, self.handlers.handle_assoc_rejected),
                (evt.EVT_ABORTED, self.handlers.handle_assoc_aborted),
            ]

            return handlers

        except Exception as e:
            self.exceptions_logger.exception(
                "Unexpected error while registering the DICOM handlers"
            )

    def start_the_application(self):

        ae = AE(ae_title=b"DICOMHAWK")

        # Enforce CALLED AE title
        ae.require_called_aet = True
        ae.accepted_aetitles = [b"DICOMHAWK", b"TCIA"]

        # ---- CRITICAL PART ----
        # Advertise Verification SCP correctly
        ae.add_supported_context(Verification)

        handlers = [
            (evt.EVT_REQUESTED, self.handlers.handle_assoc),
            (evt.EVT_ACCEPTED, self.handlers.handle_assoc_accepted),
            (evt.EVT_REJECTED, self.handlers.handle_assoc_rejected),
            (evt.EVT_ABORTED, self.handlers.handle_assoc_aborted),
            (evt.EVT_C_ECHO, self.handlers.handle_echo),
            (evt.EVT_C_STORE, self.handlers.handle_store),
        ]

        ae.start_server(
            ("0.0.0.0", self.port),
            block=True,
            evt_handlers=handlers
        )

    