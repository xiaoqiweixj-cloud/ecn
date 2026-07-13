import json, requests, urllib3
from pathlib import Path
urllib3.disable_warnings()

CONFIG_PATH = Path(__file__).resolve().parent.parent / "ixia_config.json"
if CONFIG_PATH.exists():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
else:
    cfg = {}

API_SERVER = cfg.get("api_server_ip", "10.140.0.204")
REST_PORT = cfg.get("rest_port", 443)
USERNAME = cfg.get("username", "admin")
PASSWORD = cfg.get("password", "")
BASE_URL = f"https://{API_SERVER}:{REST_PORT}"

s = requests.Session()
s.verify = False
s.auth = (USERNAME, PASSWORD)
r = s.get(f"{BASE_URL}/api/v1/sessions", timeout=10)
data = r.json()
print(f"Response type: {type(data).__name__}, length: {len(data)}")
print(f"Raw: {data}")
