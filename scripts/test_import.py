import sys, time
sys.path.insert(0, r"D:\00Pro\PyPro\ecn2to1")
print(f"1. Importing logger at {time.strftime('%H:%M:%S')}")
from logger import get_logger
print(f"2. Importing connect at {time.strftime('%H:%M:%S')}")
from ixia.connect import IxiaSession
print(f"3. Creating session at {time.strftime('%H:%M:%S')}")
t0 = time.time()
m2n = IxiaSession()
m2n.connect()
dt = time.time() - t0
print(f"4. Connected in {dt:.1f}s, Session {m2n.session_id}")
m2n.disconnect()
print("Done")
