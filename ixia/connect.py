"""IxNetwork API Server connection."""

import os
import sys
import json
import time
import requests
import urllib3
from pathlib import Path
from typing import Any, Optional
from ixnetwork_restpy import SessionAssistant, Files
from logger import get_logger

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_DIR / "ixia_config.json"

log = get_logger("ixia")


def _load_config():
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                log_level_map = {
                    "NONE": SessionAssistant.LOGLEVEL_NONE,
                    "INFO": SessionAssistant.LOGLEVEL_INFO,
                    "WARNING": SessionAssistant.LOGLEVEL_WARNING,
                    "REQUEST": SessionAssistant.LOGLEVEL_REQUEST,
                    "ALL": SessionAssistant.LOGLEVEL_ALL,
                }
                if "log_level" in cfg and isinstance(cfg["log_level"], str):
                    cfg["log_level"] = log_level_map.get(
                        cfg["log_level"], SessionAssistant.LOGLEVEL_INFO
                    )
                return cfg
        except Exception as e:
            log.warning(f"Load config failed: {e}")

    return {
        "api_server_ip": "10.140.0.204",
        "rest_port": 443,
        "username": "admin",
        "password": "",
        "session_id": None,
        "session_name": "ixia_session",
        "clear_config": True,
        "delete_on_exit": False,
        "log_level": SessionAssistant.LOGLEVEL_INFO,
        "log_file": "logs/ixnetwork_restpy.log",
    }


CONNECT_CFG = _load_config()


def _extract_api_key(assistant):
    try:
        conn = assistant.Session._connection
        for attr in ("_api_key", "api_key"):
            val = getattr(conn, attr, None)
            if val:
                return str(val)
        headers = getattr(conn, "_headers", {}) or {}
        for key in ("x-api-key", "X-Api-Key"):
            if key in headers:
                return str(headers[key])
    except Exception as e:
        log.warning(f"Extract API Key failed: {e}")
    return None


