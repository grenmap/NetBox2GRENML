# check_roles.py
import requests
requests.packages.urllib3.disable_warnings()
headers = {"Authorization": "Token nbt_B19Tx72FSAe2.XAczm1bW2v5Mi9OE7T64jWgOezx6lH3ZwWf1U7jk", "Accept": "application/json"}
r = requests.get("https://netbox.ceo.rnp.br/api/dcim/devices/?limit=10", headers=headers, verify=False)
for d in r.json()["results"][:10]:
    print(d.get("name"), "->", (d.get("role") or {}).get("slug"))
