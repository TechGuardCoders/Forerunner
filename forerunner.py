"""
Forerunner I — production drift monitor for inference quality and latency.

Golden datasets go stale. Production is the eval suite that never stops
updating. Forerunner I is the sampling daemon: it periodically compares a
current observation window against a baseline and alerts when a tracked
metric drifts beyond tolerance — before users complain.

Extends Spinal's drift concept (which compared two trace files) into a
continuous monitor with:
- multiple tracked metrics (latency phases, error rate, KV utilization)
- per-metric tolerances and consecutive-breach hysteresis (no flapping)
- alert callbacks (webhook/log/anything callable)
- stateful windows: baseline is a rolling reference, not a static file

Public API:
    DriftMonitor(baseline, tolerances=None, on_alert=None)
    .observe(window)     -> dict  (feed one observation window; alerts fire)
    .snapshot()          -> dict  (current state, serializable)

A "window" is the same shape as a Spinal trace doc ({"traces": [...]}) or a
flat metric dict. Baseline is a metric dict (metric -> expected value).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

__version__ = "0.1.0"

# metric -> (direction, tolerance_fraction, consecutive_breaches_to_alert)
# direction: "higher" = higher is better (drift = falling); "lower" = lower better
DEFAULT_METRICS: dict[str, tuple[str, float, int]] = {
    "ttft_p50_ms": ("lower", 0.20, 3),
    "ttft_p95_ms": ("lower", 0.25, 3),
    "total_p50_ms": ("lower", 0.15, 3),
    "error_rate": ("lower", 0.0, 2),
    "kv_peak": ("lower", 0.30, 5),
}


# ---------------------------------------------------------------- window extraction

def _percentile(values: list[float], frac: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(frac * len(s)))]


def window_metrics(window: dict) -> dict[str, float]:
    """Flatten an observation window into metrics.

    Accepts a Spinal-style doc ({"traces": [...]}) or a flat metric dict.
    """
    if "traces" in window:
        ok = [t for t in window["traces"] if not t.get("failed")]
        ttfts = [float(t["ttftMs"]) for t in ok if "ttftMs" in t]
        totals = [float(t["totalMs"]) for t in ok if "totalMs" in t]
        n = len(window["traces"])
        m = {
            "ttft_p50_ms": _percentile(ttfts, 0.5),
            "ttft_p95_ms": _percentile(ttfts, 0.95),
            "total_p50_ms": _percentile(totals, 0.5),
            "error_rate": (n - len(ok)) / n if n else 0.0,
        }
        kv = [float(s["kvCache"]) for t in window["traces"]
              for s in t.get("samples", []) if "kvCache" in s]
        if kv:
            m["kv_peak"] = max(kv)
        return m
    return {k: float(v) for k, v in window.items()}


# ---------------------------------------------------------------- monitor

@dataclass
class DriftMonitor:
    """Stateful drift monitor. Feed windows via .observe()."""
    baseline: dict[str, float]
    tolerances: dict[str, float] | None = None
    on_alert: Callable[[dict], None] | None = None
    _breach_streaks: dict[str, int] = field(default_factory=dict)
    _alerts_fired: dict[str, bool] = field(default_factory=dict)
    _windows_seen: int = 0

    def observe(self, window: dict) -> dict:
        """Process one observation window. Returns status dict; fires alerts."""
        self._windows_seen += 1
        metrics = window_metrics(window)
        status: dict = {"window": self._windows_seen, "metrics": {},
                        "alerts": []}

        for metric, value in metrics.items():
            if metric not in self.baseline:
                continue
            direction, tol, breaches_needed = self._spec(metric)
            base = self.baseline[metric]

            if direction == "higher":
                drifted = value < base * (1 - tol)
            else:
                drifted = value > base * (1 + tol)

            streak = self._breach_streaks.get(metric, 0)
            streak = streak + 1 if drifted else 0
            self._breach_streaks[metric] = streak

            entry = {"value": round(value, 4), "baseline": round(base, 4),
                     "drifted": drifted, "breach_streak": streak}
            status["metrics"][metric] = entry

            if drifted and streak >= breaches_needed and not self._alerts_fired.get(metric):
                alert = {"metric": metric, "value": value, "baseline": base,
                         "streak": streak, "tolerance": tol,
                         "message": f"{metric} drifted: {base} -> {value} "
                                    f"({streak} consecutive windows)"}
                status["alerts"].append(alert)
                self._alerts_fired[metric] = True
                if self.on_alert:
                    try:
                        self.on_alert(alert)
                    except Exception:  # noqa: BLE001 — alert must not kill the loop
                        pass

        return status

    def _spec(self, metric: str) -> tuple[str, float, int]:
        if metric in DEFAULT_METRICS:
            return DEFAULT_METRICS[metric]
        for suffix, spec in DEFAULT_METRICS.items():
            if metric.endswith("_" + suffix):
                return spec
        return ("lower", 0.20, 3)

    def snapshot(self) -> dict:
        return {"windows_seen": self._windows_seen,
                "breach_streaks": dict(self._breach_streaks),
                "alerts_fired": dict(self._alerts_fired),
                "baseline": self.baseline}


def monitor_from_trace(baseline_trace_path: str, **kwargs) -> DriftMonitor:
    """Build a DriftMonitor whose baseline comes from a Spinal trace file."""
    doc = json.loads(Path(baseline_trace_path).read_text(encoding="utf-8"))
    return DriftMonitor(baseline=window_metrics(doc), **kwargs)
