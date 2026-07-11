#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compute summary stats from Ixia rate samples."""


def run(ixia_result: dict) -> dict:
    """Compute summary statistics from Ixia rate samples.

    Args:
        ixia_result: Dictionary containing rate_samples, port_names, etc.

    Returns:
        Dictionary with computed statistics.
    """
    rate_samples = ixia_result.get("rate_samples", [])
    port_names = ixia_result.get("port_names", ["Flow0", "Flow1"])
    stopped_early = ixia_result.get("stopped_early", False)
    trigger_rates = ixia_result.get("trigger_rates")
    port_capacity = ixia_result.get("port_capacity", 400)

    # Compute average rates from all samples
    if rate_samples:
        n = len(rate_samples)
        sum1 = sum(row[1] for row in rate_samples if len(row) > 1)
        sum2 = sum(row[3] for row in rate_samples if len(row) > 3)
        avg1 = sum1 / n if n > 0 else 0
        avg2 = sum2 / n if n > 0 else 0
    else:
        avg1 = avg2 = 0

    stats = {
        "stopped_early": stopped_early,
        "stop_reason": ixia_result.get("stop_reason"),
        "duration_s": ixia_result.get("duration_s", 0),
        "port_capacity": port_capacity,
        "port_names": port_names,
        "avg_rates": [avg1, avg2],
        "avg_rates_pct": [
            avg1 / port_capacity * 100 if port_capacity else 0,
            avg2 / port_capacity * 100 if port_capacity else 0,
        ],
        "sample_count": len(rate_samples),
        "max_diff": None,
        "is_pass": not stopped_early,
    }

    if trigger_rates:
        stats["trigger_flow0_rate"] = trigger_rates["flow0_rate"]
        stats["trigger_flow0_pct"] = trigger_rates["flow0_pct"]
        stats["trigger_flow1_rate"] = trigger_rates["flow1_rate"]
        stats["trigger_flow1_pct"] = trigger_rates["flow1_pct"]
        stats["max_diff"] = trigger_rates["diff"]
        stats["trigger_time_s"] = trigger_rates["time_s"]
    elif stopped_early:
        # No trigger_rates but stopped_early (shouldn't happen, but fallback)
        if rate_samples and len(rate_samples[-1]) >= 6:
            last = rate_samples[-1]
            stats["trigger_flow0_rate"] = last[1]
            stats["trigger_flow0_pct"] = last[2]
            stats["trigger_flow1_rate"] = last[3]
            stats["trigger_flow1_pct"] = last[4]
            stats["max_diff"] = last[5]
            stats["trigger_time_s"] = last[0]
    else:
        # PASS: use last sample
        if rate_samples and len(rate_samples[-1]) >= 6:
            last = rate_samples[-1]
            stats["trigger_flow0_rate"] = last[1]
            stats["trigger_flow0_pct"] = last[2]
            stats["trigger_flow1_rate"] = last[3]
            stats["trigger_flow1_pct"] = last[4]
            stats["max_diff"] = last[5]
            stats["trigger_time_s"] = last[0]

    return stats
