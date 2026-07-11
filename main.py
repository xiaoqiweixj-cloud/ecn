"""ECN2to1 automated test: iterate WRED parameter combinations."""

import datetime
import json5
import sys
import time
import traceback
from pathlib import Path
from typing import Optional
from switch import switch_config
from ixia import run
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
    completed = 0
    failed = 0

    try:
        if not skip_switch:
            sw = switch_config.Switch(switch_host, port=switch_port)

        for idx, ecn_params in enumerate(valid_params, 1):
            min_th, max_th, mark = ecn_params[0], ecn_params[1], ecn_params[2]
            log.info(f"--- Test {idx}/{len(valid_params)}: "
                     f"min={min_th} max={max_th} mark={mark} ---")

            run_ts = datetime.datetime.now()

            try:
                # Step 1: Configure switch
                if not skip_switch:
                    assert sw is not None
                    sw.connect()
                    sw.ecn(str(min_th), str(max_th), str(mark))
                    sw.close()
                    log.info("Switch ECN configured")

                # Step 2: Run Ixia test
                ixia_result = run.run(
                    config_file=ixia_config_file,
                    ecn_params=ecn_params,
                    run_ts=run_ts,
                    duration_minutes=duration_minutes,
                    check_interval=check_interval,
                    port_capacity=port_capacity,
                    threshold_pct=threshold_pct,
                )

                if not ixia_result.get("success"):
                    log.error(f"Ixia failed: {ixia_result.get('error', 'Unknown')}")
                    failed += 1
                    continue

                # Step 3: Process data
                stats = data_processor.run(ixia_result)

                # Step 4: Save results
                result_saver.save(stats, ecn_params=ecn_params, run_ts=run_ts)

                completed += 1
                status = "EARLY_STOP" if stats.get("stopped_early") else "PASS"
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
        if sw:
            sw.close()


if __name__ == "__main__":
    main()
