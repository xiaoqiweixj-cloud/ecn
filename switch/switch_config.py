"""Switch Telnet control via telnetlib3. Configures WRED on 400G ports."""

import asyncio
import threading
import time
import sys
import telnetlib3
from logger import get_logger

log = get_logger("switch")

ECN_PORT_LIST = ["four-hundredgige0_0", "four-hundredgige0_1", "four-hundredgige0_2"]

PROMPT_USER = b"<DPTECH>"
PROMPT_CONFIG = b"[DPTECH]"
PROMPT_DEV = b"[DPTECH-Developer]"
PROMPT_SHELL = b"[DPTECH-Developer-Shell]"

TIMEOUT = 10

VIEW_TRANSITIONS = {
    ("user", "cfg"): [("c", PROMPT_CONFIG)],
    ("user", "dev"): [("c", PROMPT_CONFIG), ("_", PROMPT_DEV)],
    ("user", "shell"): [("c", PROMPT_CONFIG), ("_", PROMPT_DEV),
                         ("_", None), ("hzdp2015", PROMPT_SHELL)],
    ("cfg", "user"): [("exit", PROMPT_USER)],
    ("cfg", "dev"): [("_", PROMPT_DEV)],
    ("cfg", "shell"): [("_", PROMPT_DEV), ("_", None), ("hzdp2015", PROMPT_SHELL)],
    ("dev", "user"): [("end", PROMPT_USER)],
    ("dev", "cfg"): [("exit", PROMPT_CONFIG)],
    ("dev", "shell"): [("_", None), ("hzdp2015", PROMPT_SHELL)],
    ("shell", "user"): [("exit", PROMPT_DEV), ("end", PROMPT_USER)],
    ("shell", "cfg"): [("exit", PROMPT_DEV), ("exit", PROMPT_CONFIG)],
    ("shell", "dev"): [("exit", PROMPT_DEV)],
}


class TelnetSocket:
    """Telnet client using telnetlib3 with background reader feeding a buffer."""

    def __init__(self, host: str, port: int, timeout: int = 15):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._reader = None
        self._writer = None
        self._buffer = b""
        self._lock = threading.Lock()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._thread.start()
        self._connect()

    def _connect(self):
        coro = telnetlib3.open_connection(self.host, self.port, encoding=False)
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        self._reader, self._writer = fut.result(timeout=self.timeout)
        asyncio.run_coroutine_threadsafe(self._reader_loop(), self._loop)
        time.sleep(0.3)

    async def _reader_loop(self):
        while True:
            try:
                data = await self._reader.read(4096)
                if not data:
                    break
                with self._lock:
                    self._buffer += data
            except Exception:
                break

    def _drain(self) -> bytes:
        with self._lock:
            data = self._buffer
            self._buffer = b""
        return data

    def read_until(self, expected: bytes, timeout: int = None) -> bytes:
        if timeout is None:
            timeout = self.timeout
        buf = b""
        start = time.time()
        while time.time() - start < timeout:
            buf += self._drain()
            if expected in buf:
                return buf
            time.sleep(0.1)
        raise EOFError(f"Timed out waiting for {expected}")

    def read_very_eager(self) -> bytes:
        return self._drain()

    def write(self, data: bytes):
        self._writer.write(data)

    def close(self):
        try:
            if self._writer:
                self._writer.close()
        except Exception:
            pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=2)
        except Exception:
            pass
        # Prevent GC hangs on Windows: both BaseEventLoop.__del__ and
        # IocpProactor.__del__ call close() which blocks on IOCP _poll().
        # Mark loop closed, and set proactor._iocp=None so its __del__
        # returns immediately (close() checks `if self._iocp is None: return`).
        # The IOCP handle is intentionally leaked — closing it would warn
        # about pending Overlapped operations, and the OS reclaims it on exit.
        try:
            self._loop._closed = True
        except Exception:
            pass
        try:
            proactor = getattr(self._loop, '_proactor', None)
            if proactor is not None:
                proactor._iocp = None
        except Exception:
            pass


