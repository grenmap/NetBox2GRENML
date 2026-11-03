"""NetBox API data importer with automatic pagination."""
from __future__ import annotations

from typing import Any, Dict

import requests

from config import Config


class NetBoxImporter:
    """Fetches topology data from a NetBox instance."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.headers = {
            "Authorization": f"Token {config.source.token}",
            "Accept": "application/json; indent=4",
        }
        if not config.source.token:
            print("WARNING: The 'token' config value is empty.")

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def fetch_devices(self) -> Dict[str, Any]:
        url = f"{self.config.source.base_url}dcim/devices/?limit={self.config.source.page_size}"
        print(f"Fetching ALL Devices from: {url}")
        return self._get_paginated(url)

    def fetch_cables(self) -> Dict[str, Any]:
        url = f"{self.config.source.base_url}dcim/cables/?limit={self.config.source.page_size}"
        print(f"Fetching Cables from: {url}")
        return self._get_paginated(url)

    def fetch_sites(self) -> Dict[str, Any]:
        url = f"{self.config.source.base_url}dcim/sites/?limit={self.config.source.page_size}"
        print(f"Fetching Sites from: {url}")
        return self._get_paginated(url)

    def fetch_circuits(self) -> Dict[str, Any]:
        url = f"{self.config.source.base_url}circuits/circuits/?limit={self.config.source.page_size}"
        print(f"Fetching Circuits from: {url}")
        return self._get_paginated(url)

    def fetch_all(self) -> Dict[str, Dict[str, Any]]:
        """Fetch all topology data in one call."""
        devices_data = self.fetch_devices()
        cables_data = self.fetch_cables()
        sites_data = self.fetch_sites()
        circuits_data = self.fetch_circuits()

        # Fallback: extract sites from device data when /dcim/sites/ fails
        if not sites_data.get("results"):
            print("WARNING: Sites fetch failed/empty. Extracting sites from device data...")
            sites_from_devices = {}
            for dev in devices_data.get("results", []):
                site = dev.get("site")
                if site and site.get("id") and site["id"] not in sites_from_devices:
                    sites_from_devices[site["id"]] = site
            sites_data = {"results": list(sites_from_devices.values())}
            print(f"  Extracted {len(sites_from_devices)} sites from device data.")

        return {
            "devices": devices_data,
            "cables": cables_data,
            "sites": sites_data,
            "circuits": circuits_data,
        }

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------

    def _get_paginated(self, url: str) -> Dict[str, Any]:
        try:
            requests.packages.urllib3.disable_warnings(
                requests.packages.urllib3.exceptions.InsecureRequestWarning
            )
            response = requests.get(url, headers=self.headers, verify=False)
            response.raise_for_status()
            data = response.json()

            next_url = data.get("next")
            while isinstance(next_url, str):
                print(f"Fetching next page: {next_url}")
                page = requests.get(next_url, headers=self.headers, verify=False)
                page.raise_for_status()
                page_data = page.json()
                data["results"] += page_data["results"]
                next_url = page_data.get("next")

            print(f"Fetch completed. Total of {len(data.get('results', []))} items.")
            return data
        except Exception as err:
            print(f"An unexpected error occurred: {err}")
        return {"results": []}