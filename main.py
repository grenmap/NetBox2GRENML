import json
from collections import defaultdict 
from grenml.managers import GRENMLManager
from grenml.models import Node, Institution, Link
from geopy.geocoders import Nominatim
import requests

# ====================================================================
# GLOBAL CONFIGURATION FLAGS
# ====================================================================

# 🚩 Remove nodes that do not have any links
REMOVE_UNLINKED_NODES = False 

# 🚩 Aggregate nodes by Tenant (ignores aggregation by site)
AGGREGATE_BY_OWNER = True 

# 🚩 Aggregate devices by SITE (only used if AGGREGATE_BY_OWNER is False)
AGGREGATE_BY_SITE = False 

# 🚩 Anonymize LOCATION data
ANONYMIZE_LOCATION = True 

# 🚩 Anonymize ADDITIONAL FIELDS
ANONYMIZE_FIELDS = True 

# 🚩 Anonymize Interface names/descriptions
ANONYMIZE_INTERFACES = True 

# 🚩 Prefix for interface anonymization
ANONYMIZE_INTERFACE_PREFIX = 'if-'

# 🚩 Anonymize primary_ip/primary_ip4 fields
ANONYMIZE_IPS = True

# Fields to keep even if ANONYMIZE_FIELDS is True
ANONYMIZATION_EXCEPTIONS = {'status', 'tags'}

# NetBox keys mapped directly or ignored in 'additional_properties'
HANDLED_KEYS = {
    'id', 'name', 'tenant', 'site', 'location', 'latitude', 'longitude', 
    'url', 'display_url', 'display'
}

# Geocoding Caches
REVERSE_GEOCODE_CACHE = {}
GEOCODE_CACHE = {}

# ====================================================================
# 1. API DATA COLLECTION
# ====================================================================

baseUrl = 'https://netbox.gp4l.nmaas.eu/api/'

def getCredentials():
    # ⬇️ ENTER YOUR TOKEN HERE ⬇️
    credentials = '' 
    # ⬆️ ENTER YOUR TOKEN HERE ⬆️
    
    data_number = '1000' 
    headers =  {
        'Authorization': 'Token ' + credentials,
        'Accept': 'application/json; indent=4',
    }
    if not credentials:
        print("WARNING: The 'credentials' variable is empty.")
    return credentials, data_number, headers

def getDevices(data_number, headers):
    url = baseUrl + 'dcim/devices/?limit=' + data_number
    print(f"Fetching ALL Devices from: {url}")
    return get_paginated_data(url, headers)

def getCables(data_number, headers):
    url = baseUrl + 'dcim/cables/?limit=' + data_number
    print(f"Fetching Cables from: {url}")
    return get_paginated_data(url, headers)

def getSites(data_number, headers):
    url = baseUrl + 'dcim/sites/?limit=' + data_number
    print(f"Fetching Sites from: {url}")
    return get_paginated_data(url, headers)

def getCircuits(data_number, headers):
    url = baseUrl + 'circuits/circuits/?limit=' + data_number
    print(f"Fetching Circuits from: {url}")
    return get_paginated_data(url, headers)

def get_paginated_data(url, headers):
    try:
        requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)
        response = requests.get(url, headers=headers, verify=False)
        response.raise_for_status()
        data = response.json()
        nextURL = data.get('next')
        while type(nextURL) == str:
            print(f"Fetching next page: {nextURL}")
            current_response = requests.get(nextURL, headers=headers, verify=False)
            current_response.raise_for_status()
            currentData = current_response.json()
            data['results'] += currentData['results']
            nextURL = currentData.get('next')
        print(f"Fetch completed. Total of {len(data.get('results', []))} items.")
        return data
    except Exception as err:
        print(f"An unexpected error occurred: {err}")
    return {"results": []}

# --- Collection Execution ---
credentials, data_number, headers = getCredentials()
all_devices_data = getDevices(data_number, headers)
cables_data = getCables(data_number, headers)
sites_data = getSites(data_number, headers)
circuits_data = getCircuits(data_number, headers)

sites_map = {site['id']: site for site in sites_data.get('results', [])}

# ====================================================================
# 3. HELPER FUNCTIONS
# ====================================================================

