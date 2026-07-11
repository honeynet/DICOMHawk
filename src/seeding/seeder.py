import io
import logging
import random
import threading
from datetime import date

from pydicom import dcmread

from dicomhawk.repository import Repository
from honeytoken.injector import Middleware

from .fallback import load_fallback_datasets
from .locations import Location, _DEFAULT_LOCATIONS
from .names import NamePools, _patch_location, faker_pools
from .tcia import TciaClient

logger = logging.getLogger(__name__)


def resolve_rotation(
    collections: list[str],
    modalities: list[str],
    rotate: bool,
    epoch: str | None = None,
) -> tuple[str, str, str]:
    # Rotate off: deterministic (first entries, no epoch). Rotate on: source + identity
    # salt both change by ISO week, so a stateless weekly cron stays fresh but idempotent in-week.
    if not rotate:
        return collections[0], modalities[0], ""
    year, week, _ = date.today().isocalendar()
    epoch = epoch if epoch is not None else f"{year}W{week:02d}"
    return collections[week % len(collections)], modalities[week % len(modalities)], epoch


class SeedScheduler(threading.Thread):
    """Daemon thread that re-seeds the honeypot on a fixed interval."""

    def __init__(
        self,
        seeder: "Seeder",
        collections: list[str],
        interval_minutes: int,
        max_series: int = 3,
        max_images: int = 30,
        modalities: list[str] | None = None,
        rotate: bool = True,
    ):
        super().__init__(daemon=True, name="dicomhawk-seeder")
        self._seeder = seeder
        self._collections = collections
        self._modalities = modalities or ["CT"]  # only cli.py's caller-validated lists reach here
        self._interval = interval_minutes * 60
        self._max_series = max_series
        self._max_images = max_images
        self._rotate = rotate
        self._stop = threading.Event()

    def run(self) -> None:
        logger.info(
            f"Seed scheduler started — interval: {self._interval // 60}m, "
            f"collections: {self._collections}, modalities: {self._modalities}, "
            f"rotate: {self._rotate}"
        )
        while not self._stop.wait(self._interval):
            collection, modality, epoch = resolve_rotation(
                self._collections, self._modalities, self._rotate
            )
            n = self._seeder.seed(collection, self._max_series, self._max_images, modality, epoch)
            logger.info(f"Scheduled seed completed: {n} instances stored")

    def stop(self) -> None:
        self._stop.set()


class Seeder:
    def __init__(
        self,
        repo: Repository,
        locations: list[Location] | None = None,
        locale: str = "en_US",
        name_pools: NamePools | None = None,
        honeytoken: Middleware | None = None,
    ):
        self._repo = repo
        self._client = TciaClient()
        self._locations = locations if locations else _DEFAULT_LOCATIONS
        self._male_pool, self._female_pool, self._physician_pool = (
            name_pools or faker_pools(locale)
        )
        self._honeytoken = honeytoken
        self._honeytoken_planted = False

    def _tag_honeytoken(self, ds):
        # Plants the bait (RetrieveURL/canary PDF) into exactly one instance per seed() run,
        # baked into the stored file — not a per-retrieval overlay, so most instances stay real.
        if self._honeytoken and not self._honeytoken_planted:
            ds = self._honeytoken(ds)
            self._honeytoken_planted = True
        return ds

    def seed(
        self,
        collection: str,
        max_series: int = 3,
        max_images: int = 30,
        modality: str = "CT",
        epoch: str = "",
    ) -> int:
        # getSeries has no usable instance count, so we sample instead of sorting; a
        # per-epoch seeded RNG keeps selection idempotent within a week but varying across weeks.
        rng = random.Random(epoch or collection)
        loc = rng.choice(self._locations)
        series_list = self._client.get_series(collection, modality)

        requested = max_series > 0 and max_images > 0  # 0 means "fetch nothing", not "TCIA is down"

        self._honeytoken_planted = False
        stored = 0
        if series_list and requested:
            uids = [uid for s in series_list if (uid := s.get("SeriesInstanceUID"))]
            rng.shuffle(uids)
            for uid in uids[:max_series]:
                stored += self._ingest_series(uid, loc, max_images, epoch)

        if stored == 0 and requested:
            # TCIA unreachable, empty, or every download failed → bundled offline dataset.
            stored = self._seed_fallback(loc, modality, epoch)
            if stored:
                logger.warning(
                    f"TCIA unavailable for '{collection}' ({modality}); "
                    f"seeded {stored} instances from bundled offline fallback"
                )
            else:
                logger.warning(
                    f"TCIA unavailable for '{collection}' ({modality}) and no fallback "
                    f"data bundled; nothing seeded"
                )
            return stored

        logger.info(
            f"Seeded {stored} instances from '{collection}' ({modality}) as '{loc.institution}'"
        )
        return stored

    def _ingest_series(self, series_uid: str, loc: Location, max_images: int, epoch: str) -> int:
        sop_uids = self._client.get_sop_uids(series_uid)

        stored = 0
        for sop_uid in sop_uids[:max_images]:
            data = self._client.download_image(series_uid, sop_uid)
            if data is None:
                continue
            try:
                ds = dcmread(io.BytesIO(data))
            except Exception as exc:
                logger.error(f"Error reading {sop_uid}: {exc}")
                continue
            ds = _patch_location(
                ds, loc, self._male_pool, self._female_pool, self._physician_pool, epoch
            )
            ds = self._tag_honeytoken(ds)
            err = self._repo.store(ds, safe=True)
            if err is None:
                stored += 1
            else:
                logger.warning(f"Failed to store {sop_uid}: {err.error}")

        return stored

    def _seed_fallback(self, loc: Location, modality: str, epoch: str) -> int:
        stored = 0
        for ds in load_fallback_datasets(modality):
            ds = _patch_location(
                ds, loc, self._male_pool, self._female_pool, self._physician_pool, epoch
            )
            ds = self._tag_honeytoken(ds)
            err = self._repo.store(ds, safe=True)
            if err is None:
                stored += 1
            else:
                logger.warning(f"Failed to store fallback instance: {err.error}")
        return stored


def new_seeder(
    repo: Repository,
    locations: list[Location] | None = None,
    locale: str = "en_US",
    name_pools: NamePools | None = None,
    honeytoken: Middleware | None = None,
) -> Seeder:
    return Seeder(repo, locations=locations, locale=locale, name_pools=name_pools, honeytoken=honeytoken)
