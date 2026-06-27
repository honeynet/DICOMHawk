import io
import logging
import random
from dataclasses import dataclass
from hashlib import md5

import requests
from pydicom import dcmread
from pydicom.dataset import Dataset

from .repository import Repository

logger = logging.getLogger(__name__)

_NBIA_BASE = "https://services.cancerimagingarchive.net/nbia-api/services/v1"


@dataclass(frozen=True)
class Location:
    institution: str
    address: str
    physicians: tuple[str, ...]
    patients: tuple[str, ...]


_LOCATIONS: list[Location] = [
    Location(
        "Valley Medical Center",
        "1400 West Valley Pkwy, Escondido, CA 92029",
        ("Rivera^Carlos^M", "Patel^Anita^K", "Thompson^David^R"),
        (
            "Anderson^James^T", "Brown^Patricia^L", "Clark^Michael^S",
            "Davis^Linda^J", "Evans^Robert^W", "Foster^Barbara^A",
        ),
    ),
    Location(
        "Riverside General Hospital",
        "9851 Magnolia Ave, Riverside, CA 92503",
        ("Nguyen^Thuy^H", "Kim^James^Y", "Okonkwo^Emeka^C"),
        (
            "Garcia^Maria^E", "Harris^Charles^B", "Jackson^Dorothy^M",
            "Johnson^William^F", "Lewis^Ruth^A", "Martin^Joseph^D",
        ),
    ),
    Location(
        "Lakewood Community Hospital",
        "3700 E South St, Lakewood, CA 90712",
        ("Chen^Wei^L", "Sharma^Priya^N", "Robinson^Mark^A"),
        (
            "Moore^Thomas^H", "Nelson^Sandra^K", "Parker^Kevin^R",
            "Roberts^Karen^S", "Scott^George^E", "Turner^Nancy^C",
        ),
    ),
    Location(
        "Northgate Regional Medical",
        "5555 N Gate Blvd, Sacramento, CA 95834",
        ("Williams^Janet^M", "Jones^Brian^T", "Martinez^Elena^R"),
        (
            "Walker^Steven^L", "White^Deborah^J", "Young^Edward^P",
            "Adams^Carol^W", "Baker^Frank^N", "Campbell^Shirley^B",
        ),
    ),
    Location(
        "Desert Springs Medical Center",
        "2075 E Flamingo Rd, Las Vegas, NV 89119",
        ("Taylor^Michael^D", "Wilson^Sarah^A", "Lee^Kevin^J"),
        (
            "Collins^Harold^K", "Edwards^Gloria^T", "Flores^Miguel^A",
            "Green^Helen^R", "Hall^Dennis^S", "Hill^Margaret^L",
        ),
    ),
    Location(
        "Summit View Hospital",
        "800 Summit Ridge Dr, Denver, CO 80203",
        ("Patel^Raj^S", "O'Brien^Katherine^M", "Yamamoto^Kenji^T"),
        (
            "Jenkins^Arthur^G", "King^Virginia^L", "Lee^Raymond^C",
            "Mitchell^Donna^H", "Perry^Billy^J", "Reed^Alice^F",
        ),
    ),
]

_SENSITIVITY_TAGS = (
    "PatientBirthDate",
    "PatientAddress",
    "PatientTelephoneNumbers",
    "OtherPatientIDs",
    "OtherPatientNames",
    "PatientMotherBirthName",
    "ResponsiblePerson",
)


def _stable_pick(pool: tuple[str, ...], key: str) -> str:
    idx = int(md5(key.encode()).hexdigest(), 16) % len(pool)
    return pool[idx]


def _patch_location(ds: Dataset, loc: Location) -> Dataset:
    patient_key = str(getattr(ds, "PatientID", "") or getattr(ds, "PatientName", "") or "")
    study_key = str(getattr(ds, "StudyInstanceUID", patient_key) or patient_key)

    ds.InstitutionName = loc.institution
    ds.InstitutionAddress = loc.address
    ds.StationName = f"{getattr(ds, 'Modality', 'XX')}01"
    ds.PatientName = _stable_pick(loc.patients, patient_key)
    ds.PatientID = md5(patient_key.encode()).hexdigest()[:8].upper()
    ds.ReferringPhysicianName = _stable_pick(loc.physicians, study_key)

    for tag in _SENSITIVITY_TAGS:
        if hasattr(ds, tag):
            try:
                delattr(ds, tag)
            except AttributeError:
                pass

    return ds


class TciaClient:
    def __init__(self, base_url: str = _NBIA_BASE, timeout: int = 30):
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    def get_series(self, collection: str, modality: str = "CT") -> list[dict]:
        try:
            r = requests.get(
                f"{self._base}/getSeries",
                params={"Collection": collection, "Modality": modality},
                timeout=self._timeout,
            )
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            logger.error(f"TCIA getSeries failed: {exc}")
            return []

    def get_sop_uids(self, series_uid: str) -> list[str]:
        try:
            r = requests.get(
                f"{self._base}/getSOPInstanceUIDs",
                params={"SeriesInstanceUID": series_uid},
                timeout=self._timeout,
            )
            r.raise_for_status()
            items = r.json()
        except (requests.RequestException, ValueError) as exc:
            logger.error(f"TCIA getSOPInstanceUIDs failed for {series_uid}: {exc}")
            return []
        return [uid for item in items if (uid := item.get("SOPInstanceUID"))]

    def download_image(self, series_uid: str, sop_uid: str) -> bytes | None:
        try:
            r = requests.get(
                f"{self._base}/getSingleImage",
                params={"SeriesInstanceUID": series_uid, "SOPInstanceUID": sop_uid},
                timeout=self._timeout,
            )
            r.raise_for_status()
            return r.content
        except requests.RequestException as exc:
            logger.error(f"TCIA getSingleImage failed for {sop_uid}: {exc}")
            return None


class Seeder:
    def __init__(self, repo: Repository):
        self._repo = repo
        self._client = TciaClient()

    def seed(self, collection: str, max_series: int = 3, max_images: int = 5) -> int:
        loc = random.choice(_LOCATIONS)
        series_list = self._client.get_series(collection)
        if not series_list:
            logger.warning(f"TCIA unreachable or no CT series in '{collection}'; nothing seeded")
            return 0

        # prefer smaller series to keep seeding fast
        series_list.sort(key=lambda s: int(s.get("NumberOfSeriesRelatedInstances", 999)))

        stored = 0
        for entry in series_list[:max_series]:
            if uid := entry.get("SeriesInstanceUID"):
                stored += self._ingest_series(uid, loc, max_images)

        logger.info(f"Seeded {stored} instances from '{collection}' as '{loc.institution}'")
        return stored

    def _ingest_series(self, series_uid: str, loc: Location, max_images: int) -> int:
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
            ds = _patch_location(ds, loc)
            err = self._repo.store(ds, safe=True)
            if err is None:
                stored += 1
            else:
                logger.warning(f"Failed to store {sop_uid}: {err.error}")

        return stored


def new_seeder(repo: Repository) -> Seeder:
    return Seeder(repo)
