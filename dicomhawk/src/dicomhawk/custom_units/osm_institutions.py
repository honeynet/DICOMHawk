"""
This module provides functionality to fetch real medical institutions from OpenStreetMap
and cache them for optimal performance.
"""

import json
import os
import random
from datetime import datetime, timedelta
from typing import List, Dict, Optional
import overpy
from services.osm_service import IOSMService
import config


class OSMInstitutionsService(IOSMService):
    """
    Service to fetch and manage medical institutions from OpenStreetMap.

    """

    def __init__(self, app_logger, exceptions_logger):
        self.logger = app_logger
        self.exceptions_logger = exceptions_logger
        self.api = overpy.Overpass()
        self.cache_file = config.OSM_CACHE_FILE
        self.cache_duration_hours = config.OSM_CACHE_DURATION
        self.max_institutions = config.OSM_MAX_INSTITUTIONS
        self.timeout = config.OSM_TIMEOUT
        self.country = config.OSM_COUNTRY
        self.city = config.OSM_CITY
        self.fallback_institutions = config.OSM_FALLBACK_INSTITUTIONS
        self.enabled = config.OSM_ENABLED
        
        # Medical facility tags for OSM queries
        self.medical_tags = ['amenity=hospital', 'healthcare=hospital']
        
        # Ensure cache directory exists
        os.makedirs(os.path.dirname(self.cache_file), exist_ok=True)

    def get_medical_institutions(self) -> List[str]:
        try:
            if not self.enabled:
                self.logger.info("OSM is disabled, using fallback institutions")
                return self.fallback_institutions

            # Try to get from cache first
            if self.is_cache_valid():
                institutions = self._load_from_cache()
                if institutions:
                    self.logger.debug(f"Loaded {len(institutions)} institutions from cache")
                    return institutions

            self.logger.info("Cache invalid or empty, fetching institutions from OSM")
            institutions = self._fetch_from_osm()
            
            if institutions:
                self._save_to_cache(institutions)
                self.logger.info(f"Successfully fetched {len(institutions)} institutions from OSM")
                return institutions
            else:
                self.logger.warning("No institutions found from OSM, using fallback")
                self._save_to_cache(self.fallback_institutions)
                return self.fallback_institutions

        except Exception as e:
            self.exceptions_logger.exception("Error getting medical institutions")
            self.logger.warning("Failed to get institutions from OSM, using fallback")
            self._save_to_cache(self.fallback_institutions)
            return self.fallback_institutions

    def refresh_cache(self) -> bool:
        try:
            self.logger.info("Force refreshing OSM institutions cache")
            institutions = self._fetch_from_osm()
            
            if institutions:
                self._save_to_cache(institutions)
                self.logger.info(f"Cache refreshed with {len(institutions)} institutions")
                return True
            else:
                self.logger.warning("Failed to refresh cache - no institutions found")
                return False
                
        except Exception as e:
            self.exceptions_logger.exception("Error refreshing OSM cache")
            return False

    def is_cache_valid(self) -> bool:
        """
        Check if the current cache is still valid.
        """
        try:
            if not os.path.exists(self.cache_file):
                return False

            with open(self.cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)

            cache_time = datetime.fromisoformat(cache_data.get('timestamp', ''))
            expiry_time = cache_time + timedelta(hours=self.cache_duration_hours)
            
            return datetime.now() < expiry_time

        except Exception as e:
            self.exceptions_logger.exception("Error checking cache validity")
            return False

    def _fetch_from_osm(self) -> List[str]:
        try:
            query = self._build_osm_query()
            self.logger.debug(f"OSM Query: {query}")

            result = self.api.query(query)
            
            institutions = []
            
            # Process both nodes and ways
            for element in list(result.nodes) + list(result.ways):
                name = self._extract_institution_name(element.tags)
                if name:
                    institutions.append(name)

            # Remove duplicates and limit results
            institutions = list(set(institutions))[:self.max_institutions]
            
            self.logger.info(f"Found {len(institutions)} medical institutions from OSM")
            return institutions

        except Exception as e:
            self.exceptions_logger.exception("Error fetching from OSM")
            raise

    def _build_osm_query(self) -> str:
        medical_queries = []
        for tag in self.medical_tags:
            key, value = tag.split('=')
            medical_queries.extend([
                f'node[{key}={value}](area);',
                f'way[{key}={value}](area);'
            ])
        
        medical_query_str = '\n  '.join(medical_queries)
        
        # Build area query based on configuration
        if self.city and self.country:
            area_query = f"""(
  area["name"="{self.city}"]["place"~"^(city|town)$"];
  area["name:en"="{self.city}"]["admin_level"~"^(4|5|6|7|8)$"];
) -> .area;"""
        elif self.city:
            area_query = f"""(
  area["name"="{self.city}"]["place"~"^(city|town)$"];
  area["name:en"="{self.city}"]["admin_level"];
) -> .area;"""
        else:
            area_query = f'area["ISO3166-1"="{self.country}"]["admin_level"="2"] -> .area;'
        
        return f"""[out:json][timeout:{self.timeout}];
{area_query}
(
  {medical_query_str}
);
out tags;"""

    def _extract_institution_name(self, tags: Dict) -> Optional[str]:
        name_keys = ['name', 'name:en', 'official_name', 'alt_name', 'brand']
        
        for key in name_keys:
            if key in tags and tags[key].strip():
                name = tags[key].strip()
                
                if self._is_valid_institution_name(name):
                    return name.title() 
        
        return None

    def _is_valid_institution_name(self, name: str) -> bool:
        if not name or len(name) < 3 or len(name) > 100:
            return False
            
        skip_terms = ['pharmacy', 'apotek', 'dentist', 'tandlæge', 'veterinary', 'dyrlæge']
        name_lower = name.lower()
        return not any(term in name_lower for term in skip_terms)

    def _load_from_cache(self) -> List[str]:
        try:
            with open(self.cache_file, 'r', encoding='utf-8') as f:
                cache_data = json.load(f)
                return cache_data.get('institutions', [])
        except Exception as e:
            self.exceptions_logger.exception("Error loading from cache")
            return []

    def _save_to_cache(self, institutions: List[str]) -> None:
        try:
            cache_data = {
                'timestamp': datetime.now().isoformat(),
                'institutions': institutions
            }
            
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(cache_data, f, indent=2, ensure_ascii=False)
                
            self.logger.debug(f"Saved {len(institutions)} institutions to cache")
            
        except Exception as e:
            self.exceptions_logger.exception("Error saving to cache")


def get_random_institution(osm_service: Optional[OSMInstitutionsService] = None) -> str:
    if osm_service and config.OSM_ENABLED:
        try:
            institutions = osm_service.get_medical_institutions()
            return random.choice(institutions)
        except Exception:
            pass
    
    return random.choice(config.OSM_FALLBACK_INSTITUTIONS) 