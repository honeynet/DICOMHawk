import typer

from .component import component_app
from .seed import seed_app
from .serve import serve_app

app = typer.Typer(help="DicomHawk")
app.add_typer(serve_app)
app.add_typer(component_app)
app.add_typer(seed_app)

def main():
    app()