def geocode_from_description(description_text):
    geolocator = Nominatim(user_agent="grenml_netbox_converter")
    if description_text in GEOCODE_CACHE:
        return GEOCODE_CACHE[description_text]
    try:
        location = geolocator.geocode(description_text, addressdetails=True)
        if location:
            city = location.raw.get('address', {}).get('city', location.raw.get('address', {}).get('town'))
            result = (location.latitude, location.longitude, city)
            GEOCODE_CACHE[description_text] = result
            return result
    except Exception as e:
        print(f"Geocoding Error: {e}")
    GEOCODE_CACHE[description_text] = (None, None, None)
    return (None, None, None)

def reverse_geocode(lat, lon):
    geolocator = Nominatim(user_agent="grenml_netbox_converter_reverse")
    cache_key = (lat, lon)
    if cache_key in REVERSE_GEOCODE_CACHE:
        return REVERSE_GEOCODE_CACHE[cache_key]
    try:
        location = geolocator.reverse((lat, lon), addressdetails=True, language='en')
        if location and location.raw.get('address'):
            address = location.raw.get('address', {})
            city = address.get('city', address.get('town', address.get('village')))
            state = address.get('state')
            country = address.get('country')
            anon_address_string = ", ".join(filter(None, [city, state, country]))
            if anon_address_string:
                REVERSE_GEOCODE_CACHE[cache_key] = anon_address_string
                return anon_address_string
    except Exception as e:
        print(f"Reverse Geocoding Error: {e}")
    REVERSE_GEOCODE_CACHE[cache_key] = None
    return None

def get_location_data(device, sites_map, anonymize=False):
    location_obj = device.get('location') 
    site_minimal = device.get('site')
    site_full = None
    if site_minimal and site_minimal.get('id') in sites_map:
        site_full = sites_map.get(site_minimal.get('id'))
    
    original_lat, original_lon = None, None
    original_lat = device.get('latitude') 
    original_lon = device.get('longitude') 

    if original_lat is None and site_full:
        original_lat = site_full.get('latitude')
        original_lon = site_full.get('longitude')

    if original_lat is None and site_full:
        fields_to_try = [site_full.get('address'), site_full.get('physical_address'), site_full.get('description'), site_full.get('name')]
        for field_text in fields_to_try:
            if field_text: 
                lat_geo, lon_geo, city_geo = geocode_from_description(field_text)
                if lat_geo:
                    original_lat, original_lon = lat_geo, lon_geo
                    break 
    
    if original_lat is None:
        return 0, 0, None 

    if anonymize:
        anonymized_address_string = reverse_geocode(original_lat, original_lon)
        if anonymized_address_string:
            lat_anon, lon_anon, _ = geocode_from_description(anonymized_address_string)
            if lat_anon is not None:
                return lat_anon, lon_anon, anonymized_address_string
            return original_lat, original_lon, anonymized_address_string
        return original_lat, original_lon, None
    else:
        adr_parts = [location_obj.get('name') if location_obj else None, site_minimal.get('name') if site_minimal else None]
        final_adr = ", ".join(filter(None, adr_parts))
        return original_lat, original_lon, (final_adr if final_adr else None)

def populate_additional_properties(node, device_data, ignored_keys, anonymize=False, exceptions=set(), anonymize_ips=False, ip_counter=None):
    anonymized_ip_address = None
    if anonymize_ips and ip_counter and (device_data.get('primary_ip') or device_data.get('primary_ip4')):
        ip_base = f"192.168.{ip_counter[0]}.{ip_counter[1]}"
        ip_counter[1] += 1
        if ip_counter[1] > 254:
            ip_counter[1] = 1
            ip_counter[0] += 1
        mask = None
        for key in ('primary_ip4', 'primary_ip'):
            data = device_data.get(key)
            if data and data.get('address'):
                parts = str(data['address']).split('/')
                if len(parts) > 1: mask = parts[1]; break
        anonymized_ip_address = f"{ip_base}/{mask}" if mask else ip_base

    for key, value in device_data.items():
        if key in ignored_keys or value in [None, {}, []]: continue
        if anonymized_ip_address and key in ('primary_ip', 'primary_ip4'):
            value = {'family': 4, 'address': anonymized_ip_address}
        if anonymize and key not in exceptions: continue
        node.add_property(key, json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value))

def get_device_from_termination(term):
    if term.get('object_type') == 'dcim.interface':
        return term.get('object', {}).get('device', {}).get('id')
    return None

def get_circuit_term_from_termination(term):
    if term.get('object_type') == 'circuits.circuittermination':
        return term.get('object', {}).get('id')
    return None

