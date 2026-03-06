from .server import Server
from .component import Component

import logging
logger = logging.getLogger(__name__)

class Dicomhawk:
    def __init__(self, components: list[Component], server: Server) -> None:
        self.components = components
        self.server = server

    def start(self) -> None:
        logger.debug("Starting DICOMHawk")
        for c in self.components:
            c.start()
        self.server.run()

    def stop(self) -> None:
        logger.debug("Stopping DICOMHawk")
        self.server.stop()
        for c in self.components:
            c.stop()

def new_dicomhawk(server: Server, components: list[Component]) -> Dicomhawk:
    return Dicomhawk(components, server)
