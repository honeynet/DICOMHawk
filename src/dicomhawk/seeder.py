import io
import json
import logging
import random
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from hashlib import md5
from pathlib import Path

import requests
from faker import Faker
from pydicom import dcmread
from pydicom.dataset import Dataset

from .repository import Repository

logger = logging.getLogger(__name__)

_NBIA_BASE = "https://services.cancerimagingarchive.net/nbia-api/services/v1"
# Primary, then a mirror to fall back on when the primary is down or 406s.
_OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
_OSM_CACHE_TTL_HOURS = 24
_OSM_SKIP_TERMS = frozenset({
    # English
    "pharmacy", "dentist", "veterinary", "optician", "dispensary",
    # Danish/Nordic
    "apotek", "tandlæge", "dyrlæge",
    # German
    "apotheke", "zahnarzt", "tierarzt",
    # French
    "pharmacie", "dentiste", "vétérinaire",
    # Spanish
    "farmacia", "dentista", "veterinario",
})
_OSM_DEFAULT_MAX = 50


@dataclass(frozen=True)
class Location:
    institution: str
    address: str


_DEFAULT_LOCATIONS: list[Location] = [
    Location("Valley Medical Center", "1400 West Valley Pkwy, Escondido, CA 92029"),
    Location("Riverside General Hospital", "9851 Magnolia Ave, Riverside, CA 92503"),
    Location("Lakewood Community Hospital", "3700 E South St, Lakewood, CA 90712"),
    Location("Northgate Regional Medical", "5555 N Gate Blvd, Sacramento, CA 95834"),
    Location("Desert Springs Medical Center", "2075 E Flamingo Rd, Las Vegas, NV 89119"),
    Location("Summit View Hospital", "800 Summit Ridge Dr, Denver, CO 80203"),
]

