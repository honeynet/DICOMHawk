import typer

from dicomhawk.repository import new_repo
from dicomhawk.seeder import new_seeder
from dicomhawk.storage import new_store

seed_app = typer.Typer(help="Seed the honeypot database with realistic DICOM data from TCIA")


@seed_app.command()
def seed(
    collection: str = typer.Option(
        "TCGA-LUAD",
        "-c",
        "--collection",
        help="TCIA collection name to pull from (see cancerimagingarchive.net)",
    ),
    max_series: int = typer.Option(
        3,
        "-s",
        "--max-series",
        help="Maximum number of series to download from the collection",
    ),
    max_images: int = typer.Option(
        5,
        "-n",
        "--max-images",
        help="Maximum number of images to ingest per series",
    ),
    database: str | None = typer.Option(
        None,
        "-db",
        "--database",
        help="Path to the SQLite database (must match the path used by dicomhawk serve)",
    ),
    traces: str = typer.Option(
        "traces",
        "-t",
        "--traces",
        help="Traces directory (must match the path used by dicomhawk serve)",
    ),
):
    store = new_store(traces)
    repo = new_repo(database, store, [])
    repo.start()

    try:
        seeder = new_seeder(repo)
        n = seeder.seed(collection, max_series, max_images)
        typer.echo(f"Seeded {n} instances from '{collection}'")
    finally:
        repo.stop()
