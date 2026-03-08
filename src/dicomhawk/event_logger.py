"""Structured event logging for DICOM protocol activity.

Each incoming DICOM operation is recorded as a single JSON line in a
configurable log file. The format is intentionally flat and simple so it
can be consumed by log shippers (e.g. Filebeat) feeding into ELK stacks
like T-Pot without any pre-processing.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)


class EventLogger:
    """Writes one JSON line per DICOM event to a log file.

    Uses Python's standard logging infrastructure internally so operational
    errors (e.g. permission denied) are surfaced through the normal log
    pipeline rather than swallowed silently.
    """

    def __init__(self, path: str) -> None:
        p = Path(path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise OSError(f"Cannot create log directory {p.parent}") from exc
        self._path = p
        _log.info("Event log initialised at %s", self._path)

    def log(self, fields: dict[str, Any]) -> None:
        """Append one JSON event record to the log file.

        A UTC timestamp is added under the key ``timestamp`` if not already
        present in *fields*.
        """
        fields.setdefault(
            "timestamp",
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

        try:
            with self._path.open("a") as f:
                f.write(json.dumps(fields) + "\n")
        except OSError as exc:
            _log.error("Failed to write event log entry: %s", exc)


def new_bus(path: str) -> EventLogger:
    return EventLogger(path)