class IxiaSession:
    def __init__(self, cfg: Optional[dict] = None):
        self.cfg = {**CONNECT_CFG, **(cfg or {})}
        self._assistant: Optional[SessionAssistant] = None
        self.ixnetwork: Any = None
        self.http: Optional[requests.Session] = None
        self.session_url: Optional[str] = None
        self.session_id: Optional[int] = None
        self.api_key: Optional[str] = None
        self._connected = False

    def connect(self):
        cfg = self.cfg
        log.info(f"Connecting {cfg['api_server_ip']}:{cfg['rest_port']} ({cfg['username']})")

        conn_kwargs = {
            "IpAddress": cfg["api_server_ip"],
            "RestPort": cfg["rest_port"],
            "UserName": cfg["username"],
            "Password": cfg["password"],
            "SessionName": cfg["session_name"],
            "ClearConfig": cfg["clear_config"],
            "LogLevel": cfg["log_level"],
            "LogFilename": cfg["log_file"],
        }
        if cfg.get("session_id") is not None:
            conn_kwargs["SessionId"] = cfg["session_id"]
            log.debug(f"Using SessionId={cfg['session_id']}")
        else:
            log.debug("No SessionId specified, will find by name or create new")

        log.debug("Creating SessionAssistant...")
        self._assistant = SessionAssistant(**conn_kwargs)
        log.debug("SessionAssistant created, getting Ixnetwork...")
        self.ixnetwork = self._assistant.Ixnetwork
        self.session_id = self._assistant.Session.Id
        log.debug(f"Session {self.session_id} ready")

        scheme = "https" if cfg["rest_port"] in (443, 8443) else "http"
        self.session_url = (
            f"{scheme}://{cfg['api_server_ip']}:{cfg['rest_port']}"
            f"/api/v1/sessions/{self.session_id}/ixnetwork"
        )

        self.api_key = _extract_api_key(self._assistant)
        self.http = self._build_http_session()
        self._connected = True

        log.info(f"Connected | Session {self.session_id}")
        self._print_server_info()
        assert self.session_url is not None
        assert self.session_id is not None
        return self

    def disconnect(self):
        if not self._connected:
            return
        if self.cfg.get("delete_on_exit") and self._assistant:
            try:
                self._assistant.Session.remove()
                log.info(f"Session {self.session_id} deleted")
            except Exception as e:
                log.warning(f"Delete session failed: {e}")
        self._connected = False
        log.info("Disconnected")

    def load_config(self, config_file):
        self._require_connected()
        assert self.ixnetwork is not None
        full_path = (config_file if os.path.isabs(config_file)
                     else str(PROJECT_DIR / config_file))
        if not os.path.isfile(full_path):
            raise FileNotFoundError(f"Config not found: {full_path}")
        log.info(f"Loading config: {full_path}")
        assert self.ixnetwork is not None
        self.ixnetwork.LoadConfig(Files(full_path, local_file=True))
        self._release_and_assign_ports()

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    def _require_connected(self):
        if not self._connected:
            raise RuntimeError("Not connected, call connect() first")

    def _build_http_session(self):
        s = requests.Session()
        s.verify = False
        if self.api_key:
            s.headers.update(
                {"Content-Type": "application/json", "x-api-key": self.api_key}
            )
        else:
            s.auth = (self.cfg["username"], self.cfg["password"])
            s.headers.update({"Content-Type": "application/json"})
        return s

    def _print_server_info(self):
        try:
            assert self.ixnetwork is not None
            log.info(f"IxNetwork {self.ixnetwork.Globals.BuildNumber}")
        except Exception:
            pass

    def _release_and_assign_ports(self):
        log.info("Port release & assign")
        assert self.http is not None
        assert self.session_url is not None
        assert self.session_id is not None
        vport_ids = []
        try:
            r = self.http.get(f"{self.session_url}/vport", verify=False, timeout=15)
            if r.status_code != 200:
                log.warning(f"Get vport list failed: HTTP {r.status_code}")
                return
            for vp in r.json():
                vp_id = vp.get("id", "")
                vport_ids.append(vp_id)
                log.info(f"  {vp.get('name', '?')}: id={vp_id} "
                         f"conn={vp.get('connectionStatus', '?')}")
        except Exception as e:
            log.warning(f"Get vport info failed: {e}")
            return

        try:
            url = f"{self.session_url}/vport/operations/connectPorts"
            refs = [f"url:/api/v1/sessions/{self.session_id}/ixnetwork/vport/{vid}"
                    for vid in vport_ids]
            r = self.http.post(url, json={"arg1": refs, "arg2": True},
                               verify=False, timeout=120)
            if r.status_code in (200, 202):
                log.info(f"connectPorts OK (HTTP {r.status_code})")
                if r.status_code == 202:
                    op_url = (f"{self.session_url}/vport/operations/connectports/"
                              f"{r.json().get('id', '')}")
                    self._wait_operation(op_url, timeout=180)
            else:
                log.warning(f"connectPorts failed: HTTP {r.status_code}")
        except Exception as e:
            log.warning(f"connectPorts failed: {e}")

        self._check_topology_and_ports()

    def _wait_operation(self, op_url, timeout=60):
        assert self.http is not None
        start = time.time()
        while time.time() - start < timeout:
            try:
                r = self.http.get(op_url, verify=False, timeout=10)
                if r.status_code == 200:
                    state = r.json().get("state", "")
                    if state in ("SUCCESS", "COMPLETED", "DONE"):
                        log.info(f"Operation done: {state}")
                        return True
                    elif state in ("ERROR", "FAILED"):
                        log.warning(f"Operation failed: {state}")
                        return False
                time.sleep(2)
            except Exception:
                time.sleep(2)
        log.warning(f"Operation timeout ({timeout}s)")
        return False

    def _check_topology_and_ports(self):
        assert self.http is not None
        assert self.session_url is not None
        try:
            r = self.http.get(f"{self.session_url}/topology", verify=False, timeout=15)
            if r.status_code == 200:
                topo_list = r.json()
                log.info(f"Topology: {len(topo_list)}")
                for t in topo_list:
                    log.info(f"  {t.get('name', '?')}: {t.get('portCount', 0)} ports")
        except Exception as e:
            log.warning(f"Topology check failed: {e}")

        try:
            r = self.http.get(f"{self.session_url}/vport", verify=False, timeout=15)
            if r.status_code == 200:
                for vp in r.json():
                    log.info(f"  {vp.get('name', '?')}: state={vp.get('state', '?')} "
                             f"conn={vp.get('isConnected', False)}")
        except Exception as e:
            log.warning(f"Vport check failed: {e}")


if __name__ == "__main__":
    ctx = IxiaSession()
    try:
        ctx.connect()
        assert ctx.http is not None
        assert ctx.session_url is not None
        assert ctx.session_id is not None
        r = ctx.http.get(ctx.session_url, verify=False, timeout=10)
        log.info(f"HTTP auth: {'OK' if r.status_code == 200 else r.status_code}")
        sessions_url = ctx.session_url.replace(
            f"/sessions/{ctx.session_id}/ixnetwork", "/sessions"
        )
        r2 = ctx.http.get(sessions_url, verify=False, timeout=10)
        if r2.status_code == 200:
            for s in r2.json():
                log.info(f"  Session {s.get('id')}: {s.get('state', '?')}")
    except Exception as e:
        log.error(f"Failed: {e}")
        sys.exit(1)
    finally:
        ctx.disconnect()
