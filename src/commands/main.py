import typer
from .serve import serve_app
from .component import component_app

app = typer.Typer(help="DicomHawk")
app.add_typer(serve_app)
app.add_typer(component_app)

def main():
    app()
