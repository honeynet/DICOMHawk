"""Structured event logging for DICOM protocol activity.

Each incoming DICOM operation is recorded as a single JSON line in a
configurable log file. The format is intentionally flat and simple so it
can be consumed by log shippers (e.g. Filebeat) feeding into ELK stacks
like T-Pot without any pre-processing.

Call configure() once at startup before the server starts accepting
connections.  If configure() is never called, log_event() is a silent
no-op so callers do not need to guard against an unconfigured logger.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_log_path: Path | None = None


def configure(path: str) -> None:
    """Set the destination file for the structured event log.

    Creates any missing parent directories.  Raises nothing — if the
    directory cannot be created the error is logged and logging is
    left disabled.
    """
    global _log_path

    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _log.error("Cannot create log directory %s: %s", p.parent, exc)
        return

    _log_path = p
    _log.info("Event log configured at %s", _log_path)


def log_event(fields: dict[str, Any]) -> None:
    """Append one JSON record to the event log.

    A UTC timestamp is added under the key ``timestamp`` if one is not
    already present in *fields*.  The call is a no-op when configure()
    has not been called yet.
    """
    if _log_path is None:
        return

    fields.setdefault(
        "timestamp",
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    try:
        with _log_path.open("a") as f:
            f.write(json.dumps(fields) + "\n")
    except OSError as exc:
        _log.error("Failed to write event log entry: %s", exc)
