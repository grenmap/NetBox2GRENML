# NetBox to GRENML Converter

This tool provides a bridge between **NetBox API** and the **GRENML** (Global Research and Education Network Markup Language) format. It extracts network topology data—including devices, physical cables, sites, and logical circuits—and converts them into a standardized GRENML XML file.

## Key Features

- **Multi-Level Aggregation**: Supports grouping network elements by **Tenant (Owner)** or **Site**, creating a high-level view of the infrastructure.
- **Circuit Resolution**: Automatically resolves complex connections where physical cables pass through logical circuit terminations.
- **Robust Anonymization**: 
  - **Location**: Uses Reverse Geocoding to shift exact coordinates to a city/region level.
  - **IP Addresses**: Masks real primary IPs with private 192.168.x.x ranges while preserving masks.
  - **Interfaces**: Replaces interface names (e.g., `xe-0/0/0`) with generic aliases (e.g., `if-1`).
  - **Fields**: Cleans additional device properties based on exception lists.
- **Orphan Node Removal**: Option to automatically hide nodes that do not have any valid connections.
- **Data Filtering**: Specifically designed to target active infrastructure nodes. This configuration can be changed and should be adapted to every NetBox repository (`role=CHANGE-ME`).

## Prerequisites

- Python 3.x
- `requests` library
- `geopy` library
- `grenml` library

## Installation

1. Clone the repository:
   ```bash
   git clone [https://github.com/grenmap/NetBox2GRENML.git](https://github.com/grenmap/NetBox2GRENML.git)

    Install dependencies:
    Bash

    pip install requests geopy grenml

## Configuration

The script is controlled by **Global Configuration Flags** located at the top of the file.

| Flag | Description |
| :--- | :--- |
| `AGGREGATE_BY_OWNER` | Groups all active devices of the same tenant into a single node. |
| `AGGREGATE_BY_SITE` | Groups all active devices of the same site into a single node (if Owner aggregation is off). |
| `REMOVE_UNLINKED_NODES` | Removes nodes from the final XML if they have no links. |
| `ANONYMIZE_LOCATION` | Replaces exact coordinates with city-level geodata. |
| `ANONYMIZE_INTERFACES` | Masks interface names with generic prefixes (e.g., `if-`). |
| `ANONYMIZE_IPS` | Masks real IP addresses with internal private ranges. |
| `ANONYMIZE_FIELDS` | Removes extra metadata from the nodes for privacy. |

### API Credentials

In the `getCredentials()` function, insert your NetBox API token:
```
credentials = 'YOUR_NETBOX_API_TOKEN_HERE'
```
### How It Works

The script operates in a two-pass pipeline:

    Pass 1 (Draft Phase):

        Fetches all data (including inactive elements to ensure link mapping is complete).

        Resolves physical cables and logical circuits to determine which devices are connected.

        Creates a "virtual draft" of the topology nodes based on your aggregation settings.

    Pass 2 (Commit Phase):

        Filters the nodes based on the REMOVE_UNLINKED_NODES flag.

        Commits the valid nodes and institutions to the GRENML manager.

        Deduplicates and adds the links between the final nodes.

        Generates the grenml.xml file.

### Output

The result is a grenml.xml file saved in the root directory. This file is encoded in UTF-8 and is ready to be consumed by GRENML-compatible visualization or management tools.

### Security Warning

Never commit your API Token to a public repository. Use environment variables or local configuration files excluded via .gitignore for production environments.
