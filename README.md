# Network Topology Converter

This tool imports network topology from either the **NetBox API** or a **GRENML** (Global Research and Education Network Markup Language) XML file. It converts the source into a shared topology model, optionally anonymizes it, then exports to GRENML, a target NetBox instance, or both.

## Key Features

- **Multi-Level Aggregation**: Supports grouping network elements by **Tenant (Owner)** or **Site**, creating a high-level view of the infrastructure.
- **Circuit Resolution**: Automatically resolves complex connections where physical cables pass through logical circuit terminations.
- **Robust Anonymization**: 
  - **Location**: Uses Reverse Geocoding to shift exact coordinates to a city/region level.
  - **Site names**: Replaces site names with stable aliases (e.g., `site-1`) when enabled.
  - **Device names**: Replaces individual device names with stable aliases (e.g., `device-1`) when enabled.
  - **IP Addresses**: Masks real primary IPs with private 192.168.x.x ranges while preserving masks.
  - **Interfaces**: Replaces interface names (e.g., `xe-0/0/0`) with generic aliases (e.g., `if-1`).
  - **Fields**: Cleans additional device properties based on exception lists.
- **Orphan Node Removal**: Option to automatically hide nodes that do not have any valid connections.
- **NetBox Data Filtering**: Selects relevant active infrastructure nodes. Filters can be configured for each NetBox repository.

## Prerequisites

- Python 3.x (3.12 recommended, as grenml lib works in this version)
- `requests` library
- `geopy` library
- `grenml` library
- `python-dotenv` library (not required, but useful)

## Installation

1. Clone the repository:
   ```bash
   git clone [https://github.com/grenmap/NetBox2GRENML.git](https://github.com/grenmap/NetBox2GRENML.git)

    Install dependencies:
    Bash

    pip install requests geopy grenml

## Configuration

The script is controlled by command-line options and environment variables.

| Setting | Description |
| :--- | :--- |
| `INPUT_FORMAT` | Input source: `netbox` (default) or `grenml`. |
| `GRENML_INPUT_FILE` | GRENML XML file to import when `INPUT_FORMAT=grenml`. |
| `EXPORT_FORMAT` | Output format: `grenml`, `netbox`, or `both`. |

| Flag | Description |
| :--- | :--- |
| `AGGREGATE_BY_OWNER` | Groups all active devices of the same tenant into a single node. |
| `AGGREGATE_BY_SITE` | Groups all active devices of the same site into a single node (if Owner aggregation is off). |
| `REMOVE_UNLINKED_NODES` | Removes nodes from the final XML if they have no links. |
| `UNIFY_NETBOX_TENANTS` | Assigns all devices/sites with a source NetBox tenant to one tenant. |
| `UNIFIED_TENANT_NAME` | Sets the name of the unified tenant (default: `Unified NetBox Tenant`). |
| `ANONYMIZE_LOCATION` | Replaces exact coordinates with city-level geodata. |
| `ANONYMIZE_SITE_NAMES` | Replaces site names with stable aliases (default prefix: `site-`). |
| `ANONYMIZE_SITE_PREFIX` | Sets the prefix for anonymized site names. |
| `ANONYMIZE_DEVICE_NAMES` | Replaces individual device names with stable aliases (default prefix: `device-`). |
| `ANONYMIZE_DEVICE_PREFIX` | Sets the prefix for anonymized device names. |
| `ANONYMIZE_INTERFACES` | Masks interface names with generic prefixes (e.g., `if-`). |
| `ANONYMIZE_IPS` | Masks real IP addresses with internal private ranges. |
| `ANONYMIZE_FIELDS` | Removes extra metadata from the nodes for privacy. |

### Input and API Credentials

For NetBox input, configure `NETBOX_SOURCE_URL` and `NETBOX_SOURCE_TOKEN`, or pass `--url` and `--token`.
For GRENML input, set `INPUT_FORMAT=grenml` and `GRENML_INPUT_FILE=path/to/input.xml`, or pass `--input-format grenml --input path/to/input.xml`.
NetBox output still requires `NETBOX_TARGET_URL` and `NETBOX_TARGET_TOKEN`, or `--target-url` and `--target-token`.

Example: import a GRENML file and export it to both formats:

```bash
python main.py --input-format grenml --input topology.xml --format both \
  --target-url https://dest.netbox.example/api/ --target-token DST_TOKEN
```

### How It Works

The script operates as a pipeline:

    Import:

        Reads a NetBox API or parses and validates a GRENML XML file.

    Process:

      Builds a shared topology of institutions, nodes, and links. NetBox-specific filtering and
      aggregation are applied on the NetBox input path; configured anonymization also applies to GRENML input.

    Export:

        Writes GRENML XML, exports to a target NetBox, or does both.

### Output

The result is a grenml.xml file saved in the root directory. This file is encoded in UTF-8 and is ready to be consumed by GRENML-compatible visualization or management tools.

### Security Warning

Never commit your API Token to a public repository. Use environment variables or local configuration files excluded via .gitignore for production environments.
