import typer
from dicomhawk.app import new_dicomhawk
from dicomhawk.handlers import new_dimse_factory
from dicomhawk.middlewares import Middleware
from dicomhawk.repository import new_repo
from dicomhawk.server import new_config, new_server
from dicomhawk.event_logger import new_bus
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
            help="path to database"
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
            help="Where to store traces (i.e., DICOM files and uploaded payloads)"
        )
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
    mws: list[Middleware] = [] # TODO: fix this, add a middleware to inject the honeytoken
    repo = new_repo(database, store, mws)
    bus = new_bus(log_path)

    dimse_fact = new_dimse_factory(repo, bus)

    handlers = []
    for h in dimse.split(","):
        if handler:=dimse_fact.get(h):
            handlers.append(handler)

    srv = new_server(bus, config, handlers)
    hp = new_dicomhawk(srv, []) # TODO: fix this, add components?
    hp.start()