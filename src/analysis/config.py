from dataclasses import dataclass


@dataclass
class AnalysisConfig:
    DB_PATH: str = "analysis.db"
    RULES_DIR: str | None = None  # operator .yar files, added to the shipped ones
    TIMEOUT: float = 10.0  # hard wall-clock deadline per job, seconds
    MAX_BYTES: int = 64 * 1024 * 1024  # bounded read/extraction cap per capture
    QUEUE_SIZE: int = 256  # in-memory wake-up bound; durable state is the DB


def new_analysis_config(
    db_path: str = "analysis.db",
    rules_dir: str | None = None,
    timeout: float = 10.0,
    max_bytes: int = 64 * 1024 * 1024,
    queue_size: int = 256,
) -> AnalysisConfig:
    return AnalysisConfig(
        DB_PATH=db_path,
        RULES_DIR=rules_dir,
        TIMEOUT=timeout,
        MAX_BYTES=max_bytes,
        QUEUE_SIZE=queue_size,
    )
