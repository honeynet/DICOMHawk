import logging
import os
import json

from dataclasses import dataclass
from typing import List

@dataclass(frozen=True)
class DICOMSettings:
    PORTS: List[int]
    SERVER_HOST: str

    STORAGE_DIR: str
    C_STORE_DIR: str
    DATABASE: str
    HASH_STORE: str
    CANARY_PDF: str
    INTEGRITY_CHECK: bool

    # Structured event log — one JSON line per DICOM operation
    EVENT_LOG: str

    # AE title
    AE_TITLE: str

    # UserInfo (application identity)
    IMPLEMENTATION_NAME: str
    IMPLEMENTATION_UID: str


@dataclass(frozen=True)
class TCIASettings:
    ACTIVATED: bool
    FALLBACK_MODE: bool
    USERNAME: str
    PASSWORD: str
    PERIOD_UNIT: str
    PERIOD: int
    FILES_DIR: str
    STAGGER_DIR: str
    MODALITIES: List[str]
    MIN_FILES: int
    MAX_FILES: int
    STUDIES_PER_MODALITY: int


@dataclass(frozen=True)
class OSMSettings:
    ENABLED: bool
    COUNTRY: str
    CITY: str
    CACHE_DURATION: int
    MAX_INSTITUTIONS: int
    TIMEOUT: int
    FALLBACK_INSTITUTIONS: List[str]
    CACHE_FILE: str

@dataclass(frozen=True)
class Settings:
    PROD: bool
    DOCKER: bool

    FLASK_ACTIVATED: bool
    BLOCK_SCANNERS: bool

    HONEY_URL: str
    FAKER_LOCALE: str

    DICOM: DICOMSettings
    TCIA: TCIASettings
    OSM: OSMSettings


TRUE_LIST = {"true", "1", "t", "yes"}


def env_bool(name: str, default: str = "False") -> bool:
    return os.getenv(name, default).lower() in TRUE_LIST


def env_int(name: str, default: int) -> int:
    return int(os.getenv(name, default))


def env_json(name: str, default):
    try:
        return json.loads(os.getenv(name, json.dumps(default)))
    except json.JSONDecodeError:
        return default


def docker_path(docker: bool, docker_path: str, local_path: str) -> str:
    return docker_path if docker else local_path

def require(value, name: str):
    if not value:
        raise RuntimeError(f"Missing required configuration: {name}")
    return value


def require_one_of(value, name: str, allowed):
    if value not in allowed:
        raise RuntimeError(f"{name} must be one of {allowed}")
    return value


_log = logging.getLogger(__name__)


