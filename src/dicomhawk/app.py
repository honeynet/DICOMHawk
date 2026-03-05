from .server import ServerConfig, new_server
from .component import Component
from . import event_logger
from .config import settings

from logging import Logger

class DicomHawk:
    def __init__(self, logger: Logger, components: list[Component], config: ServerConfig) -> None:
        self.logger = logger
        self.components = components
        self.server = new_server(self.logger, config)
        event_logger.configure(settings.DICOM.EVENT_LOG)

    def start(self) -> None:
        for c in self.components:
            c.start()
        self.server.run()

    def stop(self) -> None:
        self.server.stop()
        for c in self.components:
            c.stop()

def new_dicomhawk(logger: Logger, components: list[Component], config: ServerConfig) -> DicomHawk:
    return DicomHawk(logger, components, config)
