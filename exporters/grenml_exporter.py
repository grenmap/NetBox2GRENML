"""GRENML (XML) exporter."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from grenml.managers import GRENMLManager
from grenml.models import Node, Institution, Link

from exporters.base import BaseExporter
from processing.topology import Topology, TopologyInstitution


def _sanitize_properties(props: Dict[str, Any]) -> Dict[str, str]:
    """Ensure every property value is a plain string.

    GRENML's add_property() raises
    'Property <key> must be a non-dictionary iterable'
    when the value is a dict or list.  This helper serializes
    complex values to JSON strings and converts everything else
    to str.
    """
    clean: Dict[str, str] = {}
    for k, v in props.items():
        if isinstance(v, (dict, list)):
            clean[k] = json.dumps(v, ensure_ascii=False)
        else:
            clean[k] = str(v)
    return clean


class GRENMLExporter(BaseExporter):
    """Exports a Topology to GRENML XML format."""

    def export(self, topology: Topology, output_path: Optional[str] = None) -> str:
        manager = GRENMLManager(name=topology.name)

        # --- Institutions ---
        inst_objs: Dict[str, Institution] = {}
        for inst in topology.institutions:
            grenml_inst = Institution(id=inst.id, name=inst.name)
            manager.add_institution(grenml_inst, primary_owner=inst.is_primary)
            inst_objs[inst.id] = grenml_inst

        # --- Nodes ---
        node_objs: Dict[str, Node] = {}
        for topo_node in topology.nodes:
            owners = [inst_objs[oid] for oid in topo_node.owner_ids if oid in inst_objs]
            node = Node(
                id=topo_node.id,
                name=topo_node.name,
                short_name=topo_node.short_name,
                latitude=topo_node.latitude,
                longitude=topo_node.longitude,
                address=topo_node.address,
                owners=owners,
            )
            for k, v in _sanitize_properties(topo_node.properties).items():
                node.add_property(k, v)
            manager.add_node(node)
            node_objs[topo_node.id] = node

        # --- Links ---
        for topo_link in topology.links:
            n_a = node_objs.get(topo_link.node_a_id)
            n_b = node_objs.get(topo_link.node_b_id)
            if not n_a or not n_b:
                continue
            owners = [inst_objs[oid] for oid in topo_link.owner_ids if oid in inst_objs]
            link = Link(
                id=topo_link.id,
                name=topo_link.name,
                owners=owners,
                nodes=[n_a, n_b],
            )
            for k, v in _sanitize_properties(topo_link.properties).items():
                link.add_property(k, v)
            manager.add_link(link)

        # --- Serialize ---
        xml_string = manager.write_to_string()

        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(xml_string)
            print(f"\n[SUCCESS] '{output_path}' saved correctly.")

        return xml_string