"""NetBox API exporter — pushes topology to a target NetBox instance.

Workflow:
  1. PURGE  — delete existing topology data (ip-addresses, cables, circuits,
              interfaces, devices, sites, roles, types, manufacturers,
              tenants, providers, circuit-types) in dependency-safe order.
  2. SCAFFOLD — ensure generic manufacturer, device-type, device-role,
                provider, circuit-type exist (get-or-create).
  3. CREATE — tenants -> sites -> devices -> interfaces -> cables ->
              IP addresses (loopbacks + per-cable /31s) -> circuits
              in dependency order.
"""
from __future__ import annotations

import ipaddress
import re
import json
from typing import Any, Dict, List, Optional, Tuple

import requests

from config import Config
from exporters.base import BaseExporter
from processing.topology import Topology, TopologyInstitution, TopologyLink, TopologyNode


def _slugify(text: str) -> str:
    """Convert text to a NetBox-safe slug (lowercase, hyphens, max 100 chars)."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:100] or "unnamed"


class NetBoxExporter(BaseExporter):
    """Exports a Topology to a target NetBox instance via REST API."""

    # Batch sizes
    _DELETE_BATCH = 100
    _CREATE_BATCH = 50

    def __init__(self, config: Config) -> None:
        self._config = config
        self._base = config.export_netbox.target_url.rstrip("/")
        if not self._base.endswith("/api"):
            self._base = self._base + "/api"
        self._base = self._base.rstrip("/")
        self._headers = {
            "Authorization": f"Token {config.export_netbox.target_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        self._dry_run = config.export_netbox.dry_run
        self._no_purge = config.export_netbox.no_purge

        # Caches populated during export
        self._tenant_map: Dict[str, int] = {}      # institution_id -> netbox tenant id
        self._site_map: Dict[str, int] = {}         # node_id -> netbox site id
        self._device_map: Dict[str, int] = {}       # node_id -> netbox device id
        self._interface_counter: Dict[int, int] = {}  # netbox_device_id -> next eth index
        self._interface_cache: Dict[Tuple[str, str], int] = {}  # (node_id, link_id) -> netbox interface id

        # Scaffold IDs
        self._manufacturer_id: Optional[int] = None
        self._device_type_id: Optional[int] = None
        self._device_role_id: Optional[int] = None
        self._provider_id: Optional[int] = None
        self._circuit_type_id: Optional[int] = None
        self._platform_map: Dict[str, int] = {}  # platform_name -> netbox platform id

        # IP allocation state
        self._ip_next: int = 0  # offset from base address

        # Stats
        self._stats: Dict[str, int] = {}

        # Suppress SSL warnings
        requests.packages.urllib3.disable_warnings(
            requests.packages.urllib3.exceptions.InsecureRequestWarning
        )

    # ==================================================================
    # HTTP helpers
    # ==================================================================

    def _url(self, path: str) -> str:
        """Build full URL for a NetBox API endpoint."""
        return f"{self._base}/{path.strip('/')}/"

    def _get_all(self, path: str) -> List[Dict[str, Any]]:
        """GET all objects from a paginated endpoint."""
        url = self._url(path) + "?limit=1000"
        results: List[Dict[str, Any]] = []
        while url:
            try:
                r = requests.get(url, headers=self._headers, verify=False)
                r.raise_for_status()
                data = r.json()
                results.extend(data.get("results", []))
                url = data.get("next")
            except Exception as e:
                print(f"  [ERROR] GET {url}: {e}")
                break
        return results

    def _post(self, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """POST a single object. Returns the created object or None."""
        if self._dry_run:
            print(f"  [DRY-RUN] POST {path}: {json.dumps(payload, default=str)[:200]}")
            return {"id": -1}  # fake ID for dry-run
        try:
            r = requests.post(
                self._url(path), headers=self._headers,
                json=payload, verify=False,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            detail = ""
            try:
                detail = r.text[:500]
            except Exception:
                pass
            print(f"  [ERROR] POST {path}: {e} -- {detail}")
            return None

    def _patch(self, path: str, obj_id: int, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """PATCH an existing object. Returns the updated object or None."""
        if self._dry_run:
            print(f"  [DRY-RUN] PATCH {path}/{obj_id}: {json.dumps(payload, default=str)[:200]}")
            return {"id": obj_id}
        try:
            r = requests.patch(
                self._url(path) + f"{obj_id}/",
                headers=self._headers,
                json=payload, verify=False,
            )
            r.raise_for_status()
            return r.json()
        except Exception as e:
            detail = ""
            try:
                detail = r.text[:500]
            except Exception:
                pass
            print(f"  [ERROR] PATCH {path}/{obj_id}: {e} -- {detail}")
            return None

    def _post_bulk(self, path: str, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """POST objects in batches. Returns list of created objects."""
        created: List[Dict[str, Any]] = []
        for i in range(0, len(items), self._CREATE_BATCH):
            batch = items[i : i + self._CREATE_BATCH]
            if self._dry_run:
                print(f"  [DRY-RUN] POST {path}: batch of {len(batch)}")
                created.extend([{"id": -1}] * len(batch))
                continue
            try:
                r = requests.post(
                    self._url(path), headers=self._headers,
                    json=batch, verify=False,
                )
                r.raise_for_status()
                result = r.json()
                if isinstance(result, list):
                    created.extend(result)
                else:
                    created.append(result)
            except Exception as e:
                print(f"  [WARN] Bulk POST {path} failed ({e}), falling back to one-by-one")
                for item in batch:
                    obj = self._post(path, item)
                    if obj:
                        created.append(obj)
        return created

    def _delete_bulk(self, path: str, ids: List[int]) -> int:
        """DELETE objects by ID in batches. Returns count of deleted."""
        deleted = 0
        for i in range(0, len(ids), self._DELETE_BATCH):
            batch = ids[i : i + self._DELETE_BATCH]
            if self._dry_run:
                print(f"  [DRY-RUN] DELETE {path}: batch of {len(batch)}")
                deleted += len(batch)
                continue
            try:
                r = requests.delete(
                    self._url(path),
                    headers=self._headers,
                    json=[{"id": oid} for oid in batch],
                    verify=False,
                )
                r.raise_for_status()
                deleted += len(batch)
            except Exception as e:
                print(f"  [WARN] Bulk DELETE {path} failed ({e}), falling back to one-by-one")
                for oid in batch:
                    try:
                        r2 = requests.delete(
                            self._url(path) + f"{oid}/",
                            headers=self._headers, verify=False,
                        )
                        r2.raise_for_status()
                        deleted += 1
                    except Exception as e2:
                        print(f"  [ERROR] DELETE {path}/{oid}: {e2}")
        return deleted

    def _get_or_create(self, path: str, slug: str, payload: Dict[str, Any]) -> int:
        """Find existing object by slug or create it. Returns NetBox ID."""
        existing = self._get_all(path)
        for obj in existing:
            if obj.get("slug") == slug:
                print(f"  [FOUND] {path} slug='{slug}' -> id={obj['id']}")
                return obj["id"]
        result = self._post(path, payload)
        if result and result.get("id"):
            print(f"  [CREATED] {path} slug='{slug}' -> id={result['id']}")
            return result["id"]
        raise RuntimeError(f"Failed to get-or-create {path} slug='{slug}'")

    # ==================================================================
    # PURGE
    # ==================================================================

    def _purge(self) -> None:
        """Delete all topology data from the target NetBox."""
        print("\n" + "=" * 60)
        print("PURGE -- Cleaning target NetBox")
        print("=" * 60)

        purge_order = [
            ("dcim/cables", "Cables"),
            ("circuits/circuits", "Circuits"),
            ("ipam/ip-addresses", "IP Addresses"),
            ("dcim/interfaces", "Interfaces"),
            ("dcim/devices", "Devices"),
            ("dcim/platforms", "Platforms"),
            ("dcim/sites", "Sites"),
            ("dcim/device-roles", "Device Roles"),
            ("dcim/device-types", "Device Types"),
            ("dcim/manufacturers", "Manufacturers"),
            ("tenancy/tenants", "Tenants"),
            ("circuits/providers", "Providers"),
            ("circuits/circuit-types", "Circuit Types"),
        ]

        for endpoint, label in purge_order:
            objects = self._get_all(endpoint)
            if not objects:
                print(f"  [PURGE] {label}: nothing to delete")
                continue

            ids = [obj["id"] for obj in objects]
            total = len(ids)
            print(f"  [PURGE] Deleting {total} {label} (bulk)...")

            count = self._delete_bulk(endpoint, ids)

            if count == total:
                print(f"  [PURGE] {label}: {count}/{total} deleted (bulk)")
            else:
                # Bulk failed or was partial — retry remaining one by one
                remaining = self._get_remaining_ids(endpoint, ids)
                if remaining:
                    print(f"  [PURGE] {label}: bulk got {count}/{total}, "
                        f"retrying {len(remaining)} individually...")
                    for obj_id in remaining:
                        try:
                            r = requests.delete(
                                self._url(endpoint) + f"{obj_id}/",
                                headers=self._headers, verify=False,
                            )
                            if r.status_code in (204, 404):
                                count += 1
                            else:
                                print(f"    [WARN] {label} id={obj_id}: "
                                    f"HTTP {r.status_code}")
                        except Exception as exc:
                            print(f"    [WARN] {label} id={obj_id}: {exc}")
                print(f"  [PURGE] {label}: {count}/{total} deleted total")

            self._stats[f"purged_{label.lower().replace(' ', '_')}"] = count

    def _get_remaining_ids(self, endpoint: str, original_ids: list) -> list:
        """Re-fetch the endpoint and return IDs that still exist."""
        still_there = self._get_all(endpoint)
        original_set = set(original_ids)
        return [obj["id"] for obj in still_there if obj["id"] in original_set]

    # ==================================================================
    # SCAFFOLD
    # ==================================================================

    def _scaffold(self) -> None:
        """Ensure manufacturer, device-type, role, provider, circuit-type.

        All slugs/names are read from ``self._config.export_netbox``
        (env-driven via ``NETBOX_MANUFACTURER_*``, ``NETBOX_DEVICE_TYPE_*``,
        ``NETBOX_DEVICE_ROLE_*``).
        """
        ec = self._config.export_netbox          # shortcut

        print("\n" + "=" * 60)
        print("SCAFFOLD -- Ensuring infrastructure objects")
        print("=" * 60)

        self._manufacturer_id = self._get_or_create(
            "dcim/manufacturers", ec.manufacturer_slug,
            {"name": ec.manufacturer_name, "slug": ec.manufacturer_slug},
        )
        self._device_type_id = self._get_or_create(
            "dcim/device-types", ec.device_type_slug,
            {
                "name": ec.device_type_name,
                "slug": ec.device_type_slug,
                "manufacturer": self._manufacturer_id,
                "model": ec.device_type_model,
            },
        )
        self._device_role_id = self._get_or_create(
            "dcim/device-roles", ec.device_role_slug,
            {"name": ec.device_role_name, "slug": ec.device_role_slug},
        )
        self._provider_id = self._get_or_create(
            "circuits/providers", "generic-provider",
            {"name": "Generic Provider", "slug": "generic-provider"},
        )
        self._circuit_type_id = self._get_or_create(
            "circuits/circuit-types", "transit",
            {"name": "Transit", "slug": "transit"},
        )

    # ==================================================================
    # CREATE
    # ==================================================================

    def _create_tenants(
        self, institutions: List[TopologyInstitution],
    ) -> None:
        """Create tenants for non-primary institutions."""
        print("\n--- Creating Tenants ---")
        count = 0
        for inst in institutions:
            if inst.is_primary:
                continue
            slug = _slugify(inst.name)
            payload = {"name": inst.name, "slug": slug}
            result = self._post("tenancy/tenants", payload)
            if result and result.get("id"):
                self._tenant_map[inst.id] = result["id"]
                count += 1
                print(f"  [TENANT] '{inst.name}' -> id={result['id']}")
        self._stats["tenants_created"] = count

    def _create_sites(self, nodes: List[TopologyNode]) -> None:
        """Create sites in target NetBox, deduplicating by site_name."""
        print("\n--- Creating Sites ---")
        count = 0
        seen_names: Dict[str, int] = {}
        pending: List[Tuple[str, Dict[str, Any]]] = []

        # 1) Build deduplicated payloads keyed by site_name
        for node in nodes:
            site_name = node.site_name or node.name
            if site_name in seen_names:
                continue
            seen_names[site_name] = -1
            slug = _slugify(site_name)
            payload: Dict[str, Any] = {
                "name": site_name,
                "slug": slug,
                "status": "active",
            }
            if node.latitude and node.longitude:
                payload["latitude"] = round(float(node.latitude), 6)
                payload["longitude"] = round(float(node.longitude), 6)
            if node.address:
                payload["physical_address"] = node.address
            # Assign tenant if node has a non-primary owner
            for oid in node.owner_ids:
                if oid in self._tenant_map:
                    payload["tenant"] = self._tenant_map[oid]
                    break
            pending.append((site_name, payload))

        # 2) Try bulk first
        payloads = [p for _, p in pending]
        names = [n for n, _ in pending]
        if payloads:
            results = self._post_bulk("dcim/sites", payloads)
            if results:
                for site_name, result in zip(names, results):
                    if result and result.get("id"):
                        seen_names[site_name] = result["id"]
                        count += 1
            else:
                # Fallback: one-by-one with get-or-create
                for site_name, payload in pending:
                    if seen_names[site_name] > 0:
                        continue
                    result = self._post("dcim/sites", payload)
                    if result and result.get("id"):
                        seen_names[site_name] = result["id"]
                        count += 1
                    else:
                        # Already exists — fetch by slug
                        existing = self._get_by_slug(
                            "dcim/sites", payload["slug"])
                        if existing:
                            seen_names[site_name] = existing["id"]

        # 3) Map EVERY node to its site id
        for node in nodes:
            site_name = node.site_name or node.name
            site_id = seen_names.get(site_name)
            if site_id and site_id > 0:
                self._site_map[node.id] = site_id

        self._stats["sites_created"] = count
        print(f"  [SITES] Created {count} sites")

    def _get_by_slug(self, endpoint: str, slug: str) -> Optional[Dict]:
        """Fetch a single object by slug from target NetBox."""
        try:
            url = self._url(endpoint) + f"?slug={slug}"
            r = requests.get(url, headers=self._headers, verify=False)
            r.raise_for_status()
            results = r.json().get("results", [])
            return results[0] if results else None
        except Exception:
            return None

    def _create_devices(self, nodes: List[TopologyNode]) -> None:
        """Create one device per topology node, placed in its site."""
        print("\n--- Creating Devices ---")
        payloads: List[Dict[str, Any]] = []
        node_order: List[str] = []
        for node in nodes:
            site_id = self._site_map.get(node.id)
            if not site_id:
                print(f"  [SKIP] No site for node '{node.name}', skipping device")
                continue
            device_name = f"{node.name}"
            payload: Dict[str, Any] = {
                "name": device_name,
                "device_type": self._device_type_id,
                "role": self._device_role_id,
                "site": site_id,
                "status": "active",
            }
            # Assign platform from source NetBox
            if node.platform:
                plat_id = self._ensure_platform(node.platform)
                if plat_id:
                    payload["platform"] = plat_id
            # Assign tenant
            for oid in node.owner_ids:
                if oid in self._tenant_map:
                    payload["tenant"] = self._tenant_map[oid]
                    break
            payloads.append(payload)
            node_order.append(node.id)

        created = self._post_bulk("dcim/devices", payloads)
        for idx, obj in enumerate(created):
            if idx < len(node_order) and obj.get("id"):
                self._device_map[node_order[idx]] = obj["id"]
        self._stats["devices_created"] = len(created)
        print(f"  [DEVICES] Created {len(created)} devices")

    def _ensure_platform(self, platform_name: str) -> Optional[int]:
        """Get or create a platform by name. Returns the NetBox platform ID."""
        if platform_name in self._platform_map:
            return self._platform_map[platform_name]
        platform_name = platform_name.strip()
        if not platform_name:
            return None
        if len(platform_name) > 100:
            print(
                "  [WARN] Platform name exceeds NetBox's 100-character limit; "
                f"skipping platform (length={len(platform_name)})."
            )
            return None
        slug = _slugify(platform_name)
        plat_id = self._get_or_create(
            "dcim/platforms", slug,
            {"name": platform_name, "slug": slug},
        )
        if plat_id:
            self._platform_map[platform_name] = plat_id
        return plat_id

    def _ensure_interface(
        self, node_id: str, link_id: str,
    ) -> Optional[int]:
        """Get or create a synthetic interface on the device for this link."""
        cache_key = (node_id, link_id)
        if cache_key in self._interface_cache:
            return self._interface_cache[cache_key]

        device_id = self._device_map.get(node_id)
        if not device_id:
            return None

        idx = self._interface_counter.get(device_id, 0)
        if self._config.common.anonymize_interfaces:
            if_name = f"{self._config.common.anonymize_interface_prefix}{idx}"
        else:
            if_name = f"eth{idx}"
        self._interface_counter[device_id] = idx + 1

        payload = {
            "device": device_id,
            "name": if_name,
            "type": "1000base-t",  # GigE -- generic
        }
        result = self._post("dcim/interfaces", payload)
        if result and result.get("id"):
            self._interface_cache[cache_key] = result["id"]
            return result["id"]
        return None

    def _create_cables(self, links: List[TopologyLink]) -> None:
        """Create cables for links whose ID starts with 'urn:netbox:cable:'."""
        print("\n--- Creating Cables ---")
        count = 0
        cable_links = [lnk for lnk in links if lnk.id.startswith("urn:netbox:cable:")]
        for link in cable_links:
            if_a = self._ensure_interface(link.node_a_id, link.id)
            if_b = self._ensure_interface(link.node_b_id, link.id)
            if not if_a or not if_b:
                print(
                    f"  [SKIP] Cable '{link.name}': "
                    f"missing interface (a={if_a}, b={if_b})"
                )
                continue
            payload = {
                "a_terminations": [
                    {"object_type": "dcim.interface", "object_id": if_a}
                ],
                "b_terminations": [
                    {"object_type": "dcim.interface", "object_id": if_b}
                ],
                "status": "connected",
            }
            result = self._post("dcim/cables", payload)
            if result and result.get("id"):
                count += 1
        self._stats["cables_created"] = count
        iface_count = len(self._interface_cache)
        self._stats["interfaces_created"] = iface_count
        print(f"  [CABLES] Created {count} cables ({iface_count} interfaces)")

    # ==================================================================
    # IP ADDRESS CREATION
    # ==================================================================

    def _next_ip(self, prefix_len: int) -> ipaddress.IPv4Address:
        """Allocate the next IP address from the configured base.

        For /31 subnets the offset is aligned to an even boundary so that
        two consecutive calls yield the two addresses of the same /31.
        For /32 (loopback) no alignment is needed.
        """
        base = ipaddress.IPv4Address(self._config.common.anonymize_ip_base)
            
        addr = base + self._ip_next
        self._ip_next += 1
        return addr

    def _create_ip_addresses(self, links: List[TopologyLink]) -> None:
        """Create IP addresses: per-cable /31 pairs and per-device /32 loopbacks.

        Runs AFTER _create_cables() and BEFORE _create_circuits().
        Uses the anonymized IP base from config.common.anonymize_ip_base.

        Strategy:
          1. For each cable link, allocate a /31 subnet.
             - Assign <subnet>.0/31 to the A-side interface.
             - Assign <subnet>.1/31 to the B-side interface.
          2. For each device, create a Loopback0 virtual interface
             with a /32 IP and set it as primary_ip4.
        """
        print("\n--- Creating IP Addresses ---")
        ip_count = 0
        loopback_count = 0

        # --- Per-cable /31 IPs ---
        cable_links = [lnk for lnk in links if lnk.id.startswith("urn:netbox:cable:")]
        for link in cable_links:
            if_a_id = self._interface_cache.get((link.node_a_id, link.id))
            if_b_id = self._interface_cache.get((link.node_b_id, link.id))
            if not if_a_id or not if_b_id:
                continue

            # Allocate a /31 pair (even-aligned)
            ip_a = self._next_ip(31)  # e.g. x.x.x.0
            ip_b = self._next_ip(31)  # e.g. x.x.x.1

            # Create IP for A-side interface
            payload_a = {
                "address": f"{ip_a}/31",
                "assigned_object_type": "dcim.interface",
                "assigned_object_id": if_a_id,
                "status": "active",
            }
            result_a = self._post("ipam/ip-addresses", payload_a)
            if result_a and result_a.get("id"):
                ip_count += 1

            # Create IP for B-side interface
            payload_b = {
                "address": f"{ip_b}/31",
                "assigned_object_type": "dcim.interface",
                "assigned_object_id": if_b_id,
                "status": "active",
            }
            result_b = self._post("ipam/ip-addresses", payload_b)
            if result_b and result_b.get("id"):
                ip_count += 1

        # --- Per-device /32 loopback IPs ---
        for node_id, device_id in self._device_map.items():
            # Create Loopback0 virtual interface
            lo_payload = {
                "device": device_id,
                "name": "Loopback0",
                "type": "virtual",
            }
            lo_result = self._post("dcim/interfaces", lo_payload)
            if not lo_result or not lo_result.get("id"):
                print(f"  [WARN] Failed to create Loopback0 on device {device_id}")
                continue
            lo_if_id = lo_result["id"]

            # Allocate /32 IP
            lo_ip = self._next_ip(32)
            ip_payload = {
                "address": f"{lo_ip}/32",
                "assigned_object_type": "dcim.interface",
                "assigned_object_id": lo_if_id,
                "status": "active",
            }
            ip_result = self._post("ipam/ip-addresses", ip_payload)
            if ip_result and ip_result.get("id"):
                ip_count += 1
                loopback_count += 1
                # Set as primary_ip4 on the device
                self._patch("dcim/devices", device_id, {
                    "primary_ip4": ip_result["id"],
                })

        self._stats["ip_addresses_created"] = ip_count
        self._stats["loopbacks_created"] = loopback_count
        print(f"  [IPs] Created {ip_count} IP addresses ({loopback_count} loopbacks)")

    def _create_circuits(self, links: List[TopologyLink]) -> None:
        """Create circuits for links whose ID starts with 'urn:netbox:circuit:'."""
        print("\n--- Creating Circuits ---")
        count = 0
        circuit_links = [lnk for lnk in links if lnk.id.startswith("urn:netbox:circuit:")]
        for link in circuit_links:
            site_a = self._site_map.get(link.node_a_id)
            site_z = self._site_map.get(link.node_b_id)
            if not site_a or not site_z:
                print(
                    f"  [SKIP] Circuit '{link.name}': "
                    f"missing site (a={site_a}, z={site_z})"
                )
                continue

            # Assign tenant to circuit
            tenant_id = None
            for oid in link.owner_ids:
                if oid in self._tenant_map:
                    tenant_id = self._tenant_map[oid]
                    break

            cid = link.name or f"circuit-{count + 1}"
            payload: Dict[str, Any] = {
                "cid": cid,
                "provider": self._provider_id,
                "type": self._circuit_type_id,
                "status": "active",
            }
            if tenant_id:
                payload["tenant"] = tenant_id

            result = self._post("circuits/circuits", payload)
            if not result or not result.get("id"):
                continue
            circuit_id = result["id"]

            # Create termination A
            self._post(
                "circuits/circuit-terminations",
                {
                    "circuit": circuit_id,
                    "term_side": "A",
                    "site": site_a,
                },
            )
            # Create termination Z
            self._post(
                "circuits/circuit-terminations",
                {
                    "circuit": circuit_id,
                    "term_side": "Z",
                    "site": site_z,
                },
            )
            count += 1

        self._stats["circuits_created"] = count
        print(f"  [CIRCUITS] Created {count} circuits")

    # ==================================================================
    # SUMMARY
    # ==================================================================

    def _build_summary(self) -> str:
        """Build a human-readable summary of all operations."""
        lines = [
            "",
            "=" * 60,
            "EXPORT SUMMARY",
            "=" * 60,
        ]
        for key, val in sorted(self._stats.items()):
            label = key.replace("_", " ").title()
            lines.append(f"  {label}: {val}")
        lines.append("=" * 60)
        summary = "\n".join(lines)
        print(summary)
        return summary

    # ==================================================================
    # ENTRY POINT
    # ==================================================================

    def export(
        self, topology: Topology, output_path: Optional[str] = None,
    ) -> str:
        """Export topology to the target NetBox instance.

        Args:
            topology: The intermediate topology object.
            output_path: Ignored (output is via API, not file).

        Returns:
            A summary string of all operations performed.
        """
        if not self._config.export_netbox.target_url or not self._config.export_netbox.target_token:
            msg = (
                "[ERROR] NetBox export requires --target-url and "
                "--target-token. Aborting."
            )
            print(msg)
            return msg

        mode = "DRY-RUN" if self._dry_run else "LIVE"
        print(f"\n{'#' * 60}")
        print(f"NetBox Export ({mode}) -> {self._config.export_netbox.target_url}")
        print(f"{'#' * 60}")

        # Step 1: Purge
        if not self._no_purge:
            self._purge()
        else:
            print("\n[SKIP] Purge disabled (--no-purge)")

        # Step 2: Scaffold
        self._scaffold()

        # Step 3: Create topology objects
        self._create_tenants(topology.institutions)
        self._create_sites(topology.nodes)
        self._create_devices(topology.nodes)
        self._create_cables(topology.links)
        self._create_ip_addresses(topology.links)
        self._create_circuits(topology.links)

        # Step 4: Summary
        return self._build_summary()