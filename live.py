"""Live fleet scrape — Forerunner observes the real cluster, not just files.

Same parsing contract as Chalkboard's live module (kept dependency-free
per repo): fetch TensorFold's read-only /metrics and flatten into a
Forerunner observation window.
"""
from __future__ import annotations

import re
import urllib.request

TF_METRIC_RE = re.compile(r"^(tensorfold_[a-z_]+)(?:\{[^}]*\})?\s+([0-9.eE+-]+)$")


def parse_prometheus(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = TF_METRIC_RE.match(line)
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


def fetch_metrics(url: str, timeout: float = 5.0) -> dict[str, float]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return parse_prometheus(resp.read().decode("utf-8"))


def live_window(url: str, timeout: float = 5.0) -> dict[str, float]:
    """One observation window from the live cluster, Forerunner-ready."""
    m = fetch_metrics(url, timeout)
    reqs = m.get("tensorfold_requests_total", 0.0)
    errs = m.get("tensorfold_request_errors_total", 0.0)
    prompt = m.get("tensorfold_prompt_tokens_total", 0.0)
    cached = m.get("tensorfold_cached_tokens_total", 0.0)
    completion = m.get("tensorfold_completion_tokens_total", 0.0)
    decode_s = m.get("tensorfold_decode_seconds_total", 0.0)
    return {
        "error_rate": errs / reqs if reqs else 0.0,
        "cache_hit_ratio": cached / prompt if prompt else 0.0,
        "decode_tok_s": completion / decode_s if decode_s else 0.0,
        "requests_stalled": m.get("tensorfold_requests_stalled", 0.0),
        "engine_fatal": m.get("tensorfold_engine_fatal", 0.0),
    }
