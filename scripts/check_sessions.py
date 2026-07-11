import requests, urllib3
urllib3.disable_warnings()
s = requests.Session()
s.verify = False
s.auth = ("admin", "Keysightixia202@")
r = s.get("https://10.140.0.204:443/api/v1/sessions", timeout=10)
data = r.json()
print(f"Response type: {type(data).__name__}, length: {len(data)}")
print(f"Raw: {data}")
