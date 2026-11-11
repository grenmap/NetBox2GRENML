"""NetBox -> Topology -> Export pipeline.

Usage:
    python main.py --token SRC_TOKEN                          # Uses EXPORT_FORMAT (grenml if unset)
    python main.py --token SRC_TOKEN --format netbox \\
                   --target-url https://dest.netbox.example/api/ \\
                   --target-token DST_TOKEN                   # NetBox only
    python main.py --token SRC_TOKEN --format both \\
                   --target-url https://dest.netbox.example/api/ \\
                   --target-token DST_TOKEN                   # Both
    python main.py --format netbox --dry-run ...              # Dry-run mode
    python main.py --format netbox --no-purge ...             # Skip purge
"""
from __future__ import annotations

import argparse
import sys

from config import Config, ImportConfig, ExportNetboxConfig, CommonConfig
from importers.netbox import NetBoxImporter
from processing.topology import TopologyBuilder
from exporters.grenml_exporter import GRENMLExporter
from exporters.netbox_exporter import NetBoxExporter


DEFAULT_OUTPUT_FILES = {
    "grenml": "grenml.xml",
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NetBox topology exporter (GRENML / NetBox / both)"
    )
    parser.add_argument(
        "--format", "-f",
        choices=["grenml", "netbox", "both"],
        default=None,
        help="Output format (overrides EXPORT_FORMAT; defaults to EXPORT_FORMAT, then grenml)",
    )
    parser.add_argument(
        "--output", "-o",
        default="grenml.xml",
        help="Output file path for GRENML (default: grenml.xml)",
    )
    # Source NetBox
    parser.add_argument("--token", "-t", default=None, help="Source NetBox API token")
    parser.add_argument("--url", "-u", default=None, help="Source NetBox base URL")
    # Target NetBox
    parser.add_argument(
        "--target-url", default=None,
        help="Target NetBox base URL (required for --format netbox/both)",
    )
    parser.add_argument(
        "--target-token", default=None,
        help="Target NetBox API token (required for --format netbox/both)",
    )
    # Behaviour flags
    parser.add_argument(
        "--no-purge", action="store_true", default=None,
        help="Skip the purge step on NetBox export",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=None,
        help="Print what would be done without making changes",
    )
    args = parser.parse_args()

    # --- Build nested Config ---
    source = ImportConfig()
    if args.token:
        source.token = args.token
    if args.url:
        source.base_url = args.url

    export_netbox = ExportNetboxConfig()
    if args.target_url:
        export_netbox.target_url = args.target_url
    if args.target_token:
        export_netbox.target_token = args.target_token
    if args.no_purge is not None:
        export_netbox.no_purge = args.no_purge
    if args.dry_run is not None:
        export_netbox.dry_run = args.dry_run

    common = CommonConfig()

    config = Config(
        source=source,
        export_netbox=export_netbox,
        common=common,
    )
    selected_format = args.format or config.export_format
    if selected_format not in ("grenml", "netbox", "both"):
        parser.error(
            "EXPORT_FORMAT must be one of: grenml, netbox, both"
        )

    # Validate target credentials when needed
    if selected_format in ("netbox", "both"):
        if not config.export_netbox.target_url or not config.export_netbox.target_token:
            print(
                "ERROR: --target-url and --target-token are required "
                "when the output format is 'netbox' or 'both'."
            )
            sys.exit(1)

    # --- Import ---
    importer = NetBoxImporter(config)
    raw_data = importer.fetch_all()

    # --- Process ---
    builder = TopologyBuilder(config)
    topology = builder.build(raw_data)

    print(f"\nTopology built: {len(topology.nodes)} nodes, "
          f"{len(topology.links)} links, "
          f"{len(topology.institutions)} institutions.")

    # --- Export ---
    formats_to_run = (
        ["grenml", "netbox"]
        if selected_format == "both"
        else [selected_format]
    )

    for fmt in formats_to_run:
        if fmt == "grenml":
            exporter = GRENMLExporter(config)
            output_path = args.output or DEFAULT_OUTPUT_FILES.get("grenml", "output.xml")
            exporter.export(topology, output_path=output_path)
        elif fmt == "netbox":
            exporter = NetBoxExporter(config)
            exporter.export(topology)


if __name__ == "__main__":
    main()