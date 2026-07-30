import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

import requests

from .locations import Location

logger = logging.getLogger(__name__)

# Primary, then a mirror to fall back on when the primary is down or 406s.
_OVERPASS_MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
_OSM_CACHE_TTL_HOURS = 24
_OSM_SKIP_TERMS = frozenset(
    {
        # English
        "pharmacy",
        "dentist",
        "veterinary",
        "optician",
        "dispensary",
        # Danish/Nordic
        "apotek",
        "tandlæge",
        "dyrlæge",
        # German
        "apotheke",
        "zahnarzt",
        "tierarzt",
        # French
        "pharmacie",
        "dentiste",
        "vétérinaire",
        # Spanish
        "farmacia",
        "dentista",
        "veterinario",
    }
)
_OSM_DEFAULT_MAX = 50


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
        # DICOMHAWK_CACHE_DIR points the cache at a writable dir when the rootfs is read-only.
        _cache_dir = os.environ.get("DICOMHAWK_CACHE_DIR")
        if cache_path:
            self._cache = Path(cache_path)
        elif _cache_dir:
            self._cache = Path(_cache_dir) / "osm.json"
        else:
            self._cache = Path.home() / ".cache" / "dicomhawk" / "osm.json"
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
            logger.warning(
                "OSM: no institutions found; falling back to built-in defaults"
            )
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
                r = requests.post(
                    url, data={"data": query}, timeout=self._timeout, headers=headers
                )
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
                f".c map_to_area->.a;"
            )
        elif self._city:
            area = (
                f'rel["name"="{self._city}"]["boundary"="administrative"]'
                f'["admin_level"~"^[4-8]$"]->.c;\n'
                f".c map_to_area->.a;"
            )
        elif self._country:
            area = f'area["ISO3166-1:alpha2"="{self._country}"]["admin_level"="2"]->.a;'
        else:
            area = ""
        scope = "(area.a)" if area else ""

        return (
            f"[out:json][timeout:{self._timeout}];\n"
            f"{area}\n"
            f"(\n"
            f'  node["amenity"="hospital"]{scope};\n'
            f'  way["amenity"="hospital"]{scope};\n'
            f'  node["healthcare"="hospital"]{scope};\n'
            f'  way["healthcare"="hospital"]{scope};\n'
            f");\n"
            f"out tags;"
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
            self._cache.write_text(
                json.dumps(
                    {
                        "timestamp": datetime.now().isoformat(),
                        "city": self._city,
                        "country": self._country,
                        "locations": [
                            {"institution": l.institution, "address": l.address}
                            for l in locs
                        ],
                    }
                )
            )
        except Exception as exc:
            logger.warning(f"OSM: failed to save cache: {exc}")
