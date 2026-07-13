# ECN2to1

Automated RoCEv2 ECN (WRED) rate-balance test — iterates WRED parameter combinations on a switch, drives IxNetwork traffic, monitors flow rates, and reports pass/fail per segment.

## Architecture

```
main.py ──► switch/switch_config.py    Telnet-based WRED config on 400G ports
        ──► ixia/connect.py            IxNetwork REST API session
        ──► ixia/run.py                Traffic start/stop + RoCEv2 flow stats
        ──► analysis/data_processor.py Compute summary statistics
        ──► analysis/result_saver.py   Write summary.txt + per-segment CSVs
```

## Requirements

- Python ≥ 3.10
- [ixnetwork-restpy](https://pypi.org/project/ixnetwork-restpy/) — IxNetwork REST API wrapper
- [telnetlib3](https://pypi.org/project/telnetlib3/) — async Telnet client
- [json5](https://pypi.org/project/json5/) — config parsing with comments

```bash
pip install ixnetwork-restpy telnetlib3 json5
```

## Configuration

### `test_config.json5` — Test parameters

```json5
{
  "ixia": { "config_file": "ecn.ixncfg" },
  "switch": { "host": "10.140.0.142", "port": 10020 },
  "test": {
    "duration_minutes": 100,
    "segment_duration_minutes": 10,
    "check_interval_seconds": 10,
    "rate_diff_threshold_pct": 10.0,
    "port_capacity_gbps": 400
  },
  "ecn_params": [
    [100, 200, 80],
    [100, 300, 80]
  ]
}
```

| Field | Description |
|-------|-------------|
| `duration_minutes` | Total test duration per ECN param |
| `segment_duration_minutes` | Per-segment duration (stop/restart traffic between segments) |
| `check_interval_seconds` | Sampling interval for rate monitoring |
| `rate_diff_threshold_pct` | Diff threshold for FAIL marking |
| `port_capacity_gbps` | Reference line rate for percentage calculation |
| `ecn_params` | `[min_threshold, max_threshold, mark_probability]` tuples |

### `ixia_config.json` — Ixia server credentials

```json
{
  "api_server_ip": "10.140.0.204",
  "rest_port": 443,
  "username": "admin",
  "password": "",
  "session_name": "ixia_session",
  "clear_config": true,
  "delete_on_exit": false
}
```

## Usage

```bash
# Full run (Ixia + Switch)
python main.py

# Ixia-only (skip switch config)
python main.py --skip-switch

# Utility scripts
python scripts/check_ixia.py       # Test Ixia API responsiveness
python scripts/check_sessions.py   # List active Ixia sessions
python scripts/debug_stats.py      # Inspect raw statistics views
```

## How it works

For each ECN parameter combination:

1. **Stop** Ixia traffic (clean state)
2. **Configure** switch WRED via Telnet
3. **Segment loop** — repeat `duration / segment_duration` times:
   - Start traffic
   - First segment: wait for stats view to resolve
   - Monitor flow rates for `segment_duration_minutes`
   - 5s warmup before threshold judgment
   - Threshold exceeded → log warning, continue running
   - Stop traffic, save per-segment CSV
4. **Analyze** — compute avg rates, per-segment worst diff
5. **Save** — summary.txt + `segment_N.csv` per param

## Output structure

```
result/
  run_20260713_091508/
    min=100_max=200_mark=80/
      summary.txt
      segment_1.csv
      segment_2.csv
      ...
    min=100_max=300_mark=80/
      summary.txt
      segment_1.csv
      ...
```

### summary.txt example

```
======================================================================
  Test Result - PASS:1 FAIL:9
======================================================================
  Time:           2026-07-13 09:15:08
  ECN Params:     min=100, max=200, mark=80
  Duration:       1000s
  Port Capacity:  400 Gbps
  Seg  1:   0.42% ( 198.16 Gbps/ 49.54%,  199.84 Gbps/ 49.96%) -PASS
  Seg  2:  16.57% ( 165.85 Gbps/ 41.46%,  232.11 Gbps/ 58.03%) -FAIL
  ...
```

### segment_N.csv columns

| Column | Description |
|--------|-------------|
| `time_s` | Seconds from segment start |
| `flow0_gbps` | Flow 0 rate (Gbps) |
| `flow0_pct` | Flow 0 rate (% of port capacity) |
| `flow1_gbps` | Flow 1 rate (Gbps) |
| `flow1_pct` | Flow 1 rate (%) |
| `diff_pct` | Absolute diff between flow percentages |

## Design notes

- **Ixia connects once** at startup, reused across all ECN iterations and segments
- **Stats view resolved once** per run (first segment), cached for subsequent segments
- **5s warmup** — first 5s of each segment excluded from threshold judgment and diff statistics (ramp-up noise)
- **No early exit** — threshold exceed logs a warning but the test continues to completion
- **Switch ECN config** uses Telnet; `--skip-switch` flag bypasses it for Ixia-only debugging
