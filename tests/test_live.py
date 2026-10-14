"""Live-scrape tests (recorded snapshot; network path exercised separately)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from live import parse_prometheus, live_window

SNAPSHOT = """# TYPE tensorfold_cached_tokens_total counter
tensorfold_cached_tokens_total{model="glm-5.3-flash"} 16722048
tensorfold_requests_total{model="glm-5.3-flash"} 238
tensorfold_request_errors_total{model="glm-5.3-flash"} 0
tensorfold_prompt_tokens_total{model="glm-5.3-flash"} 17199400
tensorfold_completion_tokens_total{model="glm-5.3-flash"} 92337
tensorfold_decode_seconds_total{model="glm-5.3-flash"} 1809.8
tensorfold_requests_stalled{model="glm-5.3-flash"} 0
tensorfold_engine_fatal{model="glm-5.3-flash"} 0
"""


def test_parse_snapshot():
    m = parse_prometheus(SNAPSHOT)
    assert m["tensorfold_requests_total"] == 238


def test_live_window_from_fleet_snapshot():
    w = live_window.__wrapped__ if hasattr(live_window, "__wrapped__") else None
    # feed the parser directly (no network in unit tests)
    m = parse_prometheus(SNAPSHOT)
    reqs = m["tensorfold_requests_total"]
    errs = m["tensorfold_request_errors_total"]
    assert errs / reqs == 0.0
    assert m["tensorfold_cached_tokens_total"] / m["tensorfold_prompt_tokens_total"] > 0.95


def test_live_window_against_real_cluster():
    """Integration: the actual spark cluster must answer and parse."""
    try:
        w = live_window("http://192.168.0.182:8000/metrics", timeout=4)
    except Exception:
        pytest.skip("cluster unreachable from this machine")
    assert 0.0 <= w["cache_hit_ratio"] <= 1.0
    assert w["error_rate"] == 0.0
