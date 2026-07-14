"""ECN2to1 automated test: iterate WRED parameter combinations."""

import csv
import datetime
import json5
import re
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


# ---------------------------------------------------------------------------
# Helper: parse param directory name
# ---------------------------------------------------------------------------

_PARAM_DIRNAME_RE = re.compile(r"^min=(.+)_max=(.+)_mark=(.+)$")


def parse_param_dirname(dirname: str) -> Optional[list]:
    """Parse 'min=500_max=1000_mark=80' -> ['500', '1000', '80'] or None."""
    m = _PARAM_DIRNAME_RE.match(dirname)
    if m:
        return [m.group(1), m.group(2), m.group(3)]
    return None


def discover_param_dirs(input_dir: Path) -> list:
    """Scan input_dir for param subdirectories.

    Returns:
        List of (dir_path, [min, max, mark]) sorted by directory name.
    """
    result = []
    for p in sorted(input_dir.iterdir()):
        if p.is_dir():
            params = parse_param_dirname(p.name)
            if params:
                result.append((p, params))
    return result


def read_csv_samples(param_dir: Path, segment_duration_s: float) -> tuple:
    """Read segment_*.csv files from a param directory.

    Returns:
        (all_rate_samples, seg_samples_list) matching the format produced
        by the live monitoring loop.
    """
    csv_files = sorted(param_dir.glob("segment_*.csv"),
                        key=lambda p: int(p.stem.split("_")[1]))
    all_rate_samples = []
    seg_samples_list = []

    for seg_idx, csv_file in enumerate(csv_files):
        seg_samples = []
        with open(csv_file, "r", encoding="utf-8") as f:
            reader_obj = csv.reader(f)
            next(reader_obj, None)  # skip header
            for row in reader_obj:
                if len(row) >= 6:
                    seg_samples.append([
                        float(row[0]),  # time_s
                        float(row[1]),  # flow0_gbps
                        float(row[2]),  # flow0_pct
                        float(row[3]),  # flow1_gbps
                        float(row[4]),  # flow1_pct
                        float(row[5]),  # diff_pct
                    ])
        # Deep copy for per-segment list (before offset)
        seg_samples_list.append([list(r) for r in seg_samples])
        # Offset timestamps for accumulated list
        seg_offset = seg_idx * segment_duration_s
        for row in seg_samples:
            row[0] = row[0] + seg_offset
        all_rate_samples.extend(seg_samples)

    return all_rate_samples, seg_samples_list


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    skip_switch = "--skip-switch" in sys.argv
    skip_ixia = "--skip-ixia" in sys.argv
    skip_save = "--skip-save" in sys.argv

    # Parse --input-dir <path>
    input_dir: Optional[Path] = None
    for i, arg in enumerate(sys.argv):
        if arg == "--input-dir" and i + 1 < len(sys.argv):
            input_dir = Path(sys.argv[i + 1])
            if not input_dir.exists():
                log.error(f"Input dir not found: {input_dir}")
                sys.exit(1)
            if not input_dir.is_dir():
                log.error(f"Input path is not a directory: {input_dir}")
                sys.exit(1)

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

    segment_duration_s = segment_duration_minutes * 60

    log.info(f"ECN params: {len(ecn_params_list)} | {port_capacity}Gbps | "
             f"{duration_minutes}min ({segment_duration_minutes}min/seg) | "
             f"interval {check_interval}s | threshold {threshold_pct}%")
    log.info(f"Switch: {switch_host}:{switch_port} | Ixia: {ixia_config_file}")

    if skip_switch:
        log.info("Flags: --skip-switch (no switch config)")
    if skip_ixia:
        log.info("Flags: --skip-ixia (no Ixia traffic)")
    if skip_save:
        log.info("Flags: --skip-save (no result save)")
    if input_dir:
        log.info(f"Input dir: {input_dir}")

    # ---- Determine iteration source ----
    if input_dir:
        param_entries = discover_param_dirs(input_dir)
        if not param_entries:
            log.warning(f"No param subdirectories found in {input_dir}")
        iterable = param_entries
    else:
        valid_params = [p for p in ecn_params_list
                        if len(p) >= 3 and float(p[0]) < float(p[1])]
        skipped = len(ecn_params_list) - len(valid_params)
        if skipped:
            log.warning(f"Skipped {skipped} invalid combinations")
        log.info(f"Valid combinations: {len(valid_params)}")
        iterable = [(None, p) for p in valid_params]

    if not iterable:
        log.warning("Nothing to iterate — exiting")
        return

    need_ixia = not skip_ixia and not input_dir

    sw: Optional[switch_config.Switch] = None
    runner: Optional[IxiaRunner] = None
    completed = 0
    failed = 0
    skipped_count = 0 if input_dir else len(ecn_params_list) - len(iterable)

    # Timestamped output directory for this run
    run_dir = RESULT_DIR / f"run_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    log.info(f"Output: {run_dir}")

    # Collect per-param results for run summary
    run_results = []

    try:
        # ---- Connect Ixia once ----
        if need_ixia:
            runner = IxiaRunner(config_file=ixia_config_file)
            runner.connect()

        for idx, (param_dir, ecn_params) in enumerate(iterable, 1):
            min_th, max_th, mark = ecn_params[0], ecn_params[1], ecn_params[2]
            log.info(f"--- Test {idx}/{len(iterable)}: "
                     f"min={min_th} max={max_th} mark={mark} ---")

            run_ts = datetime.datetime.now()

            try:
                # Step 1: Stop Ixia traffic
                if runner:
                    runner.stop()

                # Step 2: Configure switch
                if not skip_switch and not input_dir:
                    sw = switch_config.Switch(switch_host, port=switch_port)
                    sw.connect()
                    sw.ecn(str(min_th), str(max_th), str(mark))
                    sw.close()
                    log.info("Switch ECN configured")

                # Step 3: Monitoring
                if input_dir and param_dir:
                    # Reprocess existing CSV data
                    all_rate_samples, seg_samples_list = read_csv_samples(
                        param_dir, segment_duration_s)
                    num_segments = len(seg_samples_list)
                    actual_duration = num_segments * segment_duration_s

                elif not skip_ixia:
                    # Live monitoring loop
                    total_duration = duration_minutes * 60
                    num_segments = max(1, total_duration // segment_duration_s)
                    all_rate_samples = []
                    segment_diffs = []
                    seg_samples_list = []

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

                            if not check_enabled and t_elapsed >= 30:
                                check_enabled = True
                                log.info(f"Segment {seg_label}: check enabled (30s warmup)")

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

                            if t_elapsed >= segment_duration_s:
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

                        # Compute this segment's worst diff (skip 30s warmup)
                        stable_samples = [r for r in seg_samples if r[0] >= 30]
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
                        seg_offset = seg * segment_duration_s
                        for row in seg_samples:
                            row[0] = row[0] + seg_offset
                        all_rate_samples.extend(seg_samples)

                        log.info(f"Segment {seg_label}: done, {len(seg_samples)} samples")

                    actual_duration = num_segments * segment_duration_s

                else:
                    # --skip-ixia without --input-dir: no monitoring
                    all_rate_samples = []
                    seg_samples_list = []
                    segment_diffs = []
                    num_segments = 0
                    actual_duration = 0

                # ---- Recompute segment_diffs if from input_dir ----
                if input_dir and param_dir:
                    segment_diffs = []
                    for seg_idx, seg_samples in enumerate(seg_samples_list, 1):
                        stable_samples = [r for r in seg_samples if r[0] >= 30]
                        if stable_samples:
                            worst = max(stable_samples, key=lambda r: r[5])
                            seg_diff = worst[5]
                            segment_diffs.append({
                                "seg": seg_idx,
                                "max_diff": seg_diff,
                                "flow0_rate": worst[1],
                                "flow0_pct": worst[2],
                                "flow1_rate": worst[3],
                                "flow1_pct": worst[4],
                                "over_threshold": seg_diff > threshold_pct,
                            })
                    actual_duration = len(seg_samples_list) * segment_duration_s

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

                if not skip_save and not ixia_result["success"]:
                    log.error("No rate samples collected")
                    failed += 1
                    run_results.append({
                        "min_th": min_th, "max_th": max_th, "mark": mark,
                        "status": "NO_DATA", "max_diff_pct": None,
                    })
                    continue

                if not skip_save:
                    param_out_dir = (run_dir
                                     / f"min={min_th}_max={max_th}_mark={mark}")
                    stats = data_processor.run(ixia_result)
                    result_saver.save(stats, ecn_params=ecn_params, run_ts=run_ts,
                                      output_dir=param_out_dir,
                                      seg_samples_list=seg_samples_list)
                    max_diff = stats.get('max_diff', 0)
                else:
                    max_diff = None

                completed += 1
                time.sleep(5)

                # Record for run summary
                seg_pass = sum(1 for d in segment_diffs if not d["over_threshold"])
                seg_fail = len(segment_diffs) - seg_pass
                run_results.append({
                    "min_th": min_th, "max_th": max_th, "mark": mark,
                    "seg_pass": seg_pass, "seg_fail": seg_fail,
                    "max_diff_pct": max_diff,
                })

                log.info(f"Test {idx} PASS (diff={max_diff if max_diff else 'N/A'})")

            except Exception as e:
                log.error(f"Test {idx} exception: {e}")
                log.error(traceback.format_exc())
                failed += 1
                run_results.append({
                    "min_th": min_th, "max_th": max_th, "mark": mark,
                    "status": f"ERROR: {e}",
                    "max_diff_pct": None,
                })
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
                 f"{skipped_count} skipped")

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

    # ---- Write run summary ----
    if run_results:
        run_dir.mkdir(parents=True, exist_ok=True)
        summary_path = run_dir / "run_summary.csv"
        with open(summary_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["min_th", "max_th", "mark",
                             "status", "max_diff_pct"])
            for r in run_results:
                status = r.get("status")
                if status is None:
                    status = f"PASS:{r['seg_pass']} FAIL:{r['seg_fail']}"
                diff_str = (f"{r['max_diff_pct']:.2f}"
                            if r.get('max_diff_pct') is not None else "N/A")
                writer.writerow([
                    r["min_th"], r["max_th"], r["mark"],
                    status, diff_str,
                ])
        log.info(f"Run summary: {summary_path}")


if __name__ == "__main__":
    main()
