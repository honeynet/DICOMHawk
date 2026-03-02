import typer
from .serve import serve_app
from .web import web_app

app = typer.Typer(help="DicomHawk")
app.add_typer(serve_app)
app.add_typer(web_app)

def main():
    app()
