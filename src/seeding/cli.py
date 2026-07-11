import signal
import typer

from dicomhawk.repository import new_repo
from dicomhawk.storage import new_store
from honeytoken.injector import new_honeytoken_injector

from .config import load_seeding_config
from .locations import load_locations
from .names import load_name_pools
from .osm import OsmClient
from .seeder import SeedScheduler, new_seeder, resolve_rotation

seed_app = typer.Typer(help="Seed the honeypot database with realistic DICOM data from TCIA")


@seed_app.command()
def seed(
    collection: str = typer.Option(
        "TCGA-LUAD",
        "-c",
        "--collection",
        help="TCIA collection name(s), comma-separated; with --rotate one is chosen per ISO week",
    ),
    max_series: int = typer.Option(
        3,
        "-s",
        "--max-series",
        help="Maximum number of series to download from the collection",
    ),
    max_images: int = typer.Option(
        30,
        "-n",
        "--max-images",
        help="Images to ingest per series. Higher = more realistic IMAGE-level C-FIND "
             "responses (real CT series have 100s of slices); lower = less storage/bandwidth",
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
    names: str | None = typer.Option(
        None,
        "-N",
        "--names",
        help='Path to a JSON file of name pools: {"male": ["Family^Given"], "female": [...], '
             '"physician": [...]}. Overrides --locale generation',
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
        help="DICOM modality/modalities, comma-separated; with --rotate one is chosen per ISO week",
    ),
    rotate: bool = typer.Option(
        True,
        "--rotate/--no-rotate",
        help="Rotate patient identities (by ISO week) and source collection/modality for variety "
             "on repeated seeding. --no-rotate keeps the fully deterministic behaviour",
    ),
    epoch: str | None = typer.Option(
        None,
        "--epoch",
        hidden=True,
        help="Override the rotation epoch (advanced; for reproducible runs/tests)",
    ),
    interval: int = typer.Option(
        0,
        "-i",
        "--interval",
        help="Re-seed every N minutes in the background (0 = run once and exit)",
    ),
    honey_url: str | None = typer.Option(
        None,
        "--honey-url",
        help="URL baked as RetrieveURL into one seeded instance per run (overrides seeding/config.yaml)",
    ),
    canary_pdf: str | None = typer.Option(
        None,
        "--canary-pdf",
        help="Path to a PDF canary token baked into one seeded instance per run (overrides seeding/config.yaml)",
    ),
):
    collections = [c.strip() for c in collection.split(",") if c.strip()]
    modalities = [m.strip() for m in modality.split(",") if m.strip()]
    # Empty lists would IndexError/ZeroDivisionError deep in resolve_rotation instead.
    if not collections:
        raise typer.BadParameter("must not be empty", param_hint="--collection")
    if not modalities:
        raise typer.BadParameter("must not be empty", param_hint="--modality")

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
    repo = new_repo(database, store)
    repo.start()

    honeytoken_cfg = load_seeding_config()["honeytoken"]
    resolved_honey_url = honey_url or honeytoken_cfg["honey_url"]
    resolved_canary_pdf = canary_pdf or honeytoken_cfg["canary_pdf"]
    injector = (
        new_honeytoken_injector(resolved_honey_url, resolved_canary_pdf)
        if resolved_honey_url or resolved_canary_pdf else None
    )

    try:
        seeder = new_seeder(
            repo, locations=loc_list, locale=locale, name_pools=load_name_pools(names),
            honeytoken=injector,
        )

        if interval > 0:
            scheduler = SeedScheduler(seeder, collections, interval, max_series, max_images, modalities, rotate)
            scheduler.start()
            typer.echo(
                f"Scheduler running — seeding every {interval}m "
                f"(rotate={rotate}). Press Ctrl+C to stop."
            )

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
            coll, mod, ep = resolve_rotation(collections, modalities, rotate, epoch)
            n = seeder.seed(coll, max_series, max_images, mod, ep)
            typer.echo(f"Seeded {n} instances from '{coll}' ({mod})")
    finally:
        repo.stop()
