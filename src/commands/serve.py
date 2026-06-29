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

serve_app = typer.Typer(help="dicomhawk runner")

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
        ae_title: str = typer.Option(
            "ORTHANC",
            "-ae",
            "--ae_title",
            help="AE title"
        ),
        impl_uid: str = typer.Option(
            "1.2.3.4", # TODO: fix this
            "-uid",
            "--impl_uid",
            help="Implementation UID"
        ),
        impl_name: str = typer.Option(
            "ORTHANC",
            "-name",
            "--impl_name",
            help="Implementation name"
        ),
        dimse: str = typer.Option(
            "associate,echo,get,find,move,store,release,abort",
            "-d",
            "--dimse",
            help="DIMSE operations supported"
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
            help="URL to inject as RetrieveURL for Honey URLs"
        ),
        canary_pdf: str | None = typer.Option(
            None,
            "--canary-pdf",
            help="Path to an Encapsulated PDF Canary to inject into datasets"
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

    p_int = [int(p) for p in ports.split(",")]
    config = new_config(
        host,
        p_int,
        ae_title,
        impl_uid,
        impl_name,
    )

    store = new_store(traces)

    injector = new_honeytoken_injector(honey_url, canary_pdf)
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

    dimse_fact = new_dimse_factory(repo, bus)

    handlers = []
    for h in dimse.split(","):
        if h == "connect":
            continue  # always-on, registered below
        if handler:=dimse_fact.get(h):
            handlers.append(handler)

    # Always on: captures probes that never negotiate an association.
    if conn := dimse_fact.get("connect"):
        handlers.append(conn)

    srv = new_server(bus, config, handlers)
    hp = new_dicomhawk(srv, []) # TODO: fix this, add components?
    hp.start()