import json
import logging
from datetime import date, timedelta
from hashlib import md5
from pathlib import Path

from faker import Faker
from pydicom.dataset import Dataset

from .locations import Location

logger = logging.getLogger(__name__)

# (male, female, physician) DICOM PN pools; sex-split keeps PatientSex consistent.
type NamePools = tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]

_SENSITIVITY_TAGS = (
    "PatientAddress",
    "PatientTelephoneNumbers",
    "OtherPatientIDs",
    "OtherPatientNames",
    "PatientMotherBirthName",
    "ResponsiblePerson",
)

_BIRTH_DATE_START = date(1955, 1, 1)
_BIRTH_DATE_RANGE = (date(1999, 12, 31) - _BIRTH_DATE_START).days
_STUDY_DATE_START = date(2010, 1, 1)
_STUDY_DATE_RANGE = (date(2024, 12, 31) - _STUDY_DATE_START).days


def _stable_pick(pool: tuple[str, ...], key: str) -> str:
    idx = int(md5(key.encode()).hexdigest(), 16) % len(pool)
    return pool[idx]


def _stable_date(start: date, day_range: int, key: str, salt: str) -> str:
    """Deterministic YYYYMMDD date derived from a hash of key+salt."""
    digest = int(md5(f"{key}{salt}".encode()).hexdigest(), 16)
    return (start + timedelta(days=digest % day_range)).strftime("%Y%m%d")


def faker_pools(locale: str = "en_US") -> NamePools:
    faker = Faker(locale)
    # "^" = DICOM PN separator (Family^Given). first_name_male/female is absent for
    # some locales, so fall back to name().
    try:
        male = tuple(f"{faker.last_name()}^{faker.first_name_male()}" for _ in range(100))
        female = tuple(f"{faker.last_name()}^{faker.first_name_female()}" for _ in range(100))
    except AttributeError:
        names = tuple(faker.name() for _ in range(200))
        male, female = names[:100], names[100:]
    physician = tuple(f"{faker.last_name()}^{faker.first_name()}" for _ in range(50))
    return male, female, physician


def load_name_pools(path: str | None) -> NamePools | None:
    """Load {"male": [...], "female": [...], "physician": [...]} PN pools from JSON (physician
    optional, defaults to male+female); None or any error falls back to faker_pools()."""
    if path is None:
        return None
    try:
        data = json.loads(Path(path).read_text())
        male, female = tuple(data["male"]), tuple(data["female"])
        if not male or not female:
            raise ValueError("'male' and 'female' pools must be non-empty")
        physician = tuple(data.get("physician") or male + female)
        return male, female, physician
    except Exception as exc:
        logger.warning(f"Failed to load names from '{path}': {exc}; using generated names")
        return None


def _patch_location(
    ds: Dataset,
    loc: Location,
    male_pool: tuple[str, ...],
    female_pool: tuple[str, ...],
    physician_pool: tuple[str, ...],
    epoch: str = "",
) -> Dataset:
    # NOTE: epoch salts every derived key, so the same image becomes a fresh but
    # internally-consistent identity each rotation.
    patient_key = str(getattr(ds, "PatientID", "") or getattr(ds, "PatientName", "") or "")
    study_key = str(getattr(ds, "StudyInstanceUID", patient_key) or patient_key)
    pkey = f"{patient_key}{epoch}"
    skey = f"{study_key}{epoch}"

    # Deterministic sex: odd hash byte → M, even → F
    sex = "M" if int(md5(pkey.encode()).hexdigest()[0], 16) % 2 else "F"
    name_pool = male_pool if sex == "M" else female_pool

    # UTF-8: OSM names (œ, curly quotes) and non-Latin --locale names exceed Latin-1.
    ds.SpecificCharacterSet = "ISO_IR 192"
    ds.InstitutionName = loc.institution
    ds.InstitutionAddress = loc.address
    ds.StationName = f"{getattr(ds, 'Modality', 'XX')}01"
    ds.PatientName = _stable_pick(name_pool, pkey)
    ds.PatientID = md5(pkey.encode()).hexdigest()[:8].upper()
    ds.PatientSex = sex
    ds.PatientBirthDate = _stable_date(_BIRTH_DATE_START, _BIRTH_DATE_RANGE, pkey, "dob")
    ds.StudyDate = _stable_date(_STUDY_DATE_START, _STUDY_DATE_RANGE, skey, "std")
    ds.SeriesDate = ds.StudyDate
    ds.ReferringPhysicianName = _stable_pick(physician_pool, skey)

    for tag in _SENSITIVITY_TAGS:
        if hasattr(ds, tag):
            try:
                delattr(ds, tag)
            except AttributeError:
                pass

    return ds
