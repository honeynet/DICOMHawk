import signal
import typer

from dicomhawk.repository import new_repo
from dicomhawk.seeder import OsmClient, SeedScheduler, load_locations, new_seeder
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
        envvar="DICOMHAWK_DB",
        help="SQLite path; defaults to $DICOMHAWK_DB so seed and serve share one DB",
    ),
    traces: str = typer.Option(
        "traces",
        "-t",
        "--traces",
        envvar="DICOMHAWK_TRACES",
        help="Traces directory; defaults to $DICOMHAWK_TRACES so seed and serve share it",
    ),
    locations: str | None = typer.Option(
        None,
        "-L",
        "--locations",
        help='Path to a JSON file of locations: [{"institution": "...", "address": "..."}]',
    ),
    locale: str = typer.Option(
        "en_US",
        "--locale",
        help="Faker locale for patient and physician name generation (e.g. en_US, de_DE, ja_JP)",
    ),
    osm_city: str | None = typer.Option(
        None,
        "--osm-city",
        help="City name to query OpenStreetMap for real hospital names (e.g. 'New York')",
    ),
    osm_country: str | None = typer.Option(
        None,
        "--osm-country",
        help="ISO 3166-1 alpha-2 country code to query OpenStreetMap (e.g. 'US', 'DE')",
    ),
    osm_cache: str | None = typer.Option(
        None,
        "--osm-cache",
        help="Path for the OSM institution cache file (default: ~/.cache/dicomhawk/osm.json)",
    ),
    osm_max: int = typer.Option(
        50,
        "--osm-max",
        help="Maximum number of institutions to fetch from OpenStreetMap",
    ),
    modality: str = typer.Option(
        "CT",
        "-m",
        "--modality",
        help="DICOM modality to request from TCIA (e.g. CT, MR, US, DX)",
    ),
    interval: int = typer.Option(
        0,
        "-i",
        "--interval",
        help="Re-seed every N minutes in the background (0 = run once and exit)",
    ),
):
    # Resolve location list: OSM > --locations file > built-in defaults
    if osm_city or osm_country:
        osm = OsmClient(city=osm_city, country=osm_country, cache_path=osm_cache, max_results=osm_max)
        osm_locs = osm.get_locations()
        if osm_locs:
            typer.echo(f"Fetched {len(osm_locs)} institutions from OpenStreetMap")
        else:
            typer.echo("OpenStreetMap returned no institutions; using built-in defaults")
        loc_list = osm_locs or load_locations(locations)
    else:
        loc_list = load_locations(locations)

    store = new_store(traces)
    repo = new_repo(database, store, [])
    repo.start()

    try:
        seeder = new_seeder(repo, locations=loc_list, locale=locale)

        if interval > 0:
            scheduler = SeedScheduler(seeder, collection, interval, max_series, max_images, modality)
            scheduler.start()
            typer.echo(f"Scheduler running — seeding every {interval}m ({modality}). Press Ctrl+C to stop.")

            stop_event = scheduler._stop
            try:
                signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
                stop_event.wait()
            except KeyboardInterrupt:
                pass
            finally:
                scheduler.stop()
                scheduler.join(timeout=5)
        else:
            n = seeder.seed(collection, max_series, max_images, modality)
            typer.echo(f"Seeded {n} instances from '{collection}' ({modality})")
    finally:
        repo.stop()
