import json
import logging
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Location:
    institution: str
    address: str


_DEFAULT_FILE = "locations.json"


def _read_locations(source) -> list[Location]:
    data = json.loads(source.read_text())
    locations = [
        Location(item["institution"], item.get("address", "")) for item in data
    ]
    if not locations:
        raise ValueError("empty locations file")
    return locations


def load_locations(path: str | None) -> list[Location]:
    """Load a location file, falling back to packaged defaults."""
    defaults = files("seeding").joinpath(_DEFAULT_FILE)
    if path is None:
        return _read_locations(defaults)
    try:
        return _read_locations(Path(path))
    except Exception as exc:
        logger.warning(
            f"Failed to load locations from '{path}': {exc}; using built-in defaults"
        )
        return _read_locations(defaults)
