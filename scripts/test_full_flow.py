import sys, time
sys.path.insert(0, r"D:\00Pro\PyPro\ecn2to1")
print("Importing...", flush=True)
from switch.switch_config import Switch
from ixia.connect import IxiaSession
print("Imports OK", flush=True)

print("Switch...", flush=True)
sw = Switch("10.140.0.142", 10020)
sw.connect()
print("ECN...", flush=True)
sw.ecn("100", "200", "80")
sw.close()
print("Switch done", flush=True)

time.sleep(2)
print("Ixia...", flush=True)
m2n = IxiaSession()
m2n.connect()
print("Connected!", flush=True)
m2n.disconnect()
print("Done", flush=True)
