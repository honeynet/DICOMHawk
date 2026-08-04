import ipaddress
import logging
import signal
import sys

import typer
from analysis.component import new_analysis_component
from analysis.config import new_analysis_config
from dicomhawk.app import new_dicomhawk
from dicomhawk.handlers import new_dimse_factory
from dicomhawk.repository import new_repo
from dicomhawk.server import new_config, new_server
from dicomhawk.bus import new_bus, new_dev_log, LevelColorFormatter
from dicomhawk.storage import new_store
from fingerprint.component import new_fingerprint_component
from fingerprint.config import new_fingerprint_config

from profiles.profile import load_profile
from web.component import new_dicomweb_component, new_web_component

logger = logging.getLogger(__name__)

serve_app = typer.Typer(help="dicomhawk runner")

# ACSE/connection lifecycle handlers are always on; `operations` only gates DIMSE ops.
_ALWAYS_ON_HANDLERS: tuple[str, ...] = (
    "associate",
    "reject",
    "release",
    "abort",
    "connect",
)


# Over plain HTTP a browser discards the Secure session cookie, so the decoy login silently resets.
def _warn_unusable_login(prof, trusted_proxy: str | None) -> None:
    web = prof.web
    if not web.secure_cookies:
        return
    if web.grant_access == "none":
        return
    # Only a trusted proxy forwarding the scheme makes the arriving request itself https.
    if trusted_proxy:
        return
    logger.warning(
        "web.secure_cookies is on without TLS in front: browsers will drop the session cookie "
        "over plain HTTP and the decoy login will silently fail. Set DICOMHAWK_SECURE_COOKIES=false "
        "for a plaintext deployment, or terminate TLS and set --trusted-proxy/--public-base-url."
    )


@serve_app.command()
def serve(
    host: str = typer.Option(
        "0.0.0.0", "-h", "--host", help="Host addresses to listen for connections"
    ),
    ports: str = typer.Option(
        "104,11112", "-p", "--ports", help="Ports to listen for connections"
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Profile name (profiles/<name>/<name>.yaml) or path to a custom profile YAML; omit for generic behavior",
    ),
    ae_title: str | None = typer.Option(
        None, "-ae", "--ae_title", help="AE title (overrides the profile default)"
    ),
    max_associations: int | None = typer.Option(
        None,
        "-ma",
        "--max-associations",
        help="Maximum simultaneous associations (overrides the profile default)",
    ),
    database: str | None = typer.Option(
        None,
        "-db",
        "--database",
        envvar="DICOMHAWK_DB",
        help="path to database (defaults to $DICOMHAWK_DB)",
    ),
    log_path: str = typer.Option(
        "data/dicomhawk.log", "-l", "--log-path", help="path to the JSON event log file"
    ),
    log_max_bytes: int = typer.Option(
        50 * 1024 * 1024,
        "--log-max-bytes",
        help="Rotate the JSON event log after this many bytes (0 disables rotation)",
    ),
    log_backups: int = typer.Option(
        5,
        "--log-backups",
        help="Number of rotated JSON event logs to retain",
    ),
    traces: str = typer.Option(
        "traces",
        "-t",
        "--traces",
        envvar="DICOMHAWK_TRACES",
        help="Where to store traces (defaults to $DICOMHAWK_TRACES)",
    ),
    dev_log_path: str | None = typer.Option(
        None,
        "--dev-log",
        help="Path to the developer/internal log file (Python warnings and errors)",
    ),
    web_port: int = typer.Option(
        8080,
        "--web-port",
        help="Port for the attacker-facing web UI (pacs profiles with web.enabled only)",
    ),
    operator_port: int = typer.Option(
        8081, "--operator-port", help="Port for the operator API"
    ),
    operator_host: str = typer.Option(
        "127.0.0.1",
        "--operator-host",
        help="Bind address for the operator API (loopback-only by default; Docker needs 0.0.0.0 here, see docs/commands.md)",
    ),
    operator_token: str | None = typer.Option(
        None,
        "--operator-token",
        envvar="DICOMHAWK_OPERATOR_TOKEN",
        help="Optional password/Bearer token protecting the operator API and dashboard",
    ),
    allow_remote_operator: bool = typer.Option(
        False,
        "--allow-remote-operator",
        help="Explicitly permit a non-loopback operator bind (needed inside Docker)",
    ),
    trusted_proxy: str | None = typer.Option(
        None,
        "--trusted-proxy",
        envvar="DICOMHAWK_TRUSTED_PROXY",
        help="Exact reverse-proxy IP trusted to supply forwarded client identity for attacker-facing HTTP",
    ),
    backend_server: str | None = typer.Option(
        None,
        "--backend-server",
        envvar="DICOMHAWK_BACKEND_SERVER",
        help="Per-deployment X-Backendserver value for web profiles that expose it",
    ),
    secure_cookies: bool | None = typer.Option(
        None,
        "--secure-cookies/--no-secure-cookies",
        envvar="DICOMHAWK_SECURE_COOKIES",
        help="Override the profile's Secure cookie flag; browsers drop Secure cookies over plain HTTP",
    ),
    public_base_url: str | None = typer.Option(
        None,
        "--public-base-url",
        envvar="DICOMHAWK_PUBLIC_BASE_URL",
        help="External HTTP(S) origin used in generated OIDC redirect URIs",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Print a compact colored event summary to stdout (auto-enabled when stdout is a TTY)",
    ),
    analysis: bool = typer.Option(
        True,
        "--analysis/--no-analysis",
        envvar="DICOMHAWK_ANALYSIS",
        help="Run captured payloads through the static analysis pipeline",
    ),
    analysis_db: str = typer.Option(
        "analysis.db",
        "--analysis-db",
        envvar="DICOMHAWK_ANALYSIS_DB",
        help="SQLite path for the durable artifact/analysis-job table",
    ),
    analysis_rules: str | None = typer.Option(
        None,
        "--analysis-rules",
        envvar="DICOMHAWK_ANALYSIS_RULES",
        help="Directory of additional operator .yar files (beyond the shipped starters)",
    ),
    analysis_timeout: float = typer.Option(
        10.0,
        "--analysis-timeout",
        envvar="DICOMHAWK_ANALYSIS_TIMEOUT",
        help="Hard wall-clock deadline per analysis job, in seconds",
    ),
    analysis_max_bytes: int = typer.Option(
        64 * 1024 * 1024,
        "--analysis-max-bytes",
        envvar="DICOMHAWK_ANALYSIS_MAX_BYTES",
        help="Bounded read/extraction cap per analyzed capture",
    ),
    analysis_queue_size: int = typer.Option(
        256,
        "--analysis-queue-size",
        envvar="DICOMHAWK_ANALYSIS_QUEUE_SIZE",
        help="In-memory wake-up queue bound; the durable job table is the source of truth",
    ),
    fingerprint: bool = typer.Option(
        True,
        "--fingerprint/--no-fingerprint",
        envvar="DICOMHAWK_FINGERPRINT",
        help="Serve the browser fingerprint collector on profiles whose web.fingerprint is enabled",
    ),
    fingerprint_db: str = typer.Option(
        "fingerprint.db",
        "--fingerprint-db",
        envvar="DICOMHAWK_FINGERPRINT_DB",
        help="SQLite path for collected browser fingerprints (its own store, separate from every other)",
    ),
    fingerprint_max_bytes: int = typer.Option(
        64 * 1024,
        "--fingerprint-max-bytes",
        envvar="DICOMHAWK_FINGERPRINT_MAX_BYTES",
        help="Hard cap on one collector submission body",
    ),
    fingerprint_max_per_session: int = typer.Option(
        20,
        "--fingerprint-max-per-session",
        envvar="DICOMHAWK_FINGERPRINT_MAX_PER_SESSION",
        help="Submissions stored per web session before further ones are dropped",
    ),
):

    try:
        prof = load_profile(profile)
    except (FileNotFoundError, ValueError) as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    try:
        p_int = [int(p.strip()) for p in ports.split(",") if p.strip()]
    except ValueError:
        raise typer.BadParameter("ports must be a comma-separated list of integers")
    if not p_int or any(port < 1 or port > 65535 for port in p_int):
        raise typer.BadParameter("ports must contain values from 1 to 65535")
    if max_associations is not None and max_associations < 1:
        raise typer.BadParameter("max-associations must be positive")
    if log_max_bytes < 0 or log_backups < 0:
        raise typer.BadParameter("log-max-bytes and log-backups cannot be negative")
    if log_max_bytes and log_backups < 1:
        raise typer.BadParameter("rotating logs require at least one backup")
    if analysis_timeout <= 0:
        raise typer.BadParameter("analysis-timeout must be positive")
    if analysis_max_bytes < 1:
        raise typer.BadParameter("analysis-max-bytes must be positive")
    if analysis_queue_size < 1:
        raise typer.BadParameter("analysis-queue-size must be positive")
    if fingerprint_max_bytes < 1:
        raise typer.BadParameter("fingerprint-max-bytes must be positive")
    if fingerprint_max_per_session < 1:
        raise typer.BadParameter("fingerprint-max-per-session must be positive")
    try:
        operator_is_loopback = (
            operator_host == "localhost"
            or ipaddress.ip_address(operator_host).is_loopback
        )
    except ValueError:
        operator_is_loopback = False
    if not operator_is_loopback and not allow_remote_operator:
        raise typer.BadParameter(
            "a non-loopback operator-host requires --allow-remote-operator"
        )
    if trusted_proxy:
        try:
            ipaddress.ip_address(trusted_proxy)
        except ValueError as exc:
            raise typer.BadParameter(
                "trusted-proxy must be one exact IP address"
            ) from exc
    if secure_cookies is not None:
        prof.web.secure_cookies = secure_cookies
    # Only override a header the profile already ships; injecting it elsewhere leaks one vendor into another.
    if backend_server and "X-Backendserver" in prof.web.headers:
        prof.web.headers["X-Backendserver"] = backend_server
    if public_base_url:
        from urllib.parse import urlsplit

        parsed = urlsplit(public_base_url)
        try:
            valid_port = parsed.port is None or 1 <= parsed.port <= 65535
        except ValueError:
            valid_port = False
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or not valid_port
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise typer.BadParameter(
                "public-base-url must be an HTTP(S) origin without a path"
            )
        prof.web.public_base_url = public_base_url.rstrip("/")
    config = new_config(
        host,
        p_int,
        ae_title or prof.ae_title,
        prof.implementation_class_uid,
        prof.implementation_version_name,
        prof.dicom.operations,
        prof.dicom.verification,
        prof.dicom.storage_classes,
        prof.dicom.qr_classes,
        prof.dicom.max_associations if max_associations is None else max_associations,
        prof.dicom.max_pdu_size,
        require_called_aet=prof.dicom.ae_auth.require_called_aet,
        require_calling_aet=prof.dicom.ae_auth.require_calling_aet,
        acse_timeout=prof.dicom.acse_timeout,
        network_timeout=prof.dicom.network_timeout,
        dimse_timeout=prof.dicom.dimse_timeout,
    )

    store = new_store(traces)
    repo = new_repo(database, store)
    bus = new_bus(
        log_path,
        size=log_max_bytes or None,
        backups=log_backups,
        verbose=verbose,
    )
    if dev_log_path:
        new_dev_log(dev_log_path)
    else:
        # No dev-log file: log to stdout so `docker logs` shows startup and errors.
        fmt, datefmt = (
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            "%Y-%m-%dT%H:%M:%S",
        )
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            LevelColorFormatter(fmt, datefmt)
            if sys.stdout.isatty()
            else logging.Formatter(fmt, datefmt)
        )
        logging.basicConfig(level=logging.INFO, handlers=[handler])
        logging.getLogger("pynetdicom").setLevel(logging.WARNING)

    logger.info(
        f"Profile: {prof.name} ({prof.manufacturer or 'generic'} {prof.model_name or ''})".strip()
    )
    logger.info(
        "DIMSE limits: associations=%s acse=%s network=%s dimse=%s store_bytes=%s",
        config.MAX_ASSOC,
        config.ACSE_TIMEOUT,
        config.NETWORK_TIMEOUT,
        config.DIMSE_TIMEOUT,
        prof.dicom.max_store_bytes,
    )
    if trusted_proxy:
        logger.info("Trusted HTTP proxy: %s", trusted_proxy)

    components = []
    sink = None
    analysis_store = None
    if analysis:
        analysis_component = new_analysis_component(
            new_analysis_config(
                db_path=analysis_db,
                rules_dir=analysis_rules,
                timeout=analysis_timeout,
                max_bytes=analysis_max_bytes,
                queue_size=analysis_queue_size,
            ),
            bus,
        )
        # First in `components` -> starts before ingress listeners, stops after they close.
        components.append(analysis_component)
        sink = analysis_component.sink
        analysis_store = analysis_component.store
        logger.info("Analysis: enabled, rules=%s", analysis_rules or "shipped starters only")
    else:
        logger.info("Analysis: disabled (--no-analysis)")

    fingerprint_sink = None
    fingerprint_store = None
    if not fingerprint:
        # The flag overrides the profile, so no collector is served and no route is registered.
        prof.web.fingerprint.enabled = False
    # Only build the store when a profile actually serves a collector, so nothing is created unused.
    if fingerprint and prof.kind == "pacs" and prof.web.enabled and prof.web.fingerprint.enabled:
        fingerprint_component = new_fingerprint_component(
            new_fingerprint_config(
                db_path=fingerprint_db,
                max_body_bytes=fingerprint_max_bytes,
                max_per_session=fingerprint_max_per_session,
            )
        )
        components.append(fingerprint_component)
        fingerprint_sink = fingerprint_component.sink
        fingerprint_store = fingerprint_component.store
        logger.info(
            "Fingerprinting: signals=%s", ",".join(prof.web.fingerprint.signals)
        )
    else:
        logger.info("Fingerprinting: disabled")

    dimse_fact = new_dimse_factory(repo, bus, prof.dicom.max_store_bytes, sink=sink)

    handlers = []
    for op in prof.dicom.operations:
        if h := dimse_fact.get(op):
            handlers.append(h)
    for always_on in _ALWAYS_ON_HANDLERS:
        if h := dimse_fact.get(always_on):
            handlers.append(h)

    if prof.kind == "pacs" and prof.web.enabled:
        _warn_unusable_login(prof, trusted_proxy)
        components.append(
            new_web_component(
                prof,
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
        )
    # DICOMweb ports/paths are profile fingerprint identity, not CLI flags; only --host is shared.
    if prof.kind == "pacs" and prof.dicomweb.enabled:
        components.append(
            new_dicomweb_component(prof, repo, bus, host, trusted_proxy, sink)
        )

    srv = new_server(bus, config, handlers)
    hp = new_dicomhawk(srv, components)

    def stop_honeypot(_signum=None, _frame=None):
        hp.stop()

    previous_handlers = {}
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[sig] = signal.signal(sig, stop_honeypot)
    try:
        hp.start()
    finally:
        hp.stop()
        repo.stop()
        for sig, previous in previous_handlers.items():
            signal.signal(sig, previous)