def anonymize_termination_data(termination_list, alias_map, counter_state, prefix='if-'):
    if not termination_list: return termination_list
    for term in termination_list:
        if term.get('object_type') == 'dcim.interface' and 'object' in term:
            if_id = term['object']['id']
            if if_id not in alias_map:
                alias_map[if_id] = f"{prefix}{counter_state[0]}"
                counter_state[0] += 1
            term['object']['name'] = term['object']['display'] = alias_map[if_id]
            term['object']['description'] = "" 
    return termination_list

def extract_link_endpoints(cable, aggregate_by_site, aggregate_by_owner, rep_urns_site, dev_site_map, rep_urns_owner, dev_owner_map):
    try:
        a_term, b_term = cable['a_terminations'][0], cable['b_terminations'][0]
        if a_term.get('object_type') != 'dcim.interface' or b_term.get('object_type') != 'dcim.interface':
            return None, None, None, None
        dev_a_id, dev_b_id = a_term['object']['device']['id'], b_term['object']['device']['id']
        link_id = f"urn:netbox:cable:{cable['id']}"
        link_name = cable.get('display') or f"Cable {cable['id']}"

        if aggregate_by_owner:
            node_a_urn, node_b_urn = rep_urns_owner.get(dev_owner_map.get(dev_a_id)), rep_urns_owner.get(dev_owner_map.get(dev_b_id))
        elif aggregate_by_site:
            node_a_urn, node_b_urn = rep_urns_site.get(dev_site_map.get(dev_a_id)), rep_urns_site.get(dev_site_map.get(dev_b_id))
        else:
            node_a_urn, node_b_urn = f"urn:netbox:device:{dev_a_id}", f"urn:netbox:device:{dev_b_id}"
        return link_id, link_name, node_a_urn, node_b_urn
    except: return None, None, None, None

# ====================================================================
# 4. PROCESSING
# ====================================================================

manager = GRENMLManager(name="NetBox Topology")
generic_owner_id = "urn:org:generic-owner1"
device_to_site_map, device_to_owner_map, all_tenants_data_map = {}, {}, {}

for device in all_devices_data.get('results', []):
    dev_id = device.get('id')
    if not dev_id: continue
    site_id = device.get('site', {}).get('id')
    tenant_id = device.get('tenant', {}).get('id')
    if site_id: device_to_site_map[dev_id] = site_id
    if tenant_id:
        device_to_owner_map[dev_id] = f"urn:netbox:tenant:{tenant_id}"
        all_tenants_data_map[tenant_id] = device['tenant']
    else: device_to_owner_map[dev_id] = generic_owner_id

rnp_owner = Institution(id=generic_owner_id, name='generic-owner')
manager.add_institution(rnp_owner, primary_owner=True)
institutions_by_urn_map = {rnp_owner.id: rnp_owner}
tenant_institutions_map = {}

for t_id, t_data in all_tenants_data_map.items():
    urn = f"urn:netbox:tenant:{t_id}"
    inst = Institution(id=urn, name=t_data['name'])
    manager.add_institution(inst)
    tenant_institutions_map[t_id] = institutions_by_urn_map[urn] = inst

circuit_termination_to_device_map, device_links, resolved_circuits_ids = {}, [], set()
for cable in cables_data.get('results', []):
    try:
        t_a, t_b = cable['a_terminations'][0], cable['b_terminations'][0]
        d_a, d_b = get_device_from_termination(t_a), get_device_from_termination(t_b)
        c_a, c_b = get_circuit_term_from_termination(t_a), get_circuit_term_from_termination(t_b)
        if d_a and d_b: device_links.append((d_a, d_b, cable))
        elif d_a and c_b: circuit_termination_to_device_map[c_b] = d_a
        elif c_a and d_b: circuit_termination_to_device_map[c_a] = d_b
    except: continue

for circuit in circuits_data.get('results', []):
    try:
        d_a, d_b = circuit_termination_to_device_map.get(circuit['termination_a']['id']), circuit_termination_to_device_map.get(circuit['termination_z']['id'])
        if d_a and d_b: device_links.append((d_a, d_b, circuit)); resolved_circuits_ids.add(circuit['id'])
    except: continue

