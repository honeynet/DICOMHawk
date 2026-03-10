import typer
from dicomhawk.app import new_dicomhawk
from dicomhawk.handlers import new_dimse_factory
from dicomhawk.middlewares import Middleware, new_honeytoken_injector
from dicomhawk.repository import new_repo
from dicomhawk.server import new_config, new_server
from dicomhawk.bus import new_bus
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
        stix_out: str | None = typer.Option(
            None,
            "--stix-out",
            help="Directory to write STIX 2.1 Bundle JSON (NDJSON) output"
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
    
    injector = new_honeytoken_injector(honey_url, canary_pdf)
    mws: list[Middleware] = [injector]
    
    repo = new_repo(database, store, mws)

    stix_identity = None
    if stix_out:
        from dicomhawk.stix import new_honeypot_identity
        stix_identity = new_honeypot_identity(ae_title, impl_uid)

    bus = new_bus(log_path, stix_out=stix_out, stix_identity=stix_identity)

    dimse_fact = new_dimse_factory(repo, bus)

    handlers = []
    for h in dimse.split(","):
        if handler:=dimse_fact.get(h):
            handlers.append(handler)

    srv = new_server(bus, config, handlers)
    hp = new_dicomhawk(srv, []) # TODO: fix this, add components?
    hp.start()