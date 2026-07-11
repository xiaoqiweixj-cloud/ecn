"""ECN2to1 automated test: iterate WRED parameter combinations."""

import datetime
import json5
import sys
import time
import traceback
from pathlib import Path
from typing import Optional
from switch import switch_config
from ixia.run import IxiaRunner, STARTUP_DELAY
from analysis import data_processor, result_saver
from logger import get_logger

SCRIPT_DIR = Path(__file__).resolve().parent
TEST_CONFIG_PATH = SCRIPT_DIR / "test_config.json5"
RESULT_DIR = SCRIPT_DIR / "result"

log = get_logger()


def load_test_config() -> dict:
    if not TEST_CONFIG_PATH.exists():
        raise FileNotFoundError(f"Config not found: {TEST_CONFIG_PATH}")
    with open(TEST_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json5.load(f)


def main() -> None:
    skip_switch = "--skip-switch" in sys.argv

    config = load_test_config()
    ixia_config_file = config["ixia"]["config_file"]
    switch_host = config["switch"]["host"]
    switch_port = config["switch"].get("port", 10020)
    test_cfg = config["test"]
    duration_minutes = test_cfg.get("duration_minutes", 100)
    check_interval = test_cfg.get("check_interval_seconds", 10)
    threshold_pct = test_cfg.get("rate_diff_threshold_pct", 10.0)
    port_capacity = test_cfg.get("port_capacity_gbps", 400)
    ecn_params_list = config["ecn_params"]

    log.info(f"ECN params: {len(ecn_params_list)} | {port_capacity}Gbps | "
             f"{duration_minutes}min | interval {check_interval}s | "
             f"threshold {threshold_pct}%")
    log.info(f"Switch: {switch_host}:{switch_port} | Ixia: {ixia_config_file}")

    valid_params = [p for p in ecn_params_list
                    if len(p) >= 3 and p[0] < p[1]]
    skipped = len(ecn_params_list) - len(valid_params)
    if skipped:
        log.warning(f"Skipped {skipped} invalid combinations")

    log.info(f"Valid combinations: {len(valid_params)}")

    sw: Optional[switch_config.Switch] = None
    runner: Optional[IxiaRunner] = None
    completed = 0
    failed = 0

    try:
        # ---- Connect Ixia once ----
        if not skip_switch:
            runner = IxiaRunner(config_file=ixia_config_file)
            runner.connect()

        for idx, ecn_params in enumerate(valid_params, 1):
            min_th, max_th, mark = ecn_params[0], ecn_params[1], ecn_params[2]
            log.info(f"--- Test {idx}/{len(valid_params)}: "
                     f"min={min_th} max={max_th} mark={mark} ---")

            run_ts = datetime.datetime.now()

            try:
                # Step 1: Stop Ixia traffic
                if runner:
                    runner.stop()

                # Step 2: Configure switch
                if not skip_switch:
                    sw = switch_config.Switch(switch_host, port=switch_port)
                    sw.connect()
                    sw.ecn(str(min_th), str(max_th), str(mark))
                    sw.close()
                    log.info("Switch ECN configured")

                # Step 3: Start Ixia traffic
                if runner:
                    runner.start()

                # Step 4: Monitor rates
                total_duration = duration_minutes * 60
                rate_samples = []
                stopped_early = False
                stop_reason = None
                trigger_rates = None
                check_enabled = False
                t_start = time.time()

                while True:
                    t_elapsed = time.time() - t_start

                    if not check_enabled and t_elapsed >= STARTUP_DELAY:
                        check_enabled = True
                        log.info(f"Check enabled after {STARTUP_DELAY}s startup")

                    if runner:
                        runner.ensure_stats_ready()
                        rates = runner.get_rates()
                    else:
                        rates = []

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

                actual_duration = time.time() - t_start

                # Step 5: Process & save
                ixia_result = {
                    "success": len(rate_samples) > 0,
                    "stopped_early": stopped_early,
                    "stop_reason": stop_reason,
                    "duration_s": actual_duration,
                    "rate_samples": rate_samples,
                    "trigger_rates": trigger_rates,
                    "port_capacity": port_capacity,
                }

                if not ixia_result["success"]:
                    log.error("No rate samples collected")
                    failed += 1
                    continue

                stats = data_processor.run(ixia_result)
                result_saver.save(stats, ecn_params=ecn_params, run_ts=run_ts)

                completed += 1
                status = "EARLY_STOP" if stopped_early else "PASS"
                time.sleep(5)
                log.info(f"Test {idx} {status} (diff={stats.get('max_diff', 0):.2f}%)")

            except Exception as e:
                log.error(f"Test {idx} exception: {e}")
                log.error(traceback.format_exc())
                failed += 1
                if sw:
                    try:
                        sw.close()
                    except Exception:
                        pass
                time.sleep(5)

        log.info(f"All done: {completed} completed, {failed} failed, "
                 f"{skipped} skipped")

    finally:
        if runner:
            try:
                runner.stop()
            except Exception:
                pass
            try:
                runner.disconnect()
            except Exception:
                pass
        if sw:
            try:
                sw.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
