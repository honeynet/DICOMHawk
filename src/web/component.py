import logging
import threading

import waitress

from dicomhawk.component import Component
from dicomhawk.repository import Repository
from profiles.profile import ProfileConfig

from .app import new_web
from .operator_api import new_operator_api

logger = logging.getLogger(__name__)


class WebComponent(Component):
    """Attacker-facing + operator-facing Flask apps, each served by waitress in its own daemon thread."""

    def __init__(self, profile: ProfileConfig, repo: Repository, bus: logging.Logger,
                 host: str, web_port: int, operator_port: int, operator_host: str = "127.0.0.1"):
        self.profile = profile
        self.repo = repo
        self.bus = bus
        self.host = host
        self.web_port = web_port
        self.operator_port = operator_port
        self.operator_host = operator_host

    def start(self) -> None:
        web_app = new_web(self.profile, self.repo, self.bus)
        operator_app = new_operator_api(self.profile, self.repo, self.bus)
        for name, app, host, port in (
            ("web", web_app, self.host, self.web_port),
            ("operator", operator_app, self.operator_host, self.operator_port),
        ):
            threading.Thread(
                target=waitress.serve, args=(app,),
                kwargs={"host": host, "port": port},
                daemon=True, name=f"dicomhawk-{name}",
            ).start()
        logger.info(f"Web: {self.host}:{self.web_port}  Operator API: {self.operator_host}:{self.operator_port}")

    def stop(self) -> None:
        # No cross-thread waitress stop; daemon threads exit with the process
        # (graceful SIGTERM shutdown is the Weeks 11-12 hardening item, not this component's job).
        pass


def new_web_component(profile: ProfileConfig, repo: Repository, bus: logging.Logger,
                       host: str, web_port: int, operator_port: int,
                       operator_host: str = "127.0.0.1") -> WebComponent:
    return WebComponent(profile, repo, bus, host, web_port, operator_port, operator_host)
