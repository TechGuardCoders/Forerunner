"""Forerunner I tests — alerts fire on sustained drift, never on noise."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from forerunner import DriftMonitor, monitor_from_trace, window_metrics


def spinal_window(ttfts, totals, n_failed=0, kv=None):
    traces = [{"ttftMs": t, "totalMs": s, "failed": False} for t, s in zip(ttfts, totals)]
    traces += [{"failed": True} for _ in range(n_failed)]
    if kv:
        traces[0]["samples"] = [{"kvCache": v} for v in kv]
    return {"traces": traces}


def baseline():
    return {"ttft_p50_ms": 400.0, "ttft_p95_ms": 600.0, "total_p50_ms": 5000.0,
            "error_rate": 0.0, "kv_peak": 0.5}


# ---------------------------------------------------------------- extraction

def test_window_metrics_from_spinal():
    w = spinal_window([300, 400, 500, 600, 700], [4000, 5000, 6000, 7000, 8000])
    m = window_metrics(w)
    assert m["ttft_p50_ms"] == 500
    assert m["ttft_p95_ms"] == 700
    assert m["error_rate"] == 0.0


def test_window_metrics_error_rate():
    w = spinal_window([300, 400], [4000, 5000], n_failed=2)
    m = window_metrics(w)
    assert m["error_rate"] == 0.5


def test_window_metrics_kv_peak():
    w = spinal_window([300], [4000], kv=[0.1, 0.9])
    m = window_metrics(w)
    assert m["kv_peak"] == 0.9


def test_window_metrics_flat_dict():
    m = window_metrics({"ttft_p50_ms": 350, "error_rate": 0})
    assert m == {"ttft_p50_ms": 350.0, "error_rate": 0.0}


# ---------------------------------------------------------------- drift logic

def test_no_drift_no_alert():
    mon = DriftMonitor(baseline=baseline())
    for _ in range(5):
        st = mon.observe(spinal_window([400, 410, 390], [5000, 5100, 4900]))
    assert st["alerts"] == []
    assert mon.snapshot()["alerts_fired"] == {}


def test_single_breach_does_not_alert():
    """Hysteresis: one bad window is noise, not drift."""
    mon = DriftMonitor(baseline=baseline())
    mon.observe(spinal_window([400], [5000]))          # good
    st = mon.observe(spinal_window([900, 950], [5000]))  # one breach (ttft p50 925)
    assert st["alerts"] == []
    assert mon.snapshot()["breach_streaks"]["ttft_p50_ms"] == 1


def test_sustained_drift_alerts():
    mon = DriftMonitor(baseline=baseline())
    mon.observe(spinal_window([400], [5000]))
    mon.observe(spinal_window([900, 950], [5000]))   # breach 1
    st = mon.observe(spinal_window([900, 950], [5000]))  # breach 2
    st = mon.observe(spinal_window([900, 950], [5000]))  # breach 3 -> alert
    assert any(a["metric"] == "ttft_p50_ms" for a in st["alerts"])


def test_alert_fires_once_per_metric():
    fired = []
    mon = DriftMonitor(baseline=baseline(), on_alert=lambda a: fired.append(a["metric"]))
    bad = spinal_window([900, 950], [5000])
    mon.observe(spinal_window([400], [5000]))
    for _ in range(6):
        mon.observe(bad)
    assert fired.count("ttft_p50_ms") == 1  # not re-fired every window


def test_recovery_resets_streak():
    mon = DriftMonitor(baseline=baseline())
    mon.observe(spinal_window([400], [5000]))
    mon.observe(spinal_window([900, 950], [5000]))   # breach 1
    mon.observe(spinal_window([400], [5000]))        # recovery
    st = mon.observe(spinal_window([900, 950], [5000]))  # breach 1 again
    assert mon.snapshot()["breach_streaks"]["ttft_p50_ms"] == 1
    assert st["alerts"] == []


def test_error_rate_alerts_fast():
    """error_rate needs only 2 consecutive breaches."""
    fired = []
    mon = DriftMonitor(baseline=baseline(), on_alert=lambda a: fired.append(a["metric"]))
    mon.observe(spinal_window([400], [5000]))
    mon.observe(spinal_window([400], [5000], n_failed=1))   # breach 1
    mon.observe(spinal_window([400], [5000], n_failed=1))   # breach 2 -> alert
    assert "error_rate" in fired


def test_alert_callback_receives_details():
    got = {}
    mon = DriftMonitor(baseline=baseline(), on_alert=lambda a: got.setdefault(a["metric"], a))
    mon.observe(spinal_window([400], [5000]))
    for _ in range(3):
        mon.observe(spinal_window([900, 950], [5000]))
    assert "ttft_p50_ms" in got
    assert got["ttft_p50_ms"]["baseline"] == 400.0
    assert got["ttft_p50_ms"]["streak"] == 3


def test_alert_callback_exception_does_not_kill_monitor():
    def boom(a):
        raise RuntimeError("webhook down")
    mon = DriftMonitor(baseline=baseline(), on_alert=boom)
    mon.observe(spinal_window([400], [5000]))
    for _ in range(4):
        st = mon.observe(spinal_window([900, 950], [5000]))
    assert mon.snapshot()["alerts_fired"].get("ttft_p50_ms") is True


def test_unknown_metric_ignored():
    mon = DriftMonitor(baseline=baseline())
    st = mon.observe({"some_new_metric": 999})
    assert st["alerts"] == []


# ---------------------------------------------------------------- construction

def test_monitor_from_trace_file():
    p = Path.home() / "Spinal" / "traces" / "spinal_2026-09-16_225609.json"
    if not p.exists():
        pytest.skip("spinal trace not on this machine")
    mon = monitor_from_trace(str(p))
    assert mon.baseline["ttft_p50_ms"] > 0
    st = mon.observe(spinal_window([400], [5000]))
    assert "metrics" in st


def test_snapshot_serializable():
    mon = DriftMonitor(baseline=baseline())
    mon.observe(spinal_window([400], [5000]))
    json.dumps(mon.snapshot())  # must not raise
