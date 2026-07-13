"""Save test results to run directory: summary.txt, pass.txt, and per-iteration CSV."""

import csv
import datetime
from pathlib import Path
from typing import Optional, Tuple
from logger import get_logger

log = get_logger()


def fmt(v: Optional[float], d: int = 2) -> str:
    return "N/A" if v is None else f"{v:.{d}f}"


def save(stats: dict, ecn_params: Optional[list] = None,
         run_ts: Optional[datetime.datetime] = None,
         output_dir: Optional[Path] = None,
         rate_samples: Optional[list] = None) -> Tuple[str, str, bool]:
    """Save summary text and per-iteration CSV to output_dir.

    If output_dir is None, uses result/<timestamp>/.
    """
    if output_dir is None:
        ts = (run_ts or datetime.datetime.now()).strftime("%Y%m%d_%H%M%S")
        output_dir = Path(__file__).resolve().parent.parent / "result" / f"run_{ts}"
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_file = output_dir / "summary.txt"
    pass_file = output_dir / "pass.txt"
    now_str = (run_ts or datetime.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")

    ecn_str = (f"min={ecn_params[0]}, max={ecn_params[1]}, mark={ecn_params[2]}"
               if ecn_params else "N/A")

    stopped_early = stats.get("stopped_early", False)
    status = "EARLY_STOP" if stopped_early else "PASS"

    lines = [
        "=" * 70,
        f"  Test Result - {status}",
        "=" * 70,
        f"  Time:           {now_str}",
        f"  ECN Params:     {ecn_str}",
        f"  Duration:       {stats.get('duration_s', 0):.0f}s",
        f"  Port Capacity:  {stats.get('port_capacity', 400)} Gbps",
        f"  Flow0:          {fmt(stats.get('trigger_flow0_rate', 0))} Gbps "
        f"({fmt(stats.get('trigger_flow0_pct', 0))}%)",
        f"  Flow1:          {fmt(stats.get('trigger_flow1_rate', 0))} Gbps "
        f"({fmt(stats.get('trigger_flow1_pct', 0))}%)",
        f"  Diff:           {fmt(stats.get('max_diff'))}%",
        f"  Samples:        {stats.get('sample_count', 0)}",
        "",
    ]

    text = "\n".join(lines) + "\n"

    with open(summary_file, "a", encoding="utf-8") as f:
        f.write(text)

    if not stopped_early:
        with open(pass_file, "a", encoding="utf-8") as f:
            f.write(text)

    # Save per-iteration CSV
    if rate_samples:
        ecn_tag = (f"{ecn_params[0]}-{ecn_params[1]}-{ecn_params[2]}_"
                   if ecn_params else "")
        ts_tag = (run_ts or datetime.datetime.now()).strftime("%Y%m%d_%H%M%S")
        csv_file = output_dir / f"rates_{ecn_tag}{ts_tag}.csv"
        with open(csv_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["time_s", "flow0_gbps", "flow0_pct",
                             "flow1_gbps", "flow1_pct", "diff_pct"])
            for row in rate_samples:
                writer.writerow(row)
        log.info(f"CSV saved: {csv_file} ({len(rate_samples)} points)")

    log.info(f"Summary: {summary_file}")
    return str(summary_file), str(pass_file), not stopped_early
