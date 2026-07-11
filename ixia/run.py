"""IxNetwork traffic control with RoCEv2 rate monitoring."""

import csv
import time
import traceback
from pathlib import Path
from datetime import datetime
from ixia.connect import IxiaSession
from logger import get_logger

PROJECT_DIR = Path(__file__).resolve().parent.parent

PROTOCOL_START_DELAY = 5
STARTUP_DELAY = 10

log = get_logger("ixia")


class IxiaRunner:
    """Connect once, then start/stop traffic per ECN iteration."""

    def __init__(self, config_file: str = "ecn.ixncfg"):
        self._m2n: IxiaSession | None = None
        self._config_file = config_file
        self._flow_view_id = None
        self._rate_col_index = None
        self._rate_col_name = None

    # ---- lifecycle ----

    def connect(self):
        self._m2n = IxiaSession()
        self._m2n.connect()
        self._m2n.load_config(self._config_file)
        _stop_all_protocols(self._m2n.ixnetwork)
        time.sleep(PROTOCOL_START_DELAY)
        if not _start_all_protocols(self._m2n.ixnetwork):
            raise RuntimeError("Failed to start protocols")
        time.sleep(PROTOCOL_START_DELAY)
        if not _setup_rocev2_flow_groups(self._m2n.ixnetwork):
            raise RuntimeError("Failed to setup RoCEv2 flow groups")
        if not _apply_traffic(self._m2n.ixnetwork):
            raise RuntimeError("Failed to apply traffic")
        log.info("Ixia ready")

    def disconnect(self):
        if self._m2n:
            self._m2n.disconnect()
            time.sleep(3)

    # ---- per-iteration ----

    def start(self):
        if not _start_traffic(self._m2n.ixnetwork):
            raise RuntimeError("Failed to start traffic")

    def stop(self):
        _stop_traffic(self._m2n.ixnetwork)

    def ensure_stats_ready(self):
        """Resolve stats view after traffic is flowing (called once)."""
        if self._flow_view_id is not None:
            return
        self._flow_view_id = _get_flow_stats_view_id(self._m2n)
        if self._flow_view_id is None:
            raise RuntimeError("No Flow Statistics view")
        self._rate_col_index, self._rate_col_name = \
            _find_rate_tx_column(self._m2n, self._flow_view_id)
        if self._rate_col_index is None:
            raise RuntimeError("No Rate Tx column")
        log.info(f"Monitoring: '{self._rate_col_name}' (col {self._rate_col_index})")

    # ---- monitoring ----

    def get_rates(self):
        return _get_flow_rates(self._m2n, self._flow_view_id, self._rate_col_index)

    @property
    def m2n(self):
        return self._m2n


def _stop_all_protocols(ixnetwork):
    try:
        ixnetwork.StopAllProtocols()
        log.info("Protocols stopped")
    except Exception as e:
        log.warning(f"Stop protocols: {e}")


def _start_all_protocols(ixnetwork):
    try:
        ixnetwork.StartAllProtocols()
        log.info("Protocols started")
        return True
    except Exception as e:
        log.error(f"Start protocols: {e}")
        return False


def _apply_traffic(ixnetwork):
    try:
        ixnetwork.Traffic.Apply()
        log.info("Traffic applied")
        return True
    except Exception as e:
        log.error(f"Apply traffic: {e}")
        return False


def _start_traffic(ixnetwork):
    try:
        ixnetwork.Traffic.Start()
        log.info("Traffic started")
        return True
    except Exception as e:
        log.error(f"Start traffic: {e}")
        return False


def _stop_traffic(ixnetwork):
    try:
        ixnetwork.Traffic.Stop()
        log.info("Traffic stopped")
    except Exception as e:
        log.warning(f"Stop traffic: {e}")


def _setup_rocev2_flow_groups(ixnetwork, line_rate=60):
    try:
        traffic = ixnetwork.Traffic.find()
        traffic.AddRoCEv2FlowGroups()
        port_configs = traffic.RoceV2Traffic.find().RoceV2PortConfig.find()
        for pc in port_configs:
            pc.TargetLineRateInPercent = line_rate
            pc.update()
        log.info(f"Flow groups: {len(port_configs)} ports @ {line_rate}%")
        return True
    except Exception as e:
        log.warning(f"Flow groups: {e}")
        return False


