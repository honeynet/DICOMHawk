import typer
from dicomhawk import new_dicomhawk

serve_app = typer.Typer(help="dicomhawk runner")

@serve_app.command()
def serve(
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
            "-id",
            "--impl_uid",
            help="Implementation UID"
        ),
        impl_name: str = typer.Option(
            "ORTHANC",
            "-in",
            "--impl_name",
            help="Implementation name"
        ),
        dimse: str | None = typer.Option(
            None,
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
    ):

    config = new_server_config(
        ports,
        ae_title,
        impl_uid,
        impl_name,
        dimse,
        database
    )

    hp = new_dicomhawk(config)
    hp.run()