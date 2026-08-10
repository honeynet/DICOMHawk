import typer

from seeding.cli import seed_app
from honeytoken.cli import honey_app

from .serve import serve_app

app = typer.Typer(help="DicomHawk")
app.add_typer(serve_app)
app.add_typer(honey_app)
app.add_typer(seed_app)


def main():
    app()
