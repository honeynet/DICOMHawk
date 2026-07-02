import json
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


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