processed_nodes_map, representative_devices_site, representative_device_urns_site = {}, {}, {}
representative_devices_owner, representative_device_urns_owner, site_to_owner_urn_map = {}, {}, {}
if_alias_map, if_alias_counter, ip_anonymization_counter = {}, [1], [1, 1]

devices_to_process = [d for d in all_devices_data.get('results', []) if d.get('role', {}).get('slug') == 'gp4l-node' and d.get('status', {}).get('value') == 'active']

if AGGREGATE_BY_OWNER:
    for device in all_devices_data.get('results', []):
        urn = device_to_owner_map.get(device['id'], generic_owner_id)
        if urn not in representative_devices_owner:
            representative_devices_owner[urn] = device
            representative_device_urns_owner[urn] = urn
    for device in devices_to_process:
        urn = device_to_owner_map.get(device['id'], generic_owner_id)
        site_id = device_to_site_map.get(device['id'])
        if site_id: site_to_owner_urn_map[site_id] = urn
        if urn not in processed_nodes_map:
            inst = institutions_by_urn_map.get(urn)
            rep = representative_devices_owner[urn]
            lat, lon, adr = get_location_data(rep, sites_map, ANONYMIZE_LOCATION)
            owners = [rnp_owner] + ([inst] if inst != rnp_owner else [])
            node = Node(id=urn, name=inst.name, short_name=inst.name, latitude=lat, longitude=lon, address=adr, owners=owners)
            processed_nodes_map[urn] = (node, owners)
            populate_additional_properties(node, rep, HANDLED_KEYS, ANONYMIZE_FIELDS, ANONYMIZATION_EXCEPTIONS, ANONYMIZE_IPS, ip_anonymization_counter)

elif AGGREGATE_BY_SITE:
    for device in all_devices_data.get('results', []):
        s_id = device.get('site', {}).get('id')
        if s_id and s_id not in representative_devices_site:
            representative_devices_site[s_id] = device
            representative_device_urns_site[s_id] = f"urn:netbox:device:{device['id']}"
    for device in devices_to_process:
        s_id = device.get('site', {}).get('id')
        if s_id:
            urn = representative_device_urns_site.get(s_id)
            if urn and urn not in processed_nodes_map:
                s_full = sites_map.get(s_id)
                name = s_full.get('name') if s_full else device['name']
                lat, lon, adr = get_location_data(representative_devices_site[s_id], sites_map, ANONYMIZE_LOCATION)
                owners = [rnp_owner] + ([tenant_institutions_map[device['tenant']['id']]] if device.get('tenant') and device['tenant']['id'] in tenant_institutions_map else [])
                node = Node(id=urn, name=name, short_name=name, latitude=lat, longitude=lon, address=adr, owners=owners)
                processed_nodes_map[urn] = (node, owners)
                populate_additional_properties(node, representative_devices_site[s_id], HANDLED_KEYS, ANONYMIZE_FIELDS, ANONYMIZATION_EXCEPTIONS, ANONYMIZE_IPS, ip_anonymization_counter)
else:
    for device in devices_to_process:
        urn = f"urn:netbox:device:{device['id']}"
        lat, lon, adr = get_location_data(device, sites_map, ANONYMIZE_LOCATION)
        owners = [rnp_owner] + ([tenant_institutions_map[device['tenant']['id']]] if device.get('tenant') and device['tenant']['id'] in tenant_institutions_map else [])
        node = Node(id=urn, name=device['name'], short_name=device['name'], latitude=lat, longitude=lon, address=adr, owners=owners)
        processed_nodes_map[urn] = (node, owners)
        populate_additional_properties(node, device, HANDLED_KEYS, ANONYMIZE_FIELDS, ANONYMIZATION_EXCEPTIONS, ANONYMIZE_IPS, ip_anonymization_counter)

linked_node_urns = set()
for d_a, d_b, _ in device_links:
    if AGGREGATE_BY_OWNER: n_a, n_b = representative_device_urns_owner.get(device_to_owner_map.get(d_a)), representative_device_urns_owner.get(device_to_owner_map.get(d_b))
    elif AGGREGATE_BY_SITE: n_a, n_b = representative_device_urns_site.get(device_to_site_map.get(d_a)), representative_device_urns_site.get(device_to_site_map.get(d_b))
    else: n_a, n_b = f"urn:netbox:device:{d_a}", f"urn:netbox:device:{d_b}"
    if n_a in processed_nodes_map and n_b in processed_nodes_map and n_a != n_b: linked_node_urns.update([n_a, n_b])

