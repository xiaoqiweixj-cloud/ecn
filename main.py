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
    segment_duration_minutes = test_cfg.get("segment_duration_minutes", 10)
    check_interval = test_cfg.get("check_interval_seconds", 10)
    threshold_pct = test_cfg.get("rate_diff_threshold_pct", 10.0)
    port_capacity = test_cfg.get("port_capacity_gbps", 400)
    ecn_params_list = config["ecn_params"]

    log.info(f"ECN params: {len(ecn_params_list)} | {port_capacity}Gbps | "
             f"{duration_minutes}min ({segment_duration_minutes}min/seg) | "
             f"interval {check_interval}s | threshold {threshold_pct}%")
    log.info(f"Switch: {switch_host}:{switch_port} | Ixia: {ixia_config_file}")

    valid_params = [p for p in ecn_params_list
                    if len(p) >= 3 and float(p[0]) < float(p[1])]
    skipped = len(ecn_params_list) - len(valid_params)
    if skipped:
        log.warning(f"Skipped {skipped} invalid combinations")

    log.info(f"Valid combinations: {len(valid_params)}")

    sw: Optional[switch_config.Switch] = None
    runner: Optional[IxiaRunner] = None
    completed = 0
    failed = 0

    # Timestamped output directory for this run
    run_dir = RESULT_DIR / f"run_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    log.info(f"Output: {run_dir}")

    try:
        # ---- Connect Ixia once ----
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

                # Step 3: Segment-based monitoring
                segment_duration = segment_duration_minutes * 60
                total_duration = duration_minutes * 60
                num_segments = max(1, total_duration // segment_duration)
                all_rate_samples = []
                segment_diffs = []
                seg_samples_list = []

                param_dir = run_dir / f"min={min_th}_max={max_th}_mark={mark}"

                for seg in range(num_segments):
                    seg_label = f"{seg + 1}/{num_segments}"
                    log.info(f"--- Segment {seg_label}: starting traffic ---")

                    # Start traffic
                    if runner:
                        runner.start()
                        if seg == 0:
                            time.sleep(STARTUP_DELAY)
                            runner.ensure_stats_ready()

                    # Monitor for this segment (no early exit on threshold)
                    check_enabled = False
                    t_seg_start = time.time()
                    seg_samples = []

                    while True:
                        t_elapsed = time.time() - t_seg_start

                        if not check_enabled and t_elapsed >= 5:
                            check_enabled = True
                            log.info(f"Segment {seg_label}: check enabled (5s warmup)")

                        if runner:
                            rates = runner.get_rates()
                        else:
                            rates = []

                        t_sample = time.time() - t_seg_start

                        if len(rates) >= 2:
                            s1, s2 = rates[0], rates[1]
                            pct1 = s1 / port_capacity * 100 if port_capacity else 0
                            pct2 = s2 / port_capacity * 100 if port_capacity else 0
                            diff = abs(pct1 - pct2)
                            seg_samples.append(
                                [t_sample, s1, pct1, s2, pct2, diff])

                            if check_enabled and diff > threshold_pct:
                                log.warning(
                                    f"Segment {seg_label}: diff {diff:.2f}% > "
                                    f"{threshold_pct}% "
                                    f"(flow0={s1:.2f}Gbps, flow1={s2:.2f}Gbps)")

                        if t_elapsed >= segment_duration:
                            log.info(
                                f"Segment {seg_label}: duration reached "
                                f"({t_elapsed:.0f}s, {len(seg_samples)} samples)")
                            break

                        time.sleep(check_interval)
                        if t_elapsed >= 60 and int(t_elapsed) % 60 < check_interval:
                            log.info(
                                f"Segment {seg_label} heartbeat {t_elapsed:.0f}s, "
                                f"{len(seg_samples)} samples")

                    # Stop traffic after segment
                    if runner:
                        runner.stop()

                    # Save per-segment samples (before offset)
                    seg_samples_list.append([list(row) for row in seg_samples])

                    # Compute this segment's worst diff (skip 5s warmup)
                    stable_samples = [r for r in seg_samples if r[0] >= 5]
                    if stable_samples:
                        worst = max(stable_samples, key=lambda r: r[5])
                        seg_diff = worst[5]
                        segment_diffs.append({
                            "seg": seg + 1,
                            "max_diff": seg_diff,
                            "flow0_rate": worst[1],
                            "flow0_pct": worst[2],
                            "flow1_rate": worst[3],
                            "flow1_pct": worst[4],
                            "over_threshold": seg_diff > threshold_pct,
                        })

                    # Accumulate: offset timestamps by segment boundary
                    seg_offset = seg * segment_duration
                    for row in seg_samples:
                        row[0] = row[0] + seg_offset
                    all_rate_samples.extend(seg_samples)

                    log.info(f"Segment {seg_label}: done, {len(seg_samples)} samples")

                actual_duration = num_segments * segment_duration

                # Step 4: Process & save
                ixia_result = {
                    "success": len(all_rate_samples) > 0,
                    "stopped_early": False,
                    "stop_reason": None,
                    "duration_s": actual_duration,
                    "rate_samples": all_rate_samples,
                    "trigger_rates": None,
                    "port_capacity": port_capacity,
                    "segment_diffs": segment_diffs,
                }

                if not ixia_result["success"]:
                    log.error("No rate samples collected")
                    failed += 1
                    continue

                stats = data_processor.run(ixia_result)
                result_saver.save(stats, ecn_params=ecn_params, run_ts=run_ts,
                                  output_dir=param_dir,
                                  seg_samples_list=seg_samples_list)

                completed += 1
                time.sleep(5)
                log.info(f"Test {idx} PASS (diff={stats.get('max_diff', 0):.2f}%)")

            except Exception as e:
                log.error(f"Test {idx} exception: {e}")
                log.error(traceback.format_exc())
                failed += 1
                if runner:
                    try:
                        runner.stop()
                    except Exception:
                        pass
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
