# Forerunner I

**Production drift monitor — the eval suite that never stops updating.**

Golden datasets go stale. Forerunner I is the sampling daemon: it continuously compares live observation windows against a baseline and alerts when a tracked metric drifts beyond tolerance — before users complain.

Extends [Spinal](https://github.com/TechGuardCoders/Spinal)'s drift concept (two trace files compared once) into a **stateful, continuous monitor**.

## What it tracks

| Metric | Direction | Tolerance | Breaches to alert |
|---|---|---|---|
| `ttft_p50_ms` | lower-better | 20% | 3 consecutive |
| `ttft_p95_ms` | lower-better | 25% | 3 |
| `total_p50_ms` | lower-better | 15% | 3 |
| `error_rate` | lower-better | 0% | 2 (fast — errors are urgent) |
| `kv_peak` | lower-better | 30% | 5 |

## The hysteresis design

One bad window is noise, not drift. A metric must breach tolerance for **N consecutive windows** before an alert fires, and each metric alerts **once** until it recovers. A recovery window resets the streak. This is what separates a monitor from a pager that cries wolf.

## Quick start

```python
from forerunner import DriftMonitor, monitor_from_trace

# baseline from a known-good Spinal trace
mon = monitor_from_trace("traces/good_baseline.json",
                         on_alert=lambda a: page_oncall(a["message"]))

# feed windows (e.g. every 5 minutes from your sampler)
status = mon.observe(live_trace_doc)
status["alerts"]   # [] or [{metric, value, baseline, streak, message}]
mon.snapshot()     # stateful: streaks, fired-alerts, windows seen
```

Windows are Spinal-shaped docs (`{"traces": [...]}`) or flat metric dicts. Unknown metrics are ignored — new instrumentation slots in without config changes.

## Part of the TechGuardCoders portfolio

- **Spinal** produces the traces; Forerunner I watches them continuously
- **Pocket Watching** can render `snapshot()` state alongside cost
- **NK-NODE** candidate: appliance health + finding-quality decay monitoring

## Tests

```bash
python -m pytest tests/ -q   # 15 tests
```

The suite encodes the hysteresis contract: single breaches never alert, sustained drift alerts exactly once per metric, recovery resets streaks, and a crashing alert callback can't kill the monitor loop. The baseline is built from the fleet's real Spinal trace.
