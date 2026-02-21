import typer
from .serve import serve_app
from .web import web_app

app = typer.Typer(help="DicomHawk")
app.add_typer(serve_app, name="dicom")   # dicomhawk dicom serve
app.add_typer(web_app, name="web")       # dicomhawk web serve

def main():
    app()
