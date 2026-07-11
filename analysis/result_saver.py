"""Save test results to result/result.txt and result/result_pass.txt."""

import datetime
from pathlib import Path
from typing import Optional, Tuple
from logger import get_logger

log = get_logger()

PROJECT_DIR = Path(__file__).resolve().parent.parent
RESULT_DIR = PROJECT_DIR / "result"
OUTPUT_FILE = RESULT_DIR / "result.txt"
PASS_FILE = RESULT_DIR / "result_pass.txt"


def fmt(v: Optional[float], d: int = 2) -> str:
    return "N/A" if v is None else f"{v:.{d}f}"


def save(stats: dict, ecn_params: Optional[list] = None,
         run_ts: Optional[datetime.datetime] = None) -> Tuple[str, str, bool]:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    now_str = (run_ts or datetime.datetime.now()).strftime("%Y-%m-%d %H:%M:%S")

    if OUTPUT_FILE.exists():
        with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
            f.write("\n")

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
        "",
    ]

    text = "\n".join(lines) + "\n"

    with open(OUTPUT_FILE, "a", encoding="utf-8") as f:
        f.write(text)
    log.info(text.rstrip())

    if not stopped_early:
        with open(PASS_FILE, "a", encoding="utf-8") as f:
            f.write(text)

    return str(OUTPUT_FILE), str(PASS_FILE), not stopped_early