def _get_flow_stats_view_id(m2n):
    try:
        r = m2n.http.get(f"{m2n.session_url}/statistics/view", verify=False, timeout=15)
        if r.status_code != 200:
            return None
        for pattern in ["RoCEv2 Flow", "RoCEv2", "Flow Statistics", "Flow",
                        "Traffic Item", "Port Statistics"]:
            for v in r.json():
                caption = v.get("caption", "")
                if pattern in caption and "CPU" not in caption:
                    return v.get("id")
        views = r.json()
        return views[0].get("id") if views else None
    except Exception as e:
        log.warning(f"Get view id: {e}")
        return None


def _find_rate_tx_column(m2n, view_id, max_wait=30):
    terms = ["Rate Tx", "Tx Rate", "tx rate", "rate tx"]
    waited = 0
    while waited < max_wait:
        time.sleep(2)
        waited += 2
        try:
            r = m2n.http.get(
                f"{m2n.session_url}/statistics/view/{view_id}/data",
                verify=False, timeout=15
            )
            if r.status_code != 200:
                continue
            data = r.json()
            col_names = data.get("columnCaptions",
                         data.get("columnNames",
                         data.get("columns", [])))
            if data.get("columnCount", 0) > 0 and col_names:
                for i, name in enumerate(col_names):
                    if any(t in name for t in terms):
                        log.info(f"Rate column: [{i}] '{name}'")
                        return i, name
                log.warning(f"Columns (no Rate Tx): {col_names}")
                return None, None
        except Exception:
            pass
    log.warning(f"Stats not ready after {max_wait}s")
    return None, None


def _get_flow_rates(m2n, view_id, rate_col_index):
    try:
        r = m2n.http.get(
            f"{m2n.session_url}/statistics/view/{view_id}/data",
            verify=False, timeout=10
        )
        if r.status_code != 200:
            return []
        data = r.json()
        rates = []
        for key in sorted(data.get("rowValues", {}).keys()):
            rows = data["rowValues"][key]
            if not rows:
                continue
            if isinstance(rows[0], list):
                for row in rows:
                    if len(row) > rate_col_index:
                        try:
                            rates.append(float(row[rate_col_index] or 0))
                        except (ValueError, TypeError):
                            rates.append(0.0)
            else:
                if len(rows) > rate_col_index:
                    try:
                        rates.append(float(rows[rate_col_index] or 0))
                    except (ValueError, TypeError):
                        rates.append(0.0)
        return rates
    except Exception as e:
        log.debug(f"Get rates: {e}")
        return []


