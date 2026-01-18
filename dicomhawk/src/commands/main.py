import typer
from .serve import serve_app

app = typer.Typer(help="DicomHawk")
app.add_typer(serve_app)

def main():
    app()