if AGGREGATE_BY_OWNER or AGGREGATE_BY_SITE:
    for circuit in circuits_data['results']:
        if circuit['id'] in resolved_circuits_ids: continue
        try:
            s_a, s_z = circuit['termination_a']['site']['id'], circuit['termination_z']['site']['id']
            if AGGREGATE_BY_OWNER: n_a, n_b = representative_device_urns_owner.get(site_to_owner_urn_map.get(s_a)), representative_device_urns_owner.get(site_to_owner_urn_map.get(s_z))
            else: n_a, n_b = representative_device_urns_site.get(s_a), representative_device_urns_site.get(s_z)
            if n_a in processed_nodes_map and n_b in processed_nodes_map and n_a != n_b: linked_node_urns.update([n_a, n_b])
        except: continue

final_added_nodes_urns = set()
for urn, (node_obj, _) in processed_nodes_map.items():
    if not REMOVE_UNLINKED_NODES or urn in linked_node_urns:
        manager.add_node(node_obj)
        final_added_nodes_urns.add(urn)

created_links_tracker = set()
for cable in cables_data['results']:
    l_id, l_name, n_a, n_b = extract_link_endpoints(cable, AGGREGATE_BY_SITE, AGGREGATE_BY_OWNER, representative_device_urns_site, device_to_site_map, representative_device_urns_owner, device_to_owner_map)
    if n_a in final_added_nodes_urns and n_b in final_added_nodes_urns and n_a != n_b:
        key = tuple(sorted([n_a, n_b]))
        if key not in created_links_tracker:
            n_a_obj, o_a = processed_nodes_map[n_a]; n_b_obj, o_b = processed_nodes_map[n_b]
            link = Link(id=l_id, name=l_name, owners=list(set(o_a) | set(o_b)), nodes=[n_a_obj, n_b_obj])
            a_t, b_t = cable.get('a_terminations'), cable.get('b_terminations')
            if ANONYMIZE_INTERFACES:
                a_t = anonymize_termination_data(a_t, if_alias_map, if_alias_counter, ANONYMIZE_INTERFACE_PREFIX)
                b_t = anonymize_termination_data(b_t, if_alias_map, if_alias_counter, ANONYMIZE_INTERFACE_PREFIX)
            if a_t: link.add_property('a_terminations', json.dumps(a_t))
            if b_t: link.add_property('b_terminations', json.dumps(b_t))
            manager.add_link(link); created_links_tracker.add(key)

if AGGREGATE_BY_OWNER or AGGREGATE_BY_SITE:
    for circuit in circuits_data['results']:
        if circuit['id'] in resolved_circuits_ids: continue
        try:
            s_a, s_z = circuit['termination_a']['site']['id'], circuit['termination_z']['site']['id']
            if AGGREGATE_BY_OWNER: n_a, n_b = representative_device_urns_owner.get(site_to_owner_urn_map.get(s_a)), representative_device_urns_owner.get(site_to_owner_urn_map.get(s_z))
            else: n_a, n_b = representative_device_urns_site.get(s_a), representative_device_urns_site.get(s_z)
            if n_a in final_added_nodes_urns and n_b in final_added_nodes_urns and n_a != n_b:
                key = tuple(sorted([n_a, n_b]))
                if key not in created_links_tracker:
                    n_a_obj, o_a = processed_nodes_map[n_a]; n_b_obj, o_b = processed_nodes_map[n_b]
                    owners = [rnp_owner] + ([tenant_institutions_map[circuit['tenant']['id']]] if circuit.get('tenant') and circuit['tenant']['id'] in tenant_institutions_map else [])
                    link = Link(id=f"urn:netbox:circuit:{circuit['id']}", name=circuit.get('cid') or f"Link {circuit['id']}", owners=owners, nodes=[n_a_obj, n_b_obj])
                    if circuit.get('termination_a'): link.add_property('termination_a', json.dumps(circuit['termination_a']))
                    if circuit.get('termination_z'): link.add_property('termination_z', json.dumps(circuit['termination_z']))
                    manager.add_link(link); created_links_tracker.add(key)
        except: continue

try:
    with open("grenml.xml", "w", encoding="utf-8") as f:
        f.write(manager.write_to_string())
    print("\n[SUCCESS] 'grenml.xml' file saved correctly.")
except Exception as e:
    print(f"\n[ERROR]: {e}")
