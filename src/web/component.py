import logging
import threading

import waitress

from dicomhawk.component import Component
from dicomhawk.repository import Repository
from profiles.profile import ProfileConfig

from .app import new_web
from .dicomweb import new_dicomweb
from .operator_api import new_operator_api

logger = logging.getLogger(__name__)


def _build_servers(specs):
    servers, threads = [], []
    try:
        for name, app, host, port, max_body in specs:
            server = waitress.create_server(
                app, host=host, port=port, max_request_body_size=max_body
            )
            servers.append(server)
            threads.append(
                threading.Thread(
                    target=server.run, daemon=True, name=f"dicomhawk-{name}"
                )
            )
    except Exception:
        _stop_servers(servers, threads)
        raise
    try:
        for thread in threads:
            thread.start()
    except Exception:
        _stop_servers(servers, threads)
        raise
    return servers, threads


def _stop_servers(servers, threads):
    for server in servers:
        try:
            server.close()
            server.task_dispatcher.shutdown(cancel_pending=True, timeout=5)
        except Exception:
            logger.exception("Failed stopping a web listener")
    for thread in threads:
        if thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=5)
    servers.clear()
    threads.clear()


class WebComponent(Component):
    """Run the attacker and operator HTTP servers."""

    def __init__(
        self,
        profile: ProfileConfig,
        repo: Repository,
        bus: logging.Logger,
        host: str,
        web_port: int,
        operator_port: int,
        operator_host: str = "127.0.0.1",
        operator_token: str | None = None,
    ):
        self.profile = profile
        self.repo = repo
        self.bus = bus
        self.host = host
        self.web_port = web_port
        self.operator_port = operator_port
        self.operator_host = operator_host
        self.operator_token = operator_token
        self._servers = []
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._servers:
            return
        web_app = new_web(self.profile, self.repo, self.bus)
        operator_app = new_operator_api(self.profile, self.bus, self.operator_token)
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
        self._servers, self._threads = _build_servers(specs)
        logger.info(
            f"Web: {self.host}:{self.web_port}  Operator API: {self.operator_host}:{self.operator_port}"
        )

    def stop(self) -> None:
        _stop_servers(self._servers, self._threads)


class DicomWebComponent(Component):
    """Run one server per profile DICOMweb port."""

    def __init__(
        self,
        profile: ProfileConfig,
        repo: Repository,
        bus: logging.Logger,
        host: str,
    ):
        self.profile = profile
        self.repo = repo
        self.bus = bus
        self.host = host
        self._servers = []
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._servers:
            return
        apps = new_dicomweb(self.profile, self.repo, self.bus)
        specs = [
            (
                f"dicomweb-{port}",
                app,
                self.host,
                port,
                app.config["MAX_CONTENT_LENGTH"],
            )
            for port, app in apps.items()
        ]
        self._servers, self._threads = _build_servers(specs)
        logger.info(
            "DICOMweb: " + ", ".join(f"{self.host}:{port}" for port in sorted(apps))
        )

    def stop(self) -> None:
        _stop_servers(self._servers, self._threads)


def new_web_component(
    profile: ProfileConfig,
    repo: Repository,
    bus: logging.Logger,
    host: str,
    web_port: int,
    operator_port: int,
    operator_host: str = "127.0.0.1",
    operator_token: str | None = None,
) -> WebComponent:
    return WebComponent(
        profile,
        repo,
        bus,
        host,
        web_port,
        operator_port,
        operator_host,
        operator_token,
    )


def new_dicomweb_component(
    profile: ProfileConfig,
    repo: Repository,
    bus: logging.Logger,
    host: str,
) -> DicomWebComponent:
    return DicomWebComponent(profile, repo, bus, host)