def run(config_file=None, ecn_params=None, run_ts=None,
        duration_minutes=100, check_interval=10, threshold_pct=10.0,
        port_capacity=400):
    if config_file is None:
        config_file = "ecn.ixncfg"

    result = {
        "success": False, "config_file": config_file, "ecn_params": ecn_params,
        "stopped_early": False, "stop_reason": None, "duration_s": 0,
        "rate_samples": [], "port_names": ["Flow0", "Flow1"],
        "port_capacity": port_capacity, "error": None, "trigger_rates": None,
    }

    m2n = None
    try:
        total_duration = duration_minutes * 60
        log.info(f"IxNetwork test | {config_file} | {duration_minutes}min | "
                 f"{port_capacity}Gbps | threshold {threshold_pct}%")

        m2n = IxiaSession()
        m2n.connect()
        m2n.load_config(config_file)

        _stop_all_protocols(m2n.ixnetwork)
        time.sleep(PROTOCOL_START_DELAY)

        if not _start_all_protocols(m2n.ixnetwork):
            result["error"] = "Failed to start protocols"
            return result
        time.sleep(PROTOCOL_START_DELAY)

        if not _setup_rocev2_flow_groups(m2n.ixnetwork):
            result["error"] = "Failed to setup RoCEv2 flow groups"
            return result

        if not _apply_traffic(m2n.ixnetwork):
            result["error"] = "Failed to apply traffic"
            return result

        if not _start_traffic(m2n.ixnetwork):
            result["error"] = "Failed to start traffic"
            return result

        t_start = time.time()
        flow_view_id = _get_flow_stats_view_id(m2n)
        if flow_view_id is None:
            result["error"] = "No Flow Statistics view"
            return result

        rate_col_index, rate_col_name = _find_rate_tx_column(m2n, flow_view_id)
        if rate_col_index is None:
            result["error"] = "No Rate Tx column"
            return result
        log.info(f"Monitoring: '{rate_col_name}' (col {rate_col_index})")

        rate_samples = []
        stopped_early = False
        stop_reason = None
        trigger_rates = None
        check_enabled = False

        while True:
            t_elapsed = time.time() - t_start

            if not check_enabled and t_elapsed >= STARTUP_DELAY:
                check_enabled = True
                log.info(f"Check enabled after {STARTUP_DELAY}s startup")

            rates = _get_flow_rates(m2n, flow_view_id, rate_col_index)
            if len(rates) >= 2:
                s1, s2 = rates[0], rates[1]
                pct1 = s1 / port_capacity * 100 if port_capacity else 0
                pct2 = s2 / port_capacity * 100 if port_capacity else 0
                diff = abs(pct1 - pct2)
                rate_samples.append([t_elapsed, s1, pct1, s2, pct2, diff])

                if check_enabled and diff > threshold_pct:
                    stopped_early = True
                    stop_reason = (f"Diff {diff:.2f}% > {threshold_pct}% "
                                   f"(flow0={s1:.2f}Gbps {pct1:.2f}%, "
                                   f"flow1={s2:.2f}Gbps {pct2:.2f}%)")
                    trigger_rates = {
                        "time_s": t_elapsed,
                        "flow0_rate": s1, "flow0_pct": pct1,
                        "flow1_rate": s2, "flow1_pct": pct2, "diff": diff,
                    }
                    log.warning(f"[!] {stop_reason}")
                    break

            if check_enabled and t_elapsed >= total_duration:
                log.info(f"Duration reached: {t_elapsed:.0f}s")
                break

            time.sleep(check_interval)
            if t_elapsed >= 60 and int(t_elapsed) % 60 < check_interval:
                log.info(f"Heartbeat {t_elapsed:.0f}s, {len(rate_samples)} samples")

        _stop_traffic(m2n.ixnetwork)
        actual_duration = time.time() - t_start

        if not rate_samples:
            result["error"] = "No rate samples collected"
            log.error("No rate samples collected")
        else:
            result["success"] = True

        result["stopped_early"] = stopped_early
        result["stop_reason"] = stop_reason
        result["duration_s"] = actual_duration
        result["rate_samples"] = rate_samples
        result["trigger_rates"] = trigger_rates

        log.info(f"Complete: {'stopped' if stopped_early else 'normal'} "
                 f"({actual_duration:.0f}s)")

        out_dir = PROJECT_DIR / "result"
        out_dir.mkdir(parents=True, exist_ok=True)
        ecn_tag = f"{ecn_params[0]}-{ecn_params[1]}-{ecn_params[2]}_" if ecn_params else ""
        ts_tag = (run_ts or datetime.now()).strftime("%Y%m%d_%H%M%S")
        csv_file = out_dir / f"rates_{ecn_tag}{ts_tag}.csv"
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["time_s", "flow0_gbps", "flow0_pct",
                             "flow1_gbps", "flow1_pct", "diff_pct"])
            for row in rate_samples:
                writer.writerow(row)
        log.info(f"CSV saved: {csv_file} ({len(rate_samples)} points)")

    except Exception as e:
        result["error"] = str(e)
        log.error(f"Test failed: {e}")
        log.error(traceback.format_exc())
    finally:
        if m2n:
            try:
                m2n.disconnect()
            except Exception:
                pass
            time.sleep(3)

    return result


if __name__ == "__main__":
    import sys
    config = sys.argv[1] if len(sys.argv) > 1 else None
    result = run(config_file=config)
    log.info(f"Success: {result['success']} | Duration: {result['duration_s']:.0f}s "
             f"| Samples: {len(result['rate_samples'])}")
    if result["error"]:
        log.error(f"Error: {result['error']}")
