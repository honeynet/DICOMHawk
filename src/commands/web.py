import typer
from web.server import main

web_app = typer.Typer(help="dicomhawk web logging server")

@web_app.command()
def serve(
        port: str = typer.Option(
            "5000",
            "-p",
            "--ports",
            help="Posts to listen for connections"
        ),
    ):

    main(port)