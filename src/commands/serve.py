import logging
import signal
import sys

import typer
from dicomhawk.app import new_dicomhawk
from dicomhawk.handlers import new_dimse_factory
from dicomhawk.repository import new_repo
from dicomhawk.server import new_config, new_server
from dicomhawk.bus import new_bus, new_dev_log, LevelColorFormatter
from dicomhawk.storage import new_store

from profiles.profile import load_profile
from web.component import new_dicomweb_component, new_web_component

logger = logging.getLogger(__name__)

serve_app = typer.Typer(help="dicomhawk runner")

# ACSE/connection lifecycle handlers are always on — `operations` only gates DIMSE ops.
_ALWAYS_ON_HANDLERS: tuple[str, ...] = (
    "associate",
    "reject",
    "release",
    "abort",
    "connect",
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
    backend_server: str | None = typer.Option(
        None,
        "--backend-server",
        envvar="DICOMHAWK_BACKEND_SERVER",
        help="Per-deployment X-Backendserver value for web profiles that expose it",
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
    if backend_server:
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

    dimse_fact = new_dimse_factory(repo, bus, prof.dicom.max_store_bytes)

    handlers = []
    for op in prof.dicom.operations:
        if h := dimse_fact.get(op):
            handlers.append(h)
    for always_on in _ALWAYS_ON_HANDLERS:
        if h := dimse_fact.get(always_on):
            handlers.append(h)

    components = []
    if prof.kind == "pacs" and prof.web.enabled:
        components.append(
            new_web_component(
                prof, repo, bus, host, web_port, operator_port, operator_host
            )
        )
    # DICOMweb ports/paths are profile fingerprint identity, not CLI flags; only --host is shared.
    if prof.kind == "pacs" and prof.dicomweb.enabled:
        components.append(new_dicomweb_component(prof, repo, bus, host))

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
