import time
from ixnetwork_restpy import SessionAssistant

print(f"Creating SessionAssistant at {time.strftime('%H:%M:%S')}...")
t0 = time.time()
try:
    sa = SessionAssistant(
        IpAddress="10.140.0.204",
        RestPort=443,
        UserName="admin",
        Password="Keysightixia202@",
        SessionName="test_session",
        ClearConfig=True,
        LogLevel=SessionAssistant.LOGLEVEL_INFO,
        LogFilename="logs/ixnetwork_restpy.log",
    )
    dt = time.time() - t0
    print(f"OK in {dt:.1f}s, Session ID: {sa.Session.Id}")
    sa.Session.remove()
    print("Cleaned up")
except Exception as e:
    dt = time.time() - t0
    print(f"Failed in {dt:.1f}s: {e}")
