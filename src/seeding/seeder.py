import io
import logging
import random
import threading
from collections.abc import Callable
from datetime import date

from pydicom import dcmread
from pydicom.dataset import Dataset

from dicomhawk.repository import Repository
from honeytoken.injector import Middleware

from .fallback import load_fallback_datasets
from .locations import Location, load_locations
from .names import NamePools, _patch_location, faker_pools
from .procedures import Procedures, load_procedures
from .tcia import TciaClient

logger = logging.getLogger(__name__)


def resolve_rotation(
    collections: list[str],
    modalities: list[str],
    rotate: bool,
    epoch: str | None = None,
) -> tuple[str, str, str]:
    # Weekly salts keep rotation fresh and idempotent within a week.
    if not rotate:
        return collections[0], modalities[0], ""
    year, week, _ = date.today().isocalendar()
    epoch = epoch if epoch is not None else f"{year}W{week:02d}"
    return (
        collections[week % len(collections)],
        modalities[week % len(modalities)],
        epoch,
    )


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
        self._modalities = modalities or ["CT"]
        self._interval = interval_minutes * 60
        self._max_series = max_series
        self._max_images = max_images
        self._rotate = rotate
        self._stop = threading.Event()

    def run(self) -> None:
        logger.info(
            f"Seed scheduler started, interval: {self._interval // 60}m, "
            f"collections: {self._collections}, modalities: {self._modalities}, "
            f"rotate: {self._rotate}"
        )
        while not self._stop.wait(self._interval):
            collection, modality, epoch = resolve_rotation(
                self._collections, self._modalities, self._rotate
            )
            n = self._seeder.seed(
                collection, self._max_series, self._max_images, modality, epoch
            )
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
        procedures: Procedures | None = None,
    ):
        self._repo = repo
        self._client = TciaClient()
        self._locations = locations or load_locations(None)
        self._procedures = procedures or load_procedures(None)
        self._locale = locale
        self._name_pools = name_pools
        self._honeytoken = honeytoken
        self._honeytoken_planted = False

    def _tag_honeytoken(self, ds: Dataset) -> tuple[Dataset, bool]:
        # Bake bait into one stored instance per seed run.
        if self._honeytoken and not self._honeytoken_planted:
            return self._honeytoken(ds), True
        return ds, False

    def seed(
        self,
        collection: str,
        max_series: int = 3,
        max_images: int = 30,
        modality: str = "CT",
        epoch: str = "",
        on_progress: Callable[[int, int, int], None] | None = None,
    ) -> int:
        # Seeded sampling compensates for getSeries lacking instance counts.
        rng = random.Random(epoch or collection)
        loc = rng.choice(self._locations)
        pools = self._name_pools or faker_pools(self._locale, epoch)
        series_list = self._client.get_series(collection, modality)

        download_requested = max_series > 0 and max_images > 0

        self._honeytoken_planted = False
        stored = 0
        if series_list and download_requested:
            uids = [uid for s in series_list if (uid := s.get("SeriesInstanceUID"))]
            rng.shuffle(uids)
            selected = uids[:max_series]
            for index, uid in enumerate(selected, 1):
                # Reported before the download so a caller can show movement, not just results.
                if on_progress is not None:
                    on_progress(index, len(selected), stored)
                stored += self._ingest_series(uid, loc, max_images, epoch, pools)

        if stored == 0 and download_requested:
            # TCIA unreachable, empty, or every download failed → bundled offline dataset.
            stored = self._seed_fallback(loc, modality, epoch, pools)
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

    def _ingest_series(
        self,
        series_uid: str,
        loc: Location,
        max_images: int,
        epoch: str,
        pools: NamePools,
    ) -> int:
        sop_uids = self._client.get_sop_uids(series_uid)
        male, female, physician = pools

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
                ds, loc, male, female, physician, epoch, self._procedures
            )
            ds, tagged = self._tag_honeytoken(ds)
            err = self._repo.store(ds, safe=True)
            if err is None:
                if tagged:
                    self._honeytoken_planted = True
                stored += 1
            else:
                logger.warning(f"Failed to store {sop_uid}: {err.error}")

        return stored

    def _seed_fallback(
        self, loc: Location, modality: str, epoch: str, pools: NamePools | None = None
    ) -> int:
        male, female, physician = pools or faker_pools(self._locale, epoch)
        stored = 0
        for ds in load_fallback_datasets(modality):
            ds = _patch_location(
                ds, loc, male, female, physician, epoch, self._procedures
            )
            ds, tagged = self._tag_honeytoken(ds)
            err = self._repo.store(ds, safe=True)
            if err is None:
                if tagged:
                    self._honeytoken_planted = True
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
    procedures: Procedures | None = None,
) -> Seeder:
    return Seeder(
        repo,
        locations=locations,
        locale=locale,
        name_pools=name_pools,
        honeytoken=honeytoken,
        procedures=procedures,
    )
