from __future__ import annotations

import time
from typing import Any, Dict, Optional, Tuple

import requests

class Geocoder:
    """Geocoder with in-memory caching. Reverse uses BigDataCloud; forward uses Nominatim."""

    _NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
    _BIGDATA_URL = "https://api.bigdatacloud.net/data/reverse-geocode-client"

    def __init__(self) -> None:
        self._forward_cache: Dict[str, Tuple[Optional[float], Optional[float], Optional[str]]] = {}
        self._reverse_cache: Dict[Tuple[float, float], Optional[str]] = {}
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "grenml_netbox_converter/1.0"})

    # ------------------------------------------------------------------
    # Forward geocoding  (text -> lat, lon, city) — Nominatim with retry
    # ------------------------------------------------------------------

    def geocode(self, description: str) -> Tuple[Optional[float], Optional[float], Optional[str]]:
        """Return (lat, lon, city) for a textual description."""
        if description in self._forward_cache:
            return self._forward_cache[description]

        for attempt in range(3):
            try:
                time.sleep(1.5)  # respect Nominatim rate-limit
                r = self._session.get(
                    self._NOMINATIM_URL,
                    params={
                        "q": description,
                        "format": "json",
                        "addressdetails": "1",
                        "limit": "1",
                        "accept-language": "en",
                    },
                    timeout=10,
                )
                if r.status_code == 429 and attempt < 2:
                    wait = 5 * (attempt + 1)
                    print(f"  [RATE-LIMIT] Forward 429 — waiting {wait}s (attempt {attempt+1}/3)")
                    time.sleep(wait)
                    continue
                r.raise_for_status()
                results = r.json()
                if results:
                    hit = results[0]
                    lat = float(hit["lat"])
                    lon = float(hit["lon"])
                    address = hit.get("address", {})
                    city = address.get("city", address.get("town"))
                    result = (lat, lon, city)
                    self._forward_cache[description] = result
                    return result
                break  # empty results, no point retrying
            except Exception as e:
                print(f"Geocoding Error: {e}")
                break

        self._forward_cache[description] = (None, None, None)
        return (None, None, None)

    # ------------------------------------------------------------------
    # Reverse geocoding  (lat, lon -> "city, state, country") — BigDataCloud
    # ------------------------------------------------------------------

    def reverse_geocode(self, lat: float, lon: float) -> Optional[str]:
        """Return an anonymized address string (city, state, country) for coordinates."""
        cache_key = (lat, lon)
        if cache_key in self._reverse_cache:
            return self._reverse_cache[cache_key]

        try:
            r = self._session.get(
                self._BIGDATA_URL,
                params={"latitude": lat, "longitude": lon, "localityLanguage": "en"},
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            city = data.get("city") or data.get("locality")
            state = data.get("principalSubdivision")
            country = data.get("countryName")
            anon = ", ".join(filter(None, [city, state, country]))
            if anon:
                self._reverse_cache[cache_key] = anon
                return anon
        except Exception as e:
            print(f"Reverse Geocoding Error: {e}")

        self._reverse_cache[cache_key] = None
        return None

    # ------------------------------------------------------------------
    # Location resolution (device -> site fallback -> geocode from text)
    # ------------------------------------------------------------------

    def get_location_data(
        self,
        device: Dict[str, Any],
        sites_map: Dict[int, Dict[str, Any]],
        anonymize: bool = False,
    ) -> Tuple[float, float, Optional[str]]:
        """Resolve lat/lon/address for a device, with optional anonymization."""
        location_obj = device.get("location")
        site_minimal = device.get("site")
        site_full = (
            sites_map.get(site_minimal["id"])
            if site_minimal and site_minimal.get("id") in sites_map
            else None
        )

        # 1. Try device-level coords
        lat = device.get("latitude")
        lon = device.get("longitude")

        # 2. Fallback to site-level coords
        if lat is None and site_full:
            lat = site_full.get("latitude")
            lon = site_full.get("longitude")

        # 3. Fallback to geocoding from site text fields
        if lat is None and site_full:
            for text in [
                site_full.get("address"),
                site_full.get("physical_address"),
                site_full.get("description"),
                site_full.get("name"),
            ]:
                if text:
                    geo_lat, geo_lon, _ = self.geocode(text)
                    if geo_lat:
                        lat, lon = geo_lat, geo_lon
                        break

        if lat is None:
            return 0, 0, None

        # 4. Anonymize or build plain address
        if anonymize:
            anon_addr = self.reverse_geocode(lat, lon)
            if anon_addr:
                anon_lat, anon_lon, _ = self.geocode(anon_addr)
                if anon_lat is not None:
                    return anon_lat, anon_lon, anon_addr
                return lat, lon, anon_addr
            return lat, lon, None
        else:
            parts = [
                location_obj.get("name") if location_obj else None,
                site_minimal.get("name") if site_minimal else None,
            ]
            final_addr = ", ".join(filter(None, parts))
            return lat, lon, (final_addr if final_addr else None)
