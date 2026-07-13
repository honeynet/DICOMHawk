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
    """Attacker-facing and loopback operator apps with explicit server lifecycles."""

    def __init__(
        self,
        profile: ProfileConfig,
        repo: Repository,
        bus: logging.Logger,
        host: str,
        web_port: int,
        operator_port: int,
        operator_host: str = "127.0.0.1",
    ):
        self.profile = profile
        self.repo = repo
        self.bus = bus
        self.host = host
        self.web_port = web_port
        self.operator_port = operator_port
        self.operator_host = operator_host
        self._servers = []
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._servers:
            return
        web_app = new_web(self.profile, self.repo, self.bus)
        operator_app = new_operator_api(self.profile, self.repo, self.bus)
        specs = (
            (
                "web",
                web_app,
                self.host,
                self.web_port,
                self.profile.web.max_request_bytes,
            ),
            (
                "operator",
                operator_app,
                self.operator_host,
                self.operator_port,
                1_048_576,
            ),
        )
        try:
            for name, app, host, port, max_body in specs:
                server = waitress.create_server(
                    app,
                    host=host,
                    port=port,
                    max_request_body_size=max_body,
                )
                self._servers.append(server)
                self._threads.append(
                    threading.Thread(
                        target=server.run,
                        daemon=True,
                        name=f"dicomhawk-{name}",
                    )
                )
        except Exception:
            self.stop()
            raise
        for thread in self._threads:
            thread.start()
        logger.info(
            f"Web: {self.host}:{self.web_port}  Operator API: {self.operator_host}:{self.operator_port}"
        )

    def stop(self) -> None:
        for server in self._servers:
            server.close()
            server.task_dispatcher.shutdown(cancel_pending=True, timeout=5)
        for thread in self._threads:
            if thread.is_alive() and thread is not threading.current_thread():
                thread.join(timeout=5)
        self._servers.clear()
        self._threads.clear()


def new_web_component(
    profile: ProfileConfig,
    repo: Repository,
    bus: logging.Logger,
    host: str,
    web_port: int,
    operator_port: int,
    operator_host: str = "127.0.0.1",
) -> WebComponent:
    return WebComponent(
        profile, repo, bus, host, web_port, operator_port, operator_host
    )
