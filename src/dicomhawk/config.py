import logging
import os
import json

from dataclasses import dataclass
from typing import List

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class Settings:
    PORTS: List[int]
    SERVER_HOST: str
    
    STORAGE_DIR: str
    C_STORE_DIR: str
    DATABASE: str
    HASH_STORE: str
    CANARY_PDF: str
    INTEGRITY_CHECK: bool

    # AE title
    AE_TITLE: str

    # UserInfo (application identity)
    IMPLEMENTATION_NAME: str
    IMPLEMENTATION_UID: str

    HONEY_URL: str


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

def require(value, name: str):
    if not value:
        raise RuntimeError(f"Missing required configuration: {name}")
    return value


def require_one_of(value, name: str, allowed):
    if value not in allowed:
        raise RuntimeError(f"{name} must be one of {allowed}")
    return value


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
    path = os.path.join(base, name)
    with open(path) as f:
        data = f.read()
        return data.strip()

def secret_or_env(secret_name: str, env_name: str, default: str | None = None) -> str | None:
    """Prefer a secret file; fall back to an env var for local development."""
    return get_secret(secret_name) or os.getenv(env_name, default)


# TODO: really do not like this. I dont know why this is not just flags?
def load_settings() -> Settings:
    settings = Settings(
        PORTS=env_json("DICOM_PORTS", [11112]),
        SERVER_HOST="0.0.0.0",

        STORAGE_DIR=os.getenv("STORAGE", "./storage/dicom_storage"),
        C_STORE_DIR=docker_path(docker, "/opt/dicomhawk/storage/c_store_files", "./storage/c_store_files"),
        DATABASE=docker_path(docker, "/opt/dicomhawk/storage/db.db", "./storage/db.db"),
        HASH_STORE=docker_path(docker, "/opt/dicomhawk/storage/hash_store.json", "./storage/hash_store.json"),
        CANARY_PDF=docker_path(docker, "/opt/dicomhawk/storage/can.pdf", "./storage/can.pdf"),
        INTEGRITY_CHECK=env_bool("INTEGRITY_CHECK", "True"),

        AE_TITLE=os.getenv("DICOM_AE_TITLE", "ORTHANC"), # TODO: I am not sure this is the typical AE title for ORTHANC
        IMPLEMENTATION_NAME=os.getenv("DICOM_IMPLEMENTATION_NAME", "ORTHANC"), # TODO: missing the version?
        IMPLEMENTATION_UID=os.getenv("DICOM_IMPLEMENTATION_UID", ""), # TODO: find the ORTHANC implementation id
        HONEY_URL=secret_or_env("honey_url", "HONEY_URL", "VALUE"),
    )

    return settings


settings = load_settings()