class Switch:
    def __init__(self, host: str, port: int = 10020):
        self.host = host
        self.port = port
        self.tn = None

    def _connect(self):
        log.info(f"Connecting {self.host}:{self.port}...")
        try:
            self.tn = TelnetSocket(self.host, self.port, timeout=15)
            self.tn.write(b"\r\n")
            time.sleep(0.5)
            initial = self.tn.read_very_eager()
            if b"Too many telnet users" in initial:
                self.tn.close()
                raise ConnectionRefusedError("Telnet sessions full")
            log.info(f"Connected {self.host}:{self.port}")
        except ConnectionRefusedError:
            raise
        except Exception as e:
            log.error(f"Connection failed: {e}")
            raise

    def _reconnect(self):
        log.info(f"Reconnecting {self.host}:{self.port}...")
        try:
            if self.tn:
                try:
                    self.tn.close()
                except Exception:
                    pass
                self.tn = None
        except Exception:
            pass
        try:
            self._connect()
        except Exception as e:
            log.error(f"Reconnect failed: {e}")
            raise

    def _read_until(self, prompt: bytes, timeout: int = TIMEOUT) -> bytes:
        try:
            return self.tn.read_until(prompt, timeout)
        except EOFError:
            data = self.tn.read_very_eager()
            log.debug(f"EOF: {data[:200]}")
            raise

    def _check_error(self, output: bytes) -> bool:
        for pat in [b"Error", b"Invalid", b"Incomplete", b"Ambiguous",
                    b"denied", b"Unknown", b"Cannot"]:
            if pat in output:
                return True
        for line in output.split(b"\n"):
            if line.strip().startswith(b"% "):
                return True
        lower = output.lower()
        return any(w in lower for w in [b" fail ", b"\tfail ", b" fail\n", b"\tfail\n"])

    def send(self, command: str, wait_prompt: bytes = None,
             timeout: int = TIMEOUT) -> bytes:
        self.tn.write(command.encode() + b"\r\n")
        if wait_prompt:
            output = self._read_until(wait_prompt, timeout)
        else:
            time.sleep(0.5)
            output = self.tn.read_very_eager()
        if self._check_error(output):
            log.warning(f"Switch error: {output.decode('utf-8', errors='ignore')[:200]}")
        return output

    def _ensure_view(self, target: str):
        self._reconnect()
        self.login()
        self.switch_view(target)

    def connect(self):
        self._connect()
        self.login()

    def login(self):
        log.info("Waiting for prompt...")
        max_retries = 3
        for retry in range(max_retries):
            try:
                initial = self.tn.read_very_eager()
                if initial:
                    log.debug(f"Prompt: {initial[:200]}")
                    log.info("Logged in")
                    return
                self.tn.write(b"\r\n")
                time.sleep(1)
                output = self.tn.read_very_eager()
                if output:
                    log.debug(f"Prompt: {output[:200]}")
                    log.info("Logged in")
                    return
                log.warning(f"Attempt {retry + 1}/{max_retries}: no response")
                if retry < max_retries - 1:
                    time.sleep(2)
                    self._reconnect()
            except ConnectionRefusedError:
                raise
            except EOFError:
                log.warning(f"Attempt {retry + 1}/{max_retries}: closed")
                if retry < max_retries - 1:
                    time.sleep(2)
                    self._reconnect()
                else:
                    raise
            except Exception as e:
                log.error(f"Login failed: {e}")
                raise

    def get_current_view(self) -> str:
        if self.tn is None:
            log.warning("Not connected, reconnecting...")
            self._reconnect()
            self.login()
        try:
            self.tn.write(b"\r\n")
            time.sleep(0.5)
            out = self.tn.read_very_eager().decode("utf-8", errors="ignore")
        except (ConnectionResetError, BrokenPipeError, EOFError, AttributeError):
            log.warning("Connection broken, reconnecting...")
            self._reconnect()
            self.login()
            out = self.tn.read_very_eager().decode("utf-8", errors="ignore")

        if "[DPTECH-Developer-Shell]" in out:
            return "shell"
        if "[DPTECH-Developer]" in out:
            return "dev"
        if "[DPTECH" in out:
            return "cfg"
        if "<DPTECH>" in out:
            return "user"
        return "other"

    def switch_view(self, target: str):
        current = self.get_current_view()
        if current == "other":
            self.send("end", PROMPT_USER)
            current = self.get_current_view()
        if current == target:
            if target == "cfg":
                self.tn.write(b"\r\n")
                time.sleep(0.5)
                out = self.tn.read_very_eager().decode("utf-8", errors="ignore")
                if (out.strip().startswith("[DPTECH-")
                        and not out.strip().startswith("[DPTECH-Developer")):
                    self.send("exit", PROMPT_CONFIG)
            return
        transitions = VIEW_TRANSITIONS.get((current, target), [])
        for cmd, prompt in transitions:
            if prompt is None:
                self.send(cmd)
                time.sleep(1)
            else:
                self.send(cmd, prompt)

    def ecn(self, min_threshold: str, max_threshold: str, mark_probability: str):
        self.switch_view("cfg")
        for iface in ECN_PORT_LIST:
            prompt = f"[DPTECH-{iface}]".encode()
            if str(min_threshold) == "-1":
                cmd = "no qos wred queue 4 color green"
            else:
                cmd = (f"qos wred queue 4 color green static "
                       f"min-threshold {min_threshold} "
                       f"max-threshold {max_threshold} "
                       f"mark-probability {mark_probability}")
            for retry in range(2):
                try:
                    self.send(f"int {iface}", prompt)
                    self.send(cmd, prompt)
                    break
                except (ConnectionResetError, BrokenPipeError, EOFError):
                    if retry < 1:
                        log.warning(f"Connection broken, reconnecting...")
                        time.sleep(2)
                        self._ensure_view("cfg")
                    else:
                        raise
            log.info(f"  {iface} done")
        log.info("ECN config complete")

    def close(self):
        if self.tn:
            self.tn.close()
            self.tn = None
            log.info(f"Closed {self.host}:{self.port}")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    host, cmd = sys.argv[1], sys.argv[2]
    if cmd != "ecn":
        log.error(f"Unknown command: {cmd}")
        sys.exit(1)
    sw = Switch(host)
    try:
        sw.login()
        sw.ecn(
            input("min_threshold: ").strip(),
            input("max_threshold: ").strip(),
            input("mark_probability: ").strip(),
        )
    finally:
        sw.close()


if __name__ == "__main__":
    main()
