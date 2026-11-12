"""GRENML XML importer for the format-agnostic topology model."""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Optional

from grenml.parse import GRENMLParser

from anonymization.anonymizer import Anonymizer
from config import Config
from geocoding.geocoder import Geocoder
from processing.topology import (
    Topology,
    TopologyInstitution,
    TopologyLink,
    TopologyNode,
)


class GRENMLImporter:
    """Parses a GRENML document into the shared topology representation."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.anonymizer = Anonymizer(config)
        self.geocoder = Geocoder()

    def import_file(self, file_path: str) -> Topology:
        manager = GRENMLParser().parse_file(file_path)
        source = manager.topology
        primary_owner = getattr(source, "primary_owner", None)
        primary_owner_id = self._id(primary_owner)
        topology = Topology(name=getattr(source, "name", None) or "GRENML Topology")

        institutions: Dict[str, TopologyInstitution] = {}
        for institution in manager.get_institutions():
            institution_id = self._id(institution)
            institutions[institution_id] = TopologyInstitution(
                id=institution_id,
                name=getattr(institution, "name", None) or institution_id,
                is_primary=institution_id == primary_owner_id,
            )
        topology.institutions.extend(institutions.values())

        nodes: Dict[str, TopologyNode] = {}
        for source_node in manager.get_nodes():
            node_id = self._id(source_node)
            name = getattr(source_node, "name", None) or node_id
            node_name = self.anonymizer.anonymize_device_name(node_id, name) or name
            properties = self._properties(source_node)
            for key in ("primary_ip", "primary_ip4"):
                if isinstance(properties.get(key), str):
                    properties[key] = {"address": properties[key]}
            site_name, site_id = self._site(properties)
            site_name = self.anonymizer.anonymize_site_name(
                site_id, site_name
            )
            if self.config.common.anonymize_site_names and site_name:
                if "site_name" in properties:
                    properties["site_name"] = site_name
                if isinstance(properties.get("site"), dict):
                    properties["site"]["name"] = site_name
                elif "site" in properties:
                    properties["site"] = site_name
            latitude = self._float(getattr(source_node, "latitude", None))
            longitude = self._float(getattr(source_node, "longitude", None))
            source_latitude = getattr(source_node, "latitude", None)
            source_longitude = getattr(source_node, "longitude", None)
            address = None
            if self.config.common.anonymize_location:
                latitude, longitude, address = self._anonymize_location(
                    latitude, longitude, address
                )
            elif source_latitude is not None and source_longitude is not None:
                address = self.geocoder.reverse_geocode(latitude, longitude)
            if "address" in properties:
                if address:
                    properties["address"] = address
                else:
                    properties.pop("address")

            node = TopologyNode(
                id=node_id,
                name=node_name,
                short_name=(
                    self.anonymizer.anonymize_device_name(
                        node_id, getattr(source_node, "short_name", None)
                    )
                    or node_name
                ),
                latitude=latitude,
                longitude=longitude,
                address=address,
                owner_ids=self._owner_ids(getattr(source_node, "owners", [])),
                site_name=site_name,
                platform=self._optional_property(properties, "platform"),
            )
            self.anonymizer.populate_additional_properties(node, properties)
            nodes[node_id] = node
            topology.nodes.append(node)

        for source_link in manager.get_links():
            node_ids = [self._id(node) for node in getattr(source_link, "nodes", [])]
            if len(node_ids) != 2 or any(node_id not in nodes for node_id in node_ids):
                continue

            properties = self._properties(source_link)
            if self.config.common.anonymize_interfaces:
                self._anonymize_link_interfaces(properties)
            topology.links.append(
                TopologyLink(
                    id=self._id(source_link),
                    name=getattr(source_link, "name", None) or self._id(source_link),
                    node_a_id=node_ids[0],
                    node_b_id=node_ids[1],
                    owner_ids=self._owner_ids(getattr(source_link, "owners", [])),
                    properties=self._string_properties(properties),
                )
            )

        return topology

    @staticmethod
    def _id(value: Any) -> str:
        return str(getattr(value, "id", value))

    @staticmethod
    def _float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _owner_ids(self, owners: Iterable[Any]) -> list[str]:
        return [self._id(owner) for owner in owners]

    @staticmethod
    def _properties(source_object: Any) -> Dict[str, Any]:
        properties = getattr(source_object, "additional_properties", {}) or {}
        if not isinstance(properties, dict):
            return {}
        return dict(properties)

    @staticmethod
    def _site(properties: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        site = properties.get("site_name", properties.get("site"))
        if isinstance(site, dict):
            return site.get("name"), str(site.get("id")) if site.get("id") else None
        return (str(site), None) if site else (None, None)

    @staticmethod
    def _optional_property(properties: Dict[str, Any], key: str) -> Optional[str]:
        value = properties.get(key)
        while value:
            if isinstance(value, (list, tuple)):
                value = next((item for item in value if item), None)
                continue
            if isinstance(value, str):
                try:
                    decoded = json.loads(value)
                except json.JSONDecodeError:
                    return value or None
                if isinstance(decoded, (dict, list)):
                    value = decoded
                    continue
                return str(decoded) if decoded else None
            if isinstance(value, dict):
                for field in ("name", "display", "slug", "value"):
                    if value.get(field):
                        value = value[field]
                        break
                else:
                    return None
                continue
            return str(value)
        return str(value) if value else None

    def _anonymize_location(
        self, latitude: float, longitude: float, address: Optional[str]
    ) -> tuple[float, float, Optional[str]]:
        if latitude == 0.0 and longitude == 0.0:
            return 0.0, 0.0, None
        coarse_address = self.geocoder.reverse_geocode(latitude, longitude)
        if not coarse_address:
            return 0.0, 0.0, None
        coarse_latitude, coarse_longitude, _ = self.geocoder.geocode(coarse_address)
        if coarse_latitude is not None and coarse_longitude is not None:
            latitude, longitude = coarse_latitude, coarse_longitude
        else:
            latitude, longitude = 0.0, 0.0
        return latitude, longitude, coarse_address

    def _anonymize_link_interfaces(self, properties: Dict[str, Any]) -> None:
        for key in ("a_terminations", "b_terminations"):
            terminations = properties.get(key)
            if (
                isinstance(terminations, list)
                and len(terminations) == 1
                and isinstance(terminations[0], str)
            ):
                terminations = terminations[0]
            if isinstance(terminations, str):
                try:
                    terminations = json.loads(terminations)
                except json.JSONDecodeError:
                    continue
            if isinstance(terminations, list) and all(
                isinstance(termination, dict) for termination in terminations
            ):
                properties[key] = self.anonymizer.anonymize_terminations(terminations)

    @staticmethod
    def _string_properties(properties: Dict[str, Any]) -> Dict[str, str]:
        result: Dict[str, str] = {}
        for key, value in properties.items():
            if isinstance(value, (dict, list)):
                result[key] = json.dumps(value, ensure_ascii=False)
            else:
                result[key] = str(value)
        return result