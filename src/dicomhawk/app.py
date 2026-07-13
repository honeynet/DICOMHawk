from .server import Server
from .component import Component

import logging
import threading

logger = logging.getLogger(__name__)


class Dicomhawk:
    def __init__(self, components: list[Component], server: Server) -> None:
        self.components = components
        self.server = server
        self._stop_lock = threading.Lock()
        self._stopped = False

    def start(self) -> None:
        logger.debug("Starting DICOMHawk")
        started = []
        try:
            for component in self.components:
                component.start()
                started.append(component)
            self.server.run()
        except Exception:
            self.server.stop()
            for component in reversed(started):
                component.stop()
            raise

    def stop(self) -> None:
        with self._stop_lock:
            if self._stopped:
                return
            self._stopped = True
            logger.debug("Stopping DICOMHawk")
            self.server.stop()
            for component in reversed(self.components):
                component.stop()


def new_dicomhawk(server: Server, components: list[Component]) -> Dicomhawk:
    return Dicomhawk(components, server)
