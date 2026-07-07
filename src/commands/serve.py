import logging
import sys

import typer
from dicomhawk.app import new_dicomhawk
from dicomhawk.handlers import new_dimse_factory
from dicomhawk.middlewares import Middleware, new_honeytoken_injector
from dicomhawk.repository import new_repo
from dicomhawk.server import new_config, new_server
from dicomhawk.bus import new_bus, new_dev_log, LevelColorFormatter
from dicomhawk.storage import new_store

from profiles.profile import load_profile

logger = logging.getLogger(__name__)

serve_app = typer.Typer(help="dicomhawk runner")

# ACSE/connection lifecycle handlers are always on — `operations` only gates DIMSE ops.
_ALWAYS_ON_HANDLERS: tuple[str, ...] = ("associate", "reject", "release", "abort", "connect")

@serve_app.command()
def serve(
        host: str = typer.Option(
            "0.0.0.0",
            "-h",
            "--host",
            help="Host addresses to listen for connections"
        ),
        ports: str = typer.Option(
            "104,11112",
            "-p",
            "--ports",
            help="Posts to listen for connections"
        ),
        profile: str | None = typer.Option(
            None,
            "--profile",
            help="Profile name (profiles/builtin/<name>.yaml) or path to a custom profile YAML; omit for generic behavior"
        ),
        ae_title: str | None = typer.Option(
            None,
            "-ae",
            "--ae_title",
            help="AE title (overrides the profile default)"
        ),
        max_associations: int | None = typer.Option(
            None,
            "-ma",
            "--max-associations",
            help="Maximum simultaneous associations (overrides the profile default)"
        ),
        database: str | None = typer.Option(
            None,
            "-db",
            "--database",
            envvar="DICOMHAWK_DB",
            help="path to database (defaults to $DICOMHAWK_DB)"
        ),
        log_path: str = typer.Option(
            "data/dicomhawk.log",
            "-l",
            "--log-path",
            help="path to the JSON event log file"
        ),
        traces : str = typer.Option(
            "traces",
            "-t",
            "--traces",
            envvar="DICOMHAWK_TRACES",
            help="Where to store traces (defaults to $DICOMHAWK_TRACES)"
        ),
        honey_url: str | None = typer.Option(
            None,
            "--honey-url",
            help="URL to inject as RetrieveURL for Honey URLs (overrides the profile default)"
        ),
        canary_pdf: str | None = typer.Option(
            None,
            "--canary-pdf",
            help="Path to an Encapsulated PDF Canary to inject into datasets (overrides the profile default)"
        ),
        dev_log_path: str | None = typer.Option(
            None,
            "--dev-log",
            help="Path to the developer/internal log file (Python warnings and errors)"
        ),
        verbose: bool = typer.Option(
            False,
            "--verbose",
            "-v",
            help="Print a compact colored event summary to stdout (auto-enabled when stdout is a TTY)"
        ),
    ):

    try:
        prof = load_profile(profile)
    except (FileNotFoundError, ValueError) as e:
        typer.secho(f"Error: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    p_int = [int(p) for p in ports.split(",")]
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
        max_associations or prof.dicom.max_associations,
        prof.dicom.max_pdu_size,
        require_called_aet=prof.dicom.ae_auth.require_called_aet,
        require_calling_aet=prof.dicom.ae_auth.require_calling_aet,
    )

    store = new_store(traces)

    injector = new_honeytoken_injector(
        honey_url or prof.operator.honey_url,
        canary_pdf or prof.operator.canary_pdf,
    )
    mws: list[Middleware] = [injector]

    repo = new_repo(database, store, mws)
    bus = new_bus(log_path, verbose=verbose)
    if dev_log_path:
        new_dev_log(dev_log_path)
    else:
        # No dev-log file: log to stdout so `docker logs` shows startup and errors.
        fmt, datefmt = "%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%dT%H:%M:%S"
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(
            LevelColorFormatter(fmt, datefmt) if sys.stdout.isatty()
            else logging.Formatter(fmt, datefmt)
        )
        logging.basicConfig(level=logging.INFO, handlers=[handler])
        logging.getLogger("pynetdicom").setLevel(logging.WARNING)

    logger.info(f"Profile: {prof.name} ({prof.manufacturer or 'generic'} {prof.model_name or ''})".strip())

    dimse_fact = new_dimse_factory(repo, bus)

    handlers = []
    for op in prof.dicom.operations:
        if h := dimse_fact.get(op):
            handlers.append(h)
    for always_on in _ALWAYS_ON_HANDLERS:
        if h := dimse_fact.get(always_on):
            handlers.append(h)

    srv = new_server(bus, config, handlers)
    hp = new_dicomhawk(srv, []) # TODO: fix this, add components?
    hp.start()