_SENSITIVITY_TAGS = (
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


def load_locations(path: str | None) -> list[Location]:
    """Load locations from a JSON file, or return built-in defaults if path is None."""
    if path is None:
        return _DEFAULT_LOCATIONS
    try:
        data = json.loads(Path(path).read_text())
        locs = [Location(e["institution"], e.get("address", "")) for e in data]
        if not locs:
            raise ValueError("empty locations file")
        return locs
    except Exception as exc:
        logger.warning(f"Failed to load locations from '{path}': {exc}; using built-in defaults")
        return _DEFAULT_LOCATIONS


class OsmClient:
    """Queries the Overpass API for hospital names and addresses in a given area."""

    def __init__(
        self,
        city: str | None = None,
        country: str | None = None,
        cache_path: str | None = None,
        timeout: int = 30,
        max_results: int = _OSM_DEFAULT_MAX,
    ):
        self._city = city
        self._country = country
        self._cache = (
            Path(cache_path)
            if cache_path
            else Path.home() / ".cache" / "dicomhawk" / "osm.json"
        )
        self._timeout = timeout
        self._max_results = max_results

    def get_locations(self) -> list[Location]:
        """Return locations from cache if fresh, otherwise fetch from Overpass."""
        if self._is_cache_valid():
            cached = self._load_cache()
            if cached:
                logger.debug(f"OSM: loaded {len(cached)} locations from cache")
                return cached

        locs = self._fetch()
        if locs:
            self._save_cache(locs)
            logger.info(f"OSM: fetched {len(locs)} medical institutions")
        else:
            logger.warning("OSM: no institutions found; falling back to built-in defaults")
        return locs

    def _fetch(self) -> list[Location]:
        query = self._build_query()
        # Explicit Accept avoids the primary's HTTP 406 (it's a header check, not the query).
        headers = {
            "User-Agent": "DICOMHawk/1.0 (https://github.com/honeynet/DICOMHawk)",
            "Accept": "application/json",
        }

        for url in _OVERPASS_MIRRORS:
            try:
                r = requests.post(url, data={"data": query}, timeout=self._timeout, headers=headers)
                r.raise_for_status()
                body = r.json()
                elements = body.get("elements", [])
                # Overpass reports timeouts as HTTP 200 + empty result + a remark.
                if not elements and "remark" in body:
                    raise ValueError(body["remark"])
                break
            except (requests.RequestException, ValueError) as exc:
                logger.warning(f"OSM Overpass query failed via {url}: {exc}")
        else:
            logger.error("OSM Overpass query failed on all mirrors")
            return []

        locs: list[Location] = []
        seen: set[str] = set()
        for el in elements:
            tags = el.get("tags", {})
            name = self._extract_name(tags)
            if not name or name in seen:
                continue
            seen.add(name)
            locs.append(Location(name, self._extract_address(tags)))
            if len(locs) >= self._max_results:
                break
        return locs

    def _build_query(self) -> str:
        # map_to_area pivot, not area∩area (which silently returns nothing for FR/US).
        if self._city and self._country:
            area = (
                f'area["ISO3166-1:alpha2"="{self._country}"]["admin_level"="2"]->.country;\n'
                f'rel(area.country)["name"="{self._city}"]["boundary"="administrative"]'
                f'["admin_level"~"^[4-8]$"]->.c;\n'
                f'.c map_to_area->.a;'
            )
        elif self._city:
            area = (
                f'rel["name"="{self._city}"]["boundary"="administrative"]'
                f'["admin_level"~"^[4-8]$"]->.c;\n'
                f'.c map_to_area->.a;'
            )
        elif self._country:
            area = f'area["ISO3166-1:alpha2"="{self._country}"]["admin_level"="2"]->.a;'
        else:
            area = ""
        scope = "(area.a)" if area else ""

        return (
            f'[out:json][timeout:{self._timeout}];\n'
            f'{area}\n'
            f'(\n'
            f'  node["amenity"="hospital"]{scope};\n'
            f'  way["amenity"="hospital"]{scope};\n'
            f'  node["healthcare"="hospital"]{scope};\n'
            f'  way["healthcare"="hospital"]{scope};\n'
            f');\n'
            f'out tags;'
        )

    def _extract_name(self, tags: dict) -> str | None:
        # Verbatim (.title() mangles acronyms); 64 = VR LO limit, longer truncates on write.
        for key in ("official_name", "name", "name:en", "alt_name", "brand"):
            val = tags.get(key, "").strip()
            if val and 3 <= len(val) <= 64:
                if not any(t in val.lower() for t in _OSM_SKIP_TERMS):
                    return val
        return None

    def _extract_address(self, tags: dict) -> str:
        parts = [
            tags.get("addr:housenumber", ""),
            tags.get("addr:street", ""),
            tags.get("addr:city", ""),
            tags.get("addr:state", ""),
            tags.get("addr:postcode", ""),
        ]
        return ", ".join(p for p in parts if p)

    def _is_cache_valid(self) -> bool:
        try:
            data = json.loads(self._cache.read_text())
            # Per-query cache: a different city/country is a miss, not a stale hit.
            if data.get("city") != self._city or data.get("country") != self._country:
                return False
            ts = datetime.fromisoformat(data["timestamp"])
            return datetime.now() < ts + timedelta(hours=_OSM_CACHE_TTL_HOURS)
        except Exception:
            return False

    def _load_cache(self) -> list[Location]:
        try:
            data = json.loads(self._cache.read_text())
            return [
                Location(e["institution"], e.get("address", ""))
                for e in data["locations"]
            ]
        except Exception:
            return []

    def _save_cache(self, locs: list[Location]) -> None:
        try:
            self._cache.parent.mkdir(parents=True, exist_ok=True)
            self._cache.write_text(json.dumps({
                "timestamp": datetime.now().isoformat(),
                "city": self._city,
                "country": self._country,
                "locations": [
                    {"institution": l.institution, "address": l.address} for l in locs
                ],
            }))
        except Exception as exc:
            logger.warning(f"OSM: failed to save cache: {exc}")


_BIRTH_DATE_START = date(1955, 1, 1)
_BIRTH_DATE_RANGE = (date(1999, 12, 31) - _BIRTH_DATE_START).days
_STUDY_DATE_START = date(2010, 1, 1)
_STUDY_DATE_RANGE = (date(2024, 12, 31) - _STUDY_DATE_START).days


def _stable_date(start: date, day_range: int, key: str, salt: str) -> str:
    """Deterministic YYYYMMDD date derived from a hash of key+salt."""
    digest = int(md5(f"{key}{salt}".encode()).hexdigest(), 16)
    return (start + timedelta(days=digest % day_range)).strftime("%Y%m%d")


def _patch_location(
    ds: Dataset,
    loc: Location,
    male_pool: tuple[str, ...],
    female_pool: tuple[str, ...],
    physician_pool: tuple[str, ...],
) -> Dataset:
    patient_key = str(getattr(ds, "PatientID", "") or getattr(ds, "PatientName", "") or "")
    study_key = str(getattr(ds, "StudyInstanceUID", patient_key) or patient_key)

    # Deterministic sex: odd hash byte → M, even → F
    sex = "M" if int(md5(patient_key.encode()).hexdigest()[0], 16) % 2 else "F"
    name_pool = male_pool if sex == "M" else female_pool

    # UTF-8: OSM names (œ, curly quotes) and non-Latin --locale names exceed Latin-1.
    ds.SpecificCharacterSet = "ISO_IR 192"
    ds.InstitutionName = loc.institution
    ds.InstitutionAddress = loc.address
    ds.StationName = f"{getattr(ds, 'Modality', 'XX')}01"
    ds.PatientName = _stable_pick(name_pool, patient_key)
    ds.PatientID = md5(patient_key.encode()).hexdigest()[:8].upper()
    ds.PatientSex = sex
    ds.PatientBirthDate = _stable_date(_BIRTH_DATE_START, _BIRTH_DATE_RANGE, patient_key, "dob")
    ds.StudyDate = _stable_date(_STUDY_DATE_START, _STUDY_DATE_RANGE, study_key, "std")
    ds.SeriesDate = ds.StudyDate
    ds.ReferringPhysicianName = _stable_pick(physician_pool, study_key)

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


class SeedScheduler(threading.Thread):
    """Daemon thread that re-seeds the honeypot on a fixed interval."""

    def __init__(
        self,
        seeder: "Seeder",
        collection: str,
        interval_minutes: int,
        max_series: int = 3,
        max_images: int = 5,
        modality: str = "CT",
    ):
        super().__init__(daemon=True, name="dicomhawk-seeder")
        self._seeder = seeder
        self._collection = collection
        self._interval = interval_minutes * 60
        self._max_series = max_series
        self._max_images = max_images
        self._modality = modality
        self._stop = threading.Event()

    def run(self) -> None:
        logger.info(
            f"Seed scheduler started — interval: {self._interval // 60}m, "
            f"collection: '{self._collection}', modality: '{self._modality}'"
        )
        while not self._stop.wait(self._interval):
            n = self._seeder.seed(self._collection, self._max_series, self._max_images, self._modality)
            logger.info(f"Scheduled seed completed: {n} instances stored")

    def stop(self) -> None:
        self._stop.set()


class Seeder:
    def __init__(
        self,
        repo: Repository,
        locations: list[Location] | None = None,
        locale: str = "en_US",
    ):
        self._repo = repo
        self._client = TciaClient()
        self._locations = locations if locations else _DEFAULT_LOCATIONS
        faker = Faker(locale)
        # "^" = DICOM PN separator (Family^Given). Split pools keep PatientSex consistent;
        # first_name_male/female is absent for some locales, so fall back to name().
        try:
            self._male_pool: tuple[str, ...] = tuple(
                f"{faker.last_name()}^{faker.first_name_male()}" for _ in range(100)
            )
            self._female_pool: tuple[str, ...] = tuple(
                f"{faker.last_name()}^{faker.first_name_female()}" for _ in range(100)
            )
        except AttributeError:
            names = tuple(faker.name() for _ in range(200))
            self._male_pool = names[:100]
            self._female_pool = names[100:]
        self._physician_pool: tuple[str, ...] = tuple(
            f"{faker.last_name()}^{faker.first_name()}" for _ in range(50)
        )

    def seed(self, collection: str, max_series: int = 3, max_images: int = 5, modality: str = "CT") -> int:
        loc = random.choice(self._locations)
        series_list = self._client.get_series(collection, modality)
        if not series_list:
            logger.warning(
                f"TCIA unreachable or no {modality} series in '{collection}'; "
                f"nothing seeded (re-run when TCIA is available, or use --interval to retry automatically)"
            )
            return 0

        series_list.sort(key=lambda s: int(s.get("NumberOfSeriesRelatedInstances", 999)))

        stored = 0
        for entry in series_list[:max_series]:
            if uid := entry.get("SeriesInstanceUID"):
                stored += self._ingest_series(uid, loc, max_images)

        logger.info(f"Seeded {stored} instances from '{collection}' ({modality}) as '{loc.institution}'")
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
            ds = _patch_location(ds, loc, self._male_pool, self._female_pool, self._physician_pool)
            err = self._repo.store(ds, safe=True)
            if err is None:
                stored += 1
            else:
                logger.warning(f"Failed to store {sop_uid}: {err.error}")

        return stored


def new_seeder(
    repo: Repository,
    locations: list[Location] | None = None,
    locale: str = "en_US",
) -> Seeder:
    return Seeder(repo, locations=locations, locale=locale)
