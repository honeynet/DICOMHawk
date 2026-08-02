import logging
import re
import threading
import time

import waitress

from analysis.store import AnalysisStore
from dicomhawk.component import Component
from dicomhawk.repository import Repository
from dicomhawk.storage import ArtifactSink
from profiles.profile import ProfileConfig

from .app import new_web
from .dicomweb import new_dicomweb
from .operator_api import new_operator_api

logger = logging.getLogger(__name__)
_QUEUE_DEPTH = re.compile(r"^Task queue depth is (\d+)$")


class _QueueDepthFilter(logging.Filter):
    """Keep queue-pressure milestones without logging every queued request."""

    def __init__(self, quiet_seconds: float = 10.0, clock=time.monotonic):
        super().__init__()
        self.quiet_seconds = quiet_seconds
        self.clock = clock
        self.last_seen = 0.0
        self.next_depth = 1
        self.lock = threading.Lock()

    @staticmethod
    def _next_milestone(depth: int) -> int:
        for milestone in (5, 10, 25, 50, 100):
            if depth < milestone:
                return milestone
        return 2 ** depth.bit_length()

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "waitress.queue":
            return True
        match = _QUEUE_DEPTH.match(record.getMessage())
        if match is None:
            return True
        depth = int(match.group(1))
        now = self.clock()
        with self.lock:
            if now - self.last_seen >= self.quiet_seconds:
                self.next_depth = 1
            self.last_seen = now
            if depth < self.next_depth:
                return False
            self.next_depth = self._next_milestone(depth)
            return True


_queue_depth_filter = _QueueDepthFilter()


def _install_waitress_queue_filter() -> None:
    queue_logger = logging.getLogger("waitress.queue")
    if _queue_depth_filter not in queue_logger.filters:
        queue_logger.addFilter(_queue_depth_filter)


def _build_servers(specs, trusted_proxy=None):
    _install_waitress_queue_filter()
    servers, threads = [], []
    try:
        for name, app, host, port, max_body, proxied in specs:
            proxy_options = {}
            if proxied and trusted_proxy:
                proxy_options = {
                    "trusted_proxy": trusted_proxy,
                    "trusted_proxy_count": 1,
                    "trusted_proxy_headers": {
                        "x-forwarded-for",
                        "x-forwarded-host",
                        "x-forwarded-port",
                        "x-forwarded-proto",
                    },
                    "clear_untrusted_proxy_headers": True,
                }
            server = waitress.create_server(
                app,
                host=host,
                port=port,
                max_request_body_size=max_body,
                **proxy_options,
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
        trusted_proxy: str | None = None,
        sink: ArtifactSink | None = None,
        analysis_store: AnalysisStore | None = None,
        fingerprint_sink=None,
        fingerprint_store=None,
    ):
        self.profile = profile
        self.repo = repo
        self.bus = bus
        self.host = host
        self.web_port = web_port
        self.operator_port = operator_port
        self.operator_host = operator_host
        self.operator_token = operator_token
        self.trusted_proxy = trusted_proxy
        self.sink = sink
        self.analysis_store = analysis_store
        self.fingerprint_sink = fingerprint_sink
        self.fingerprint_store = fingerprint_store
        self._servers = []
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._servers:
            return
        web_app = new_web(
            self.profile, self.repo, self.bus, self.sink, self.fingerprint_sink
        )
        operator_app = new_operator_api(
            self.profile,
            self.bus,
            self.operator_token,
            self.analysis_store,
            self.fingerprint_store,
        )
        specs = (
            (
                "web",
                web_app,
                self.host,
                self.web_port,
                self.profile.web.max_request_bytes,
                True,
            ),
            (
                "operator",
                operator_app,
                self.operator_host,
                self.operator_port,
                1_048_576,
                False,
            ),
        )
        self._servers, self._threads = _build_servers(specs, self.trusted_proxy)
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
        trusted_proxy: str | None = None,
        sink: ArtifactSink | None = None,
    ):
        self.profile = profile
        self.repo = repo
        self.bus = bus
        self.host = host
        self.trusted_proxy = trusted_proxy
        self.sink = sink
        self._servers = []
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._servers:
            return
        apps = new_dicomweb(self.profile, self.repo, self.bus, self.sink)
        specs = [
            (
                f"dicomweb-{port}",
                app,
                self.host,
                port,
                app.config["MAX_CONTENT_LENGTH"],
                True,
            )
            for port, app in apps.items()
        ]
        self._servers, self._threads = _build_servers(specs, self.trusted_proxy)
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
    trusted_proxy: str | None = None,
    sink: ArtifactSink | None = None,
    analysis_store: AnalysisStore | None = None,
    fingerprint_sink=None,
    fingerprint_store=None,
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
        trusted_proxy,
        sink,
        analysis_store,
        fingerprint_sink,
        fingerprint_store,
    )


def new_dicomweb_component(
    profile: ProfileConfig,
    repo: Repository,
    bus: logging.Logger,
    host: str,
    trusted_proxy: str | None = None,
    sink: ArtifactSink | None = None,
) -> DicomWebComponent:
    return DicomWebComponent(profile, repo, bus, host, trusted_proxy, sink)