def get_secret(name: str) -> str | None:
    """Read a secret from a mounted file.

    The base path is controlled by SECRETS_BASE_PATH (default: /run/secrets),
    so the same code works in Docker Compose, Swarm, and bare-metal setups.

    Returns None when the file does not exist — the caller can fall back to
    an environment variable for local development.  Raises on permission
    errors because that indicates a misconfigured deployment and we should
    not silently continue.
    """
    base = os.getenv("SECRETS_BASE_PATH", "/run/secrets")
    path = f"{base}/{name}"
    try:
        with open(path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return None
    except PermissionError:
        _log.error("Cannot read secret '%s' from %s: permission denied", name, path)
        raise


def secret_or_env(secret_name: str, env_name: str, default: str | None = None) -> str | None:
    """Prefer a secret file; fall back to an env var for local development."""
    return get_secret(secret_name) or os.getenv(env_name, default)


def load_settings() -> Settings:
    docker = env_bool("DOCKER")

    dicom = DICOMSettings(
        PORTS=env_json("DICOM_PORTS", [11112]),
        SERVER_HOST="172.29.0.3" if docker else "0.0.0.0",

        STORAGE_DIR=docker_path(docker, "/opt/dicomhawk/storage/dicom_storage", "./storage/dicom_storage"),
        C_STORE_DIR=docker_path(docker, "/opt/dicomhawk/storage/c_store_files", "./storage/c_store_files"),
        DATABASE=docker_path(docker, "/opt/dicomhawk/storage/db.db", "./storage/db.db"),
        HASH_STORE=docker_path(docker, "/opt/dicomhawk/storage/hash_store.json", "./storage/hash_store.json"),
        CANARY_PDF=docker_path(docker, "/opt/dicomhawk/storage/can.pdf", "./storage/can.pdf"),
        INTEGRITY_CHECK=env_bool("INTEGRITY_CHECK", "True"),

        EVENT_LOG=docker_path(docker, "/var/log/dicomhawk/dicomhawk.log", "./logs/dicomhawk.log"),

        AE_TITLE=os.getenv("DICOM_AE_TITLE", "ORTHANC"), # TODO: I am not sure this is the typical AE title for ORTHANC
        IMPLEMENTATION_NAME=os.getenv("DICOM_IMPLEMENTATION_NAME", "ORTHANC"), # TODO: missing the version?
        IMPLEMENTATION_UID=os.getenv("DICOM_IMPLEMENTATION_UID"), # TODO: find the ORTHANC implementation id
    )

    tcia = TCIASettings(
        ACTIVATED=env_bool("TCIA_ACTIVATED", "True"),
        FALLBACK_MODE=env_bool("TCIA_FALLBACK_MODE", "True"),
        USERNAME=secret_or_env("tcia_username", "TCIA_USER_NAME", "user"),
        PASSWORD=secret_or_env("tcia_password", "TCIA_PASSWORD", "pass"),
        PERIOD_UNIT=require_one_of(
            os.getenv("TCIA_PERIOD_UNIT", "week"),
            "TCIA_PERIOD_UNIT",
            {"hour", "day", "week"},
        ),
        PERIOD=env_int("TCIA_PERIOD", 1),
        FILES_DIR=docker_path(docker, "/opt/dicomhawk/tcia/data", "./storage/tcia_data"),
        STAGGER_DIR=docker_path(docker, "/opt/dicomhawk/tcia/stagger", "./storage/stagger"),
        MODALITIES=env_json("MODALITIES", ["CT", "MR", "US", "DX"]),
        MIN_FILES=env_int("MINIMUM_TCIA_FILES_IN_SERIE", 1),
        MAX_FILES=env_int("MAXIMUM_TCIA_FILES_IN_SERIE", 3),
        STUDIES_PER_MODALITY=env_int("TCIA_STUDIES_PER_MODALITY", 10),
    )

    osm = OSMSettings(
        ENABLED=env_bool("OSM_ENABLED", "True"),
        COUNTRY=os.getenv("OSM_COUNTRY", "DK"),
        CITY=os.getenv("OSM_CITY", ""),
        CACHE_DURATION=env_int("OSM_CACHE_DURATION", 24),
        MAX_INSTITUTIONS=env_int("OSM_MAX_INSTITUTIONS", 50),
        TIMEOUT=env_int("OSM_TIMEOUT", 30),
        FALLBACK_INSTITUTIONS=env_json(
            "OSM_FALLBACK_INSTITUTIONS",
            ["Københavns Sundhedscenter", "Aarhus Kliniken"],
        ),
        CACHE_FILE=docker_path(
            docker,
            "/opt/dicomhawk/storage/osm_institutions_cache.json",
            "./storage/osm_institutions_cache.json",
        ),
    )

    return Settings(
        PROD=env_bool("PROD"),
        DOCKER=docker,
        FLASK_ACTIVATED=env_bool("FLASK_ACTIVATED", "True"),
        BLOCK_SCANNERS=env_bool("BLOCK_SCANNERS"),
        HONEY_URL=secret_or_env("honey_url", "HONEY_URL", "VALUE"),
        FAKER_LOCALE=os.getenv("FAKER_LOCALE", "en_US"),

        # Settings
        DICOM=dicom,
        TCIA=tcia,
        OSM=osm,
    )

settings = load_settings()
