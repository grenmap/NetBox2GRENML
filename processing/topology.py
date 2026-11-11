"""Build an intermediate topology from raw NetBox data."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from config import Config, device_matches_filters
from anonymization.anonymizer import Anonymizer
from geocoding.geocoder import Geocoder


# ------------------------------------------------------------------
# Intermediate data model
# ------------------------------------------------------------------

@dataclass
class TopologyNode:
    id: str
    name: str
    short_name: str
    latitude: float
    longitude: float
    address: Optional[str]
    owner_ids: List[str]
    site_name: Optional[str] = None
    platform: Optional[str] = None
    properties: Dict[str, str] = field(default_factory=dict)


@dataclass
class TopologyLink:
    id: str
    name: str
    node_a_id: str
    node_b_id: str
    owner_ids: List[str]
    properties: Dict[str, str] = field(default_factory=dict)


@dataclass
class TopologyInstitution:
    id: str
    name: str
    is_primary: bool = False


@dataclass
class Topology:
    """Format-agnostic topology representation."""
    name: str
    institutions: List[TopologyInstitution] = field(default_factory=list)
    nodes: List[TopologyNode] = field(default_factory=list)
    links: List[TopologyLink] = field(default_factory=list)


# ------------------------------------------------------------------
# Builder
# ------------------------------------------------------------------

class TopologyBuilder:
    """Transforms raw NetBox API data into a Topology object."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.geocoder = Geocoder()
        self.anonymizer = Anonymizer(config)

    def build(self, raw_data: Dict[str, Dict[str, Any]]) -> Topology:
        """Main entry point. raw_data has keys: devices, cables, sites, circuits."""
        devices_data = raw_data["devices"]
        cables_data = raw_data["cables"]
        sites_data = raw_data["sites"]
        circuits_data = raw_data["circuits"]

        sites_map = {s["id"]: s for s in sites_data.get("results", [])}
        topology = Topology(name="NetBox Topology")

        # --- Institutions ---
        primary_inst, tenant_map, device_owner_map, device_site_map = (
            self._build_institutions(devices_data, topology)
        )

        # --- Resolve cable/circuit -> device links ---
        device_links, resolved_circuit_ids = self._resolve_links(
            cables_data, circuits_data
        )

        # --- Build nodes (with aggregation) ---
        processed_nodes, rep_urns_owner, rep_urns_site, site_to_owner = (
            self._build_nodes(
                devices_data, sites_map, topology,
                primary_inst, tenant_map, device_owner_map, device_site_map,
            )
        )

        # --- Determine linked nodes ---
        linked_urns = self._find_linked_urns(
            device_links, circuits_data, resolved_circuit_ids,
            processed_nodes, device_owner_map, device_site_map,
            rep_urns_owner, rep_urns_site, site_to_owner,
        )

        # --- Filter nodes ---
        final_node_ids = set()
        for urn, topo_node in processed_nodes.items():
            if not self.config.common.remove_unlinked_nodes or urn in linked_urns:
                topology.nodes.append(topo_node)
                final_node_ids.add(urn)

        # --- Build links ---
        self._build_topology_links(
            cables_data, circuits_data, resolved_circuit_ids,
            processed_nodes, final_node_ids, topology,
            device_owner_map, device_site_map,
            rep_urns_owner, rep_urns_site, site_to_owner,
            primary_inst, tenant_map,
        )

        return topology

    # ==================================================================
    # Institutions
    # ==================================================================

    def _build_institutions(
        self, devices_data: Dict, topology: Topology
    ) -> Tuple[TopologyInstitution, Dict, Dict, Dict]:
        device_owner_map: Dict[int, str] = {}
        device_site_map: Dict[int, int] = {}
        tenants_raw: Dict[int, Dict] = {}

        for device in devices_data.get("results", []):
            dev_id = device.get("id")
            if not dev_id:
                continue
            site_id = (device.get("site") or {}).get("id")
            tenant_id = (device.get("tenant") or {}).get("id")
            if site_id:
                device_site_map[dev_id] = site_id
            if tenant_id:
                owner_id = (
                    "urn:netbox:tenant:unified"
                    if self.config.common.unify_netbox_tenants
                    else f"urn:netbox:tenant:{tenant_id}"
                )
                device_owner_map[dev_id] = owner_id
                tenants_raw[tenant_id] = device["tenant"]
            else:
                device_owner_map[dev_id] = self.config.common.generic_owner_id

        primary = TopologyInstitution(
            id=self.config.common.generic_owner_id,
            name=self.config.common.generic_owner_name,
            is_primary=True,
        )
        topology.institutions.append(primary)

        tenant_map: Dict[int, TopologyInstitution] = {}
        if self.config.common.unify_netbox_tenants and tenants_raw:
            inst = TopologyInstitution(
                id="urn:netbox:tenant:unified",
                name=self.config.common.unified_tenant_name,
            )
            topology.institutions.append(inst)
            tenant_map = {t_id: inst for t_id in tenants_raw}
        else:
            for t_id, t_data in tenants_raw.items():
                inst = TopologyInstitution(
                    id=f"urn:netbox:tenant:{t_id}", name=t_data["name"]
                )
                topology.institutions.append(inst)
                tenant_map[t_id] = inst

        return primary, tenant_map, device_owner_map, device_site_map

    # ==================================================================
    # Resolve cable/circuit -> device-level links
    # ==================================================================

    def _resolve_links(
        self, cables_data: Dict, circuits_data: Dict
    ) -> Tuple[List[Tuple[int, int, Dict]], Set[int]]:
        circuit_term_to_device: Dict[int, int] = {}
        device_links: List[Tuple[int, int, Dict]] = []
        resolved_ids: Set[int] = set()

        for cable in cables_data.get("results", []):
            try:
                t_a = cable["a_terminations"][0]
                t_b = cable["b_terminations"][0]
                d_a = self._device_from_term(t_a)
                d_b = self._device_from_term(t_b)
                c_a = self._circuit_from_term(t_a)
                c_b = self._circuit_from_term(t_b)
                if d_a and d_b:
                    device_links.append((d_a, d_b, cable))
                elif d_a and c_b:
                    circuit_term_to_device[c_b] = d_a
                elif c_a and d_b:
                    circuit_term_to_device[c_a] = d_b
            except Exception:
                continue

        for circuit in circuits_data.get("results", []):
            try:
                d_a = circuit_term_to_device.get(circuit["termination_a"]["id"])
                d_b = circuit_term_to_device.get(circuit["termination_z"]["id"])
                if d_a and d_b:
                    device_links.append((d_a, d_b, circuit))
                    resolved_ids.add(circuit["id"])
            except Exception:
                continue

        return device_links, resolved_ids

    @staticmethod
    def _device_from_term(term: Dict) -> Optional[int]:
        if term.get("object_type") == "dcim.interface":
            return term.get("object", {}).get("device", {}).get("id")
        return None

    @staticmethod
    def _circuit_from_term(term: Dict) -> Optional[int]:
        if term.get("object_type") == "circuits.circuittermination":
            return term.get("object", {}).get("id")
        return None

    # ==================================================================
    # Build nodes (aggregation logic)
    # ==================================================================

    def _build_nodes(
        self, devices_data, sites_map, topology,
        primary_inst, tenant_map, device_owner_map, device_site_map,
    ):
        processed: Dict[str, TopologyNode] = {}
        rep_devices_owner: Dict[str, Dict] = {}
        rep_urns_owner: Dict[str, str] = {}
        rep_devices_site: Dict[int, Dict] = {}
        rep_urns_site: Dict[int, str] = {}
        site_to_owner: Dict[int, str] = {}

        all_devices = devices_data.get("results", [])
        active_nodes = [
            d for d in all_devices
            if device_matches_filters(d, self.config.source.device_filters)
        ]

        if self.config.common.aggregate_by_owner:
            for device in all_devices:
                urn = device_owner_map.get(device["id"], self.config.common.generic_owner_id)
                if urn not in rep_devices_owner:
                    rep_devices_owner[urn] = device
                    rep_urns_owner[urn] = urn

            role_slugs = set()
            for d in all_devices:
                slug = (d.get("role") or {}).get("slug")
                if slug:
                    role_slugs.add(slug)
            print(f"DEBUG: Found role slugs in devices: {role_slugs}")
            print(f"DEBUG: Filtering for device_role_slug = '{self.config.common.device_role_slug}'")

            for device in active_nodes:
                urn = device_owner_map.get(device["id"], self.config.common.generic_owner_id)
                site_id = device_site_map.get(device["id"])
                if site_id:
                    site_to_owner[site_id] = urn
                if urn not in processed:
                    inst = self._find_institution(urn, tenant_map, primary_inst)
                    rep = rep_devices_owner[urn]
                    lat, lon, adr = self.geocoder.get_location_data(
                        rep, sites_map, self.config.common.anonymize_location
                    )
                    
                    owner_ids = [primary_inst.id]
                    if inst.id != primary_inst.id:
                        owner_ids.append(inst.id)
                    site_obj = rep.get("site") or {}
                    site_nm = site_obj.get("name") or site_obj.get("display", "")
                    site_nm = self.anonymizer.anonymize_site_name(
                        site_obj.get("id"), site_nm
                    )
                    plat_obj = rep.get("platform") or {}
                    plat_nm = plat_obj.get("name") if isinstance(plat_obj, dict) else None
                    node = TopologyNode(
                        id=urn, name=inst.name, short_name=inst.name,
                        latitude=lat, longitude=lon, address=adr,
                        owner_ids=owner_ids,
                        site_name=site_nm or None,
                        platform=plat_nm,
                    )
                    self.anonymizer.populate_additional_properties(node, rep)
                    processed[urn] = node

        elif self.config.common.aggregate_by_site:
            for device in all_devices:
                s_id = (device.get("site") or {}).get("id")
                if s_id and s_id not in rep_devices_site:
                    rep_devices_site[s_id] = device
                    rep_urns_site[s_id] = f"urn:netbox:device:{device['id']}"

            for device in active_nodes:
                s_id = (device.get("site") or {}).get("id")
                if s_id:
                    urn = rep_urns_site.get(s_id)
                    if urn and urn not in processed:
                        s_full = sites_map.get(s_id)
                        name = s_full.get("name") if s_full else device["name"]
                        lat, lon, adr = self.geocoder.get_location_data(
                            rep_devices_site[s_id], sites_map,
                            self.config.common.anonymize_location,
                        )
                        owner_ids = [primary_inst.id]
                        t_id = (device.get("tenant") or {}).get("id")
                        if t_id and t_id in tenant_map:
                            owner_ids.append(tenant_map[t_id].id)
                        rep_dev = rep_devices_site[s_id]
                        site_obj = rep_dev.get("site") or {}
                        site_nm = site_obj.get("name") or site_obj.get("display", "")
                        site_nm = self.anonymizer.anonymize_site_name(
                            s_id, site_nm or name
                        )
                        if self.config.common.anonymize_site_names:
                            name = site_nm or name
                        plat_obj = rep_dev.get("platform") or {}
                        plat_nm = plat_obj.get("name") if isinstance(plat_obj, dict) else None
                        
                        node = TopologyNode(
                            id=urn, name=name, short_name=name,
                            latitude=lat, longitude=lon, address=adr,
                            owner_ids=owner_ids,
                            site_name=site_nm or None,
                            platform=plat_nm,
                        )
                        self.anonymizer.populate_additional_properties(
                            node, rep_devices_site[s_id]
                        )
                        processed[urn] = node
        else:
            for device in active_nodes:
                urn = f"urn:netbox:device:{device['id']}"
                lat, lon, adr = self.geocoder.get_location_data(
                    device, sites_map, self.config.common.anonymize_location
                )
                owner_ids = [primary_inst.id]
                t_id = (device.get("tenant") or {}).get("id")
                if t_id and t_id in tenant_map:
                    owner_ids.append(tenant_map[t_id].id)
                site_obj = device.get("site") or {}
                site_nm = site_obj.get("name") or site_obj.get("display", "")
                site_nm = self.anonymizer.anonymize_site_name(
                    site_obj.get("id"), site_nm
                )
                plat_obj = device.get("platform") or {}
                plat_nm = plat_obj.get("name") if isinstance(plat_obj, dict) else None
                device_name = self.anonymizer.anonymize_device_name(
                    device.get("id"), device["name"]
                ) or device["name"]

                node = TopologyNode(
                    id=urn, name=device_name, short_name=device_name,
                    latitude=lat, longitude=lon, address=adr,
                    owner_ids=owner_ids,
                    site_name=site_nm or None,
                    platform=plat_nm,
                )
                self.anonymizer.populate_additional_properties(node, device)
                processed[urn] = node

        return processed, rep_urns_owner, rep_urns_site, site_to_owner

    def _find_institution(self, urn, tenant_map, primary):
        for t_id, inst in tenant_map.items():
            if inst.id == urn:
                return inst
        return primary

    # ==================================================================
    # Linked-node detection
    # ==================================================================

    def _find_linked_urns(
        self, device_links, circuits_data, resolved_ids,
        processed, device_owner_map, device_site_map,
        rep_urns_owner, rep_urns_site, site_to_owner,
    ) -> Set[str]:
        linked: Set[str] = set()

        for d_a, d_b, _ in device_links:
            n_a, n_b = self._resolve_node_urns(
                d_a, d_b, device_owner_map, device_site_map,
                rep_urns_owner, rep_urns_site,
            )
            if n_a in processed and n_b in processed and n_a != n_b:
                linked.update([n_a, n_b])

        if self.config.common.aggregate_by_owner or self.config.common.aggregate_by_site:
            for circuit in circuits_data.get("results", []):
                if circuit["id"] in resolved_ids:
                    continue
                try:
                    s_a = circuit["termination_a"]["site"]["id"]
                    s_z = circuit["termination_z"]["site"]["id"]
                    if self.config.common.aggregate_by_owner:
                        n_a = rep_urns_owner.get(site_to_owner.get(s_a))
                        n_b = rep_urns_owner.get(site_to_owner.get(s_z))
                    else:
                        n_a = rep_urns_site.get(s_a)
                        n_b = rep_urns_site.get(s_z)
                    if n_a in processed and n_b in processed and n_a != n_b:
                        linked.update([n_a, n_b])
                except Exception:
                    continue

        return linked

    # ==================================================================
    # Build topology links
    # ==================================================================

    def _build_topology_links(
        self, cables_data, circuits_data, resolved_ids,
        processed, final_node_ids, topology,
        device_owner_map, device_site_map,
        rep_urns_owner, rep_urns_site, site_to_owner,
        primary_inst, tenant_map,
    ) -> None:
        created: Set[Tuple[str, str]] = set()

        for cable in cables_data.get("results", []):
            l_id, l_name, n_a, n_b = self._extract_link_endpoints(
                cable, device_owner_map, device_site_map,
                rep_urns_owner, rep_urns_site,
            )
            if (
                n_a in final_node_ids
                and n_b in final_node_ids
                and n_a != n_b
            ):
                key = tuple(sorted([n_a, n_b]))
                if key not in created:
                    owner_ids = list(
                        set(processed[n_a].owner_ids)
                        | set(processed[n_b].owner_ids)
                    )
                    props = {}
                    a_t = cable.get("a_terminations")
                    b_t = cable.get("b_terminations")
                    if self.config.common.anonymize_interfaces:
                        a_t = self.anonymizer.anonymize_terminations(a_t)
                        b_t = self.anonymizer.anonymize_terminations(b_t)
                    if a_t:
                        props["a_terminations"] = json.dumps(a_t)
                    if b_t:
                        props["b_terminations"] = json.dumps(b_t)

                    link = TopologyLink(
                        id=l_id, name=l_name,
                        node_a_id=n_a, node_b_id=n_b,
                        owner_ids=owner_ids, properties=props,
                    )
                    topology.links.append(link)
                    created.add(key)

        if self.config.common.aggregate_by_owner or self.config.common.aggregate_by_site:
            for circuit in circuits_data.get("results", []):
                if circuit["id"] in resolved_ids:
                    continue
                try:
                    s_a = circuit["termination_a"]["site"]["id"]
                    s_z = circuit["termination_z"]["site"]["id"]
                    if self.config.common.aggregate_by_owner:
                        n_a = rep_urns_owner.get(site_to_owner.get(s_a))
                        n_b = rep_urns_owner.get(site_to_owner.get(s_z))
                    else:
                        n_a = rep_urns_site.get(s_a)
                        n_b = rep_urns_site.get(s_z)
                    if (
                        n_a in final_node_ids
                        and n_b in final_node_ids
                        and n_a != n_b
                    ):
                        key = tuple(sorted([n_a, n_b]))
                        if key not in created:
                            owner_ids = [primary_inst.id]
                            t_id = (circuit.get("tenant") or {}).get("id")
                            if t_id and t_id in tenant_map:
                                owner_ids.append(tenant_map[t_id].id)
                            props = {}
                            if circuit.get("termination_a"):
                                props["termination_a"] = json.dumps(
                                    circuit["termination_a"]
                                )
                            if circuit.get("termination_z"):
                                props["termination_z"] = json.dumps(
                                    circuit["termination_z"]
                                )
                            link = TopologyLink(
                                id=f"urn:netbox:circuit:{circuit['id']}",
                                name=circuit.get("cid") or f"Link {circuit['id']}",
                                node_a_id=n_a, node_b_id=n_b,
                                owner_ids=owner_ids, properties=props,
                            )
                            topology.links.append(link)
                            created.add(key)
                except Exception:
                    continue

    # ==================================================================
    # Helpers
    # ==================================================================

    def _resolve_node_urns(
        self, d_a, d_b, device_owner_map, device_site_map,
        rep_urns_owner, rep_urns_site,
    ) -> Tuple[Optional[str], Optional[str]]:
        if self.config.common.aggregate_by_owner:
            return (
                rep_urns_owner.get(device_owner_map.get(d_a)),
                rep_urns_owner.get(device_owner_map.get(d_b)),
            )
        elif self.config.common.aggregate_by_site:
            return (
                rep_urns_site.get(device_site_map.get(d_a)),
                rep_urns_site.get(device_site_map.get(d_b)),
            )
        return f"urn:netbox:device:{d_a}", f"urn:netbox:device:{d_b}"

    def _extract_link_endpoints(self, cable, dev_owner, dev_site, rep_owner, rep_site):
        try:
            a_term = cable["a_terminations"][0]
            b_term = cable["b_terminations"][0]
            if (
                a_term.get("object_type") != "dcim.interface"
                or b_term.get("object_type") != "dcim.interface"
            ):
                return None, None, None, None
            d_a = a_term["object"]["device"]["id"]
            d_b = b_term["object"]["device"]["id"]
            link_id = f"urn:netbox:cable:{cable['id']}"
            link_name = cable.get("display") or f"Cable {cable['id']}"
            n_a, n_b = self._resolve_node_urns(
                d_a, d_b, dev_owner, dev_site, rep_owner, rep_site
            )
            return link_id, link_name, n_a, n_b
        except Exception:
            return None, None, None, None