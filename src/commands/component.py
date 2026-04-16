import os
import typer

from dicomhawk.middlewares import new_honeytoken_injector
from pydicom import dcmread
from glob import glob
from pathlib import Path

component_app = typer.Typer(help="dicomhawk runner")

@component_app.command(
        help="This commands injects a honeytoken and a honeyurl into one or more DICOM files. Visit https://canarytokens.org/nest/generate to generate yours (URL and PDF)"
    )
def honey(
        fpath: str = typer.Option(
            "*.dcm",
            "-f",
            "--file_path",
            help="Glob to file or directory with DICOM files. Defaults to all DICOM files in the current directory"
        ),
        htoken: str | None = typer.Option(
            None,
            "-ht",
            "--honey_token",
            help="Path to PDF template Honeytoken (i.e., canary token)"
        ),
        hurl: str | None = typer.Option(
            None,
            "-hu",
            "--honey_url",
            help="Honey URL to inject as RetrieveURL"
        ),
        output: str = typer.Option(
            ".",
            "-o",
            "--output",
            help="Output directory"
        ),
        suffix: str | None = typer.Option(
            "ht",
            "-s",
            "--sufix",
            help="Suffix for the output"
        ),
    ):
    for p in glob(fpath, recursive=True):
        try:
            ds = dcmread(p)
            hti = new_honeytoken_injector(hurl, htoken)
            ds = hti(ds)
            name = Path(p).name
            if suffix:
                name = name + "." + suffix
            ds.save_as(os.path.join(output, name))
        except Exception as e:
            print(f"failed to inject token into {Path(p).name}")
            raise e