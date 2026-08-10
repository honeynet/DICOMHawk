from dataclasses import dataclass


@dataclass
class FingerprintConfig:
    DB_PATH: str = "fingerprint.db"
    MAX_BODY_BYTES: int = 64 * 1024  # a collector payload is a few KB; hard cap
    MAX_PER_SESSION: int = 20  # kept per session before further ones are dropped
    MAX_PER_IP: int = 500  # per source address; rotating sessions cannot bypass it
    MAX_VALUE_CHARS: int = 512  # per-signal string cap, applied before storing


def new_fingerprint_config(
    db_path: str = "fingerprint.db",
    max_body_bytes: int = 64 * 1024,
    max_per_session: int = 20,
    max_per_ip: int = 500,
    max_value_chars: int = 512,
) -> FingerprintConfig:
    return FingerprintConfig(
        DB_PATH=db_path,
        MAX_BODY_BYTES=max_body_bytes,
        MAX_PER_SESSION=max_per_session,
        MAX_PER_IP=max_per_ip,
        MAX_VALUE_CHARS=max_value_chars,
    )
