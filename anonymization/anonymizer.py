"""Anonymization engine for topology data."""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Set

from config import Config


class Anonymizer:
    """Stateful anonymizer -- keeps alias/IP counters across calls."""

    def __init__(self, config: Config) -> None:
        self.config = config
        # Interface alias state
        self._if_alias_map: Dict[int, str] = {}
        self._if_alias_counter: int = 1
        # Site alias state
        self._site_alias_map: Dict[str, str] = {}
        self._site_alias_counter: int = 1
        # Device alias state
        self._device_alias_map: Dict[str, str] = {}
        self._device_alias_counter: int = 1
        # IP anonymization state — parse base from config
        base_parts = config.common.anonymize_ip_base.split(".")
        self._ip_base_o1: int = int(base_parts[0]) if len(base_parts) > 0 else 192
        self._ip_base_o2: int = int(base_parts[1]) if len(base_parts) > 1 else 168
        self._ip_octet3: int = int(base_parts[2]) if len(base_parts) > 2 else 0
        self._ip_octet4: int = int(base_parts[3]) if len(base_parts) > 3 else 1
        # If base ends in .0, start from .1
        if self._ip_octet4 == 0:
            self._ip_octet4 = 1

    # ------------------------------------------------------------------
    # Additional properties
    # ------------------------------------------------------------------

    def populate_additional_properties(
        self,
        node: Any,
        device_data: Dict[str, Any],
    ) -> None:
        """Add NetBox fields as node properties, optionally anonymizing."""
        anonymized_ip = (
            self._anonymize_ip(device_data)
            if self.config.common.anonymize_ips
            else None
        )

        for key, value in device_data.items():
            if key in self.config.common.handled_keys or value in [None, {}, []]:
                continue
            if self.config.common.anonymize_device_names and key == "name":
                value = self.anonymize_device_name(
                    device_data.get("id"), str(value)
                )
            if anonymized_ip and key in ("primary_ip", "primary_ip4"):
                value = {"family": 4, "address": anonymized_ip}
            if (
                self.config.common.anonymize_fields
                and key not in self.config.common.anonymization_exceptions
            ):
                continue
            serialized = (
                json.dumps(value, ensure_ascii=False)
                if isinstance(value, (dict, list))
                else str(value)
            )
            node.properties[key] = serialized

    # ------------------------------------------------------------------
    # Device / site name anonymization
    # ------------------------------------------------------------------

    def anonymize_device_name(
        self, device_id: Optional[int], device_name: Optional[str]
    ) -> Optional[str]:
        """Return a stable alias for a device when name anonymization is enabled."""
        if not self.config.common.anonymize_device_names or not device_name:
            return device_name

        key = f"id:{device_id}" if device_id is not None else f"name:{device_name}"
        if key not in self._device_alias_map:
            self._device_alias_map[key] = (
                f"{self.config.common.anonymize_device_prefix}"
                f"{self._device_alias_counter}"
            )
            self._device_alias_counter += 1
        return self._device_alias_map[key]

    def anonymize_site_name(
        self, site_id: Optional[int], site_name: Optional[str]
    ) -> Optional[str]:
        """Return a stable alias for a site when name anonymization is enabled."""
        if not self.config.common.anonymize_site_names or not site_name:
            return site_name

        key = f"id:{site_id}" if site_id is not None else f"name:{site_name}"
        if key not in self._site_alias_map:
            self._site_alias_map[key] = (
                f"{self.config.common.anonymize_site_prefix}"
                f"{self._site_alias_counter}"
            )
            self._site_alias_counter += 1
        return self._site_alias_map[key]

    # ------------------------------------------------------------------
    # Interface / termination anonymization
    # ------------------------------------------------------------------

    def anonymize_terminations(
        self, termination_list: Optional[List[Dict[str, Any]]]
    ) -> Optional[List[Dict[str, Any]]]:
        """Anonymize interface names/descriptions in cable terminations."""
        if not termination_list:
            return termination_list
        for term in termination_list:
            if term.get("object_type") == "dcim.interface" and "object" in term:
                if_id = term["object"]["id"]
                if if_id not in self._if_alias_map:
                    self._if_alias_map[if_id] = (
                        f"{self.config.common.anonymize_interface_prefix}{self._if_alias_counter}"
                    )
                    self._if_alias_counter += 1
                term["object"]["name"] = self._if_alias_map[if_id]
                term["object"]["display"] = self._if_alias_map[if_id]
                term["object"]["description"] = ""
        return termination_list

    # ------------------------------------------------------------------
    # IP anonymization (internal)
    # ------------------------------------------------------------------

    def _anonymize_ip(self, device_data: Dict[str, Any]) -> Optional[str]:
        """Generate a fake IP for a device if it has primary_ip."""
        has_ip = device_data.get("primary_ip") or device_data.get("primary_ip4")
        if not has_ip:
            return None

        ip_base = f"{self._ip_base_o1}.{self._ip_base_o2}.{self._ip_octet3}.{self._ip_octet4}"
        self._ip_octet4 += 1
        if self._ip_octet4 > 254:
            self._ip_octet4 = 1
            self._ip_octet3 += 1

        mask = None
        for key in ("primary_ip4", "primary_ip"):
            data = device_data.get(key)
            if data and data.get("address"):
                parts = str(data["address"]).split("/")
                if len(parts) > 1:
                    mask = parts[1]
                    break

        return f"{ip_base}/{mask}" if mask else ip_base