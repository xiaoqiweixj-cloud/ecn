import requests, urllib3, time
urllib3.disable_warnings()
s = requests.Session()
s.verify = False
s.auth = ("admin", "Keysightixia202@")
print("Testing Ixia API responsiveness...")
for i in range(5):
    t0 = time.time()
    try:
        r = s.get("https://10.140.0.204:443/api/v1/availableHardware/chassis", timeout=10)
        dt = time.time() - t0
        print(f"  [{i+1}] HTTP {r.status_code} in {dt:.1f}s")
    except Exception as e:
        dt = time.time() - t0
        print(f"  [{i+1}] FAIL in {dt:.1f}s: {e}")
    time.sleep(1)
