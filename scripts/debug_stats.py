#!/usr/bin/env python3
"""Debug script: connect to Ixia, start traffic, inspect stats API response."""
import json
import time
import sys
from ixia.connect import IxiaSession

def main():
    m2n = IxiaSession()
    m2n.connect()
    m2n.load_config("ecn.ixncfg")

    ixn = m2n.ixnetwork

    # Stop/start protocols
    print("\n--- Stopping protocols ---")
    try:
        ixn.StopAllProtocols()
        print("  Protocols stopped")
    except Exception as e:
        print(f"  Stop failed: {e}")

    time.sleep(3)

    print("\n--- Starting protocols ---")
    try:
        ixn.StartAllProtocols()
        print("  Protocols started")
    except Exception as e:
        print(f"  Start failed: {e}")

    time.sleep(5)

    # Setup RoCEv2
    print("\n--- Setting up RoCEv2 flow groups ---")
    try:
        traffic = ixn.Traffic.find()
        traffic.AddRoCEv2FlowGroups()
        roce = traffic.RoceV2Traffic.find()
        pcs = roce.RoceV2PortConfig.find()
        for pc in pcs:
            pc.TargetLineRateInPercent = 60
            pc.update()
        print(f"  Configured {len(pcs)} port configs at 60%")
    except Exception as e:
        print(f"  RoCEv2 setup failed: {e}")

    # Apply + Start traffic
    print("\n--- Apply traffic ---")
    try:
        ixn.Traffic.Apply()
        print("  Applied")
    except Exception as e:
        print(f"  Apply failed: {e}")

    print("\n--- Start traffic ---")
    try:
        ixn.Traffic.Start()
        print("  Started")
    except Exception as e:
        print(f"  Start failed: {e}")

    time.sleep(10)

    # List all stats views
    print("\n--- Stats views ---")
    try:
        r = m2n.http.get(f"{m2n.session_url}/statistics/view", verify=False, timeout=15)
        print(f"  HTTP {r.status_code}")
        views = r.json()
        for v in views:
            print(f"  [{v.get('id')}] {v.get('caption', 'N/A')} enabled={v.get('enabled', '?')}")
    except Exception as e:
        print(f"  Failed: {e}")
        views = []

    # For each view, try to get data
    for v in views:
        vid = v.get("id")
        caption = v.get("caption", "")
        print(f"\n--- View [{vid}] {caption} ---")
        try:
            dr = m2n.http.get(f"{m2n.session_url}/statistics/view/{vid}/data", verify=False, timeout=15)
            print(f"  HTTP {dr.status_code}")
            data = dr.json()
            print(f"  Keys: {list(data.keys())}")
            raw = json.dumps(data, ensure_ascii=False, indent=2)
            if len(raw) > 2000:
                print(f"  Data (first 2000 chars):\n{raw[:2000]}")
            else:
                print(f"  Data:\n{raw}")
        except Exception as e:
            print(f"  Failed: {e}")

    # Also try enabling a specific stats view
    print("\n--- Try enabling Flow Statistics view ---")
    for v in views:
        caption = v.get("caption", "")
        if "Flow" in caption:
            vid = v.get("id")
            try:
                enable_url = f"{m2n.session_url}/statistics/view/{vid}"
                patch = {"enabled": True}
                r = m2n.http.patch(enable_url, json=patch, verify=False, timeout=15)
                print(f"  Enable [{vid}] HTTP {r.status_code}")
            except Exception as e:
                print(f"  Enable failed: {e}")
            break

    time.sleep(5)

    # Try getting data again after enabling
    print("\n--- Re-check Flow Statistics after enable ---")
    for v in views:
        caption = v.get("caption", "")
        if "Flow" in caption:
            vid = v.get("id")
            try:
                dr = m2n.http.get(f"{m2n.session_url}/statistics/view/{vid}/data", verify=False, timeout=15)
                print(f"  HTTP {dr.status_code}")
                data = dr.json()
                print(f"  Keys: {list(data.keys())}")
                raw = json.dumps(data, ensure_ascii=False, indent=2)
                if len(raw) > 2000:
                    print(f"  Data (first 2000 chars):\n{raw[:2000]}")
                else:
                    print(f"  Data:\n{raw}")
            except Exception as e:
                print(f"  Failed: {e}")
            break

    # Stop traffic
    print("\n--- Stop traffic ---")
    try:
        ixn.Traffic.Stop()
        print("  Stopped")
    except Exception as e:
        print(f"  Stop failed: {e}")

    m2n.disconnect()
    print("\nDone.")

if __name__ == "__main__":
    main()
