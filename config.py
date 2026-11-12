from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

# Optional: load .env file if python-dotenv is installed
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _env(name: str, default: str = "") -> str:
    """Read an environment variable, falling back to *default*."""
    return os.environ.get(name, default)


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean env var (truthy: 1, true, yes, on)."""
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# ------------------------------------------------------------------
# Device filtering
# ------------------------------------------------------------------

@dataclass
class DeviceFilter:
    """A single filter criterion applied to a device dict.

    `field` is a dot-path into the device JSON (e.g. 'role.slug').
    `value` is the expected string value.
    `match_type` can be 'exact' (default) or 'contains'.
    """
    field: str = ""
    value: str = ""
    match_type: str = "exact"   # 'exact' | 'contains'


@dataclass
class FilterGroup:
    """A group of DeviceFilters combined by `logic` (and | or).

    Multiple FilterGroups are always combined with OR between them.
    """
    logic: str = "and"   # 'and' | 'or'
    filters: List[DeviceFilter] = field(default_factory=list)


# Default filter string — used when DEVICE_FILTERS env var is unset.
_DEFAULT_FILTERS = (
    "or:role.slug=aggregation,role.slug=provider-edge,"
    "role.slug=border,role.slug=core-router,role.slug=gp4l-node"
    "|"
    "and:tags.slug~core,status.value=active"
)


def _parse_filter_expr(expr: str) -> DeviceFilter:
    """Parse a single filter expression like ``field=value`` or ``field~value``.

    ``=`` → exact match, ``~`` → contains match.
    """
    if "~" in expr:
        fld, val = expr.split("~", 1)
        return DeviceFilter(field=fld.strip(), value=val.strip(), match_type="contains")
    if "=" in expr:
        fld, val = expr.split("=", 1)
        return DeviceFilter(field=fld.strip(), value=val.strip(), match_type="exact")
    # Fallback: treat the whole thing as field with empty value
    return DeviceFilter(field=expr.strip(), value="", match_type="exact")


def _parse_filters(raw: str) -> List[FilterGroup]:
    """Parse the ``DEVICE_FILTERS`` env-var string into FilterGroups.

    Syntax
    ------
    Groups separated by ``|`` (OR between groups).
    Each group starts with an optional logic prefix ``and:`` or ``or:``
    (default ``and``), followed by comma-separated filter expressions.

    Filter expression: ``field=value`` (exact) or ``field~value`` (contains).

    Example
    -------
    ``or:role.slug=aggregation,role.slug=border|and:tags.slug~core,status.value=active``

    → Group 1 (OR): role.slug == aggregation OR role.slug == border
    → Group 2 (AND): tags.slug contains "core" AND status.value == active
    → Groups are OR-ed: device passes if it matches Group 1 OR Group 2.
    """
    if not raw or not raw.strip():
        return []
    groups: List[FilterGroup] = []
    for group_str in raw.split("|"):
        group_str = group_str.strip()
        if not group_str:
            continue
        # Detect logic prefix
        logic = "and"
        if group_str.lower().startswith("or:"):
            logic = "or"
            group_str = group_str[3:]
        elif group_str.lower().startswith("and:"):
            logic = "and"
            group_str = group_str[4:]
        filters = []
        for part in group_str.split(","):
            part = part.strip()
            if part:
                filters.append(_parse_filter_expr(part))
        if filters:
            groups.append(FilterGroup(logic=logic, filters=filters))
    return groups


def _env_filters() -> List[FilterGroup]:
    """Read device filters from env, falling back to built-in defaults."""
    raw = os.environ.get("DEVICE_FILTERS")
    if raw is None:
        return _parse_filters(_DEFAULT_FILTERS)
    if not raw.strip():
        return []  # explicitly empty → no filters (accept all devices)
    return _parse_filters(raw)


# ------------------------------------------------------------------
# Section configs
# ------------------------------------------------------------------

@dataclass
class ImportConfig:
    """Input source settings for NetBox or GRENML."""
    input_format: str = field(default_factory=lambda: _env(
        "INPUT_FORMAT", "netbox"))
    grenml_file: str = field(default_factory=lambda: _env(
        "GRENML_INPUT_FILE", ""))
    base_url: str = field(default_factory=lambda: _env(
        "NETBOX_SOURCE_URL", "your-netbox.example.com"))
    token: str = field(default_factory=lambda: _env(
        "NETBOX_SOURCE_TOKEN", ""))
    page_size: str = field(default_factory=lambda: _env(
        "NETBOX_SOURCE_PAGE_SIZE", "1000"))
    device_filters: List[FilterGroup] = field(default_factory=_env_filters)


@dataclass
class ExportNetboxConfig:
    """NetBox TARGET connection settings (for NetBox exporter)."""
    target_url: str = field(default_factory=lambda: _env(
        "NETBOX_TARGET_URL", ""))
    target_token: str = field(default_factory=lambda: _env(
        "NETBOX_TARGET_TOKEN", ""))
    no_purge: bool = field(default_factory=lambda: _env_bool(
        "NETBOX_TARGET_NO_PURGE", False))
    dry_run: bool = field(default_factory=lambda: _env_bool(
        "NETBOX_TARGET_DRY_RUN", False))

    # --- Scaffold: manufacturer, device-type, device-role ---
    manufacturer_name: str = field(default_factory=lambda: _env(
        "NETBOX_MANUFACTURER_NAME", "Generic"))
    manufacturer_slug: str = field(default_factory=lambda: _env(
        "NETBOX_MANUFACTURER_SLUG", "generic"))
    device_type_name: str = field(default_factory=lambda: _env(
        "NETBOX_DEVICE_TYPE_NAME", "Generic Router"))
    device_type_slug: str = field(default_factory=lambda: _env(
        "NETBOX_DEVICE_TYPE_SLUG", "generic-router"))
    device_type_model: str = field(default_factory=lambda: _env(
        "NETBOX_DEVICE_TYPE_MODEL", "Generic Router"))
    device_role_name: str = field(default_factory=lambda: _env(
        "NETBOX_DEVICE_ROLE_NAME", "Generic Node"))
    device_role_slug: str = field(default_factory=lambda: _env(
        "NETBOX_DEVICE_ROLE_SLUG", "generic-node"))



@dataclass
class ExportGrenmlConfig:
    """GRENML export settings (placeholder for future options)."""
    pass


def _env_set(name: str, default: str = "status,tags") -> Set[str]:
    """Read a comma-separated env var into a set of strings."""
    val = os.environ.get(name)
    if val is None:
        return {s.strip() for s in default.split(",") if s.strip()}
    return {s.strip() for s in val.split(",") if s.strip()}


@dataclass
class CommonConfig:
    """Aggregation, anonymization, and internal constants."""

    # --- Aggregation ---
    aggregate_by_owner: bool = field(default_factory=lambda: _env_bool(
        "AGGREGATE_BY_OWNER", False))
    aggregate_by_site: bool = field(default_factory=lambda: _env_bool(
        "AGGREGATE_BY_SITE", False))
    remove_unlinked_nodes: bool = field(default_factory=lambda: _env_bool(
        "REMOVE_UNLINKED_NODES", False))
    unify_netbox_tenants: bool = field(default_factory=lambda: _env_bool(
        "UNIFY_NETBOX_TENANTS", False))
    unified_tenant_name: str = field(default_factory=lambda: _env(
        "UNIFIED_TENANT_NAME", "Unified NetBox Tenant"))

    # --- Anonymization ---
    anonymize_location: bool = field(default_factory=lambda: _env_bool(
        "ANONYMIZE_LOCATION", False))
    anonymize_site_names: bool = field(default_factory=lambda: _env_bool(
        "ANONYMIZE_SITE_NAMES", False))
    anonymize_site_prefix: str = field(default_factory=lambda: _env(
        "ANONYMIZE_SITE_PREFIX", "site-"))
    anonymize_device_names: bool = field(default_factory=lambda: _env_bool(
        "ANONYMIZE_DEVICE_NAMES", False))
    anonymize_device_prefix: str = field(default_factory=lambda: _env(
        "ANONYMIZE_DEVICE_PREFIX", "device-"))
    anonymize_fields: bool = field(default_factory=lambda: _env_bool(
        "ANONYMIZE_FIELDS", False))
    anonymize_interfaces: bool = field(default_factory=lambda: _env_bool(
        "ANONYMIZE_INTERFACES", False))
    anonymize_interface_prefix: str = field(default_factory=lambda: _env(
        "ANONYMIZE_INTERFACE_PREFIX", "if-"))
    anonymize_ips: bool = field(default_factory=lambda: _env_bool(
        "ANONYMIZE_IPS", True))
    anonymize_ip_base: str = field(default_factory=lambda: _env(
        "ANONYMIZE_IP_BASE", "192.168.0.0"))
    anonymization_exceptions: Set[str] = field(
        default_factory=lambda: _env_set(
            "ANONYMIZATION_EXCEPTIONS", "status,tags"))

    # --- Internal constants ---
    handled_keys: Set[str] = field(
        default_factory=lambda: _env_set(
            "HANDLED_KEYS",
            "id,name,tenant,site,location,latitude,longitude,url,display_url,display"))
    generic_owner_id: str = field(default_factory=lambda: _env(
        "GENERIC_OWNER_ID", "urn:org:generic-owner1"))
    generic_owner_name: str = field(default_factory=lambda: _env(
        "GENERIC_OWNER_NAME", "generic-owner"))
    device_role_slug: str = field(default_factory=lambda: _env(
        "DEVICE_ROLE_SLUG", "generic-node"))


# ------------------------------------------------------------------
# Root config
# ------------------------------------------------------------------

@dataclass
class Config:
    """Root configuration — aggregates all sections."""
    source: ImportConfig = field(default_factory=ImportConfig)
    export_netbox: ExportNetboxConfig = field(default_factory=ExportNetboxConfig)
    export_grenml: ExportGrenmlConfig = field(default_factory=ExportGrenmlConfig)
    common: CommonConfig = field(default_factory=CommonConfig)

    # Execution mode: grenml | netbox | both
    export_format: str = field(default_factory=lambda: _env(
        "EXPORT_FORMAT", "grenml"))


def resolve_dot_path(obj: Dict[str, Any], dot_path: str) -> Any:
    """Walk a dot-separated path into a nested dict / list.

    For list values (e.g. device['tags'] is a list of dicts), the
    remaining path is tested against EACH element and the function
    returns a *list* of leaf values so the caller can do ``in`` checks.
    """
    parts = dot_path.split(".")
    cur: Any = obj
    for i, part in enumerate(parts):
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list):
            remaining = ".".join(parts[i:])
            results = []
            for item in cur:
                val = resolve_dot_path(item, remaining)
                if isinstance(val, list):
                    results.extend(val)
                elif val is not None:
                    results.append(val)
            return results if results else None
        else:
            return None
    return cur


def eval_single_filter(device: Dict[str, Any], f: DeviceFilter) -> bool:
    """Evaluate one DeviceFilter against a device dict."""
    resolved = resolve_dot_path(device, f.field)
    if resolved is None:
        return False
    if isinstance(resolved, list):
        if f.match_type == "contains":
            return any(f.value in str(v) for v in resolved)
        return any(str(v) == f.value for v in resolved)
    if f.match_type == "contains":
        return f.value in str(resolved)
    return str(resolved) == f.value


def device_matches_filters(
    device: Dict[str, Any],
    filter_groups: List[FilterGroup],
) -> bool:
    """Return True if *device* passes the given filter groups.

    FilterGroups are OR-ed; within each group the filters are combined
    by ``group.logic`` ('and' | 'or').
    No filters / empty list -> accept all.
    """
    if not filter_groups:
        return True
    for group in filter_groups:
        if not group.filters:
            continue
        if group.logic == "or":
            if any(eval_single_filter(device, f) for f in group.filters):
                return True
        else:
            if all(eval_single_filter(device, f) for f in group.filters):
                return True
    return False