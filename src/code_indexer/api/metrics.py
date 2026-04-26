"""
Per-tool latency metrics for the CodeIndexer API.

Maintains a thread-safe rolling window (last N samples per tool) and exposes
percentile stats. Tools are identified by a free-form string — typically
their MCP tool name or HTTP route. The implementation is dependency-free:
just a deque guarded by a lock, with on-demand sorting for percentiles.

Per-tool budgets default to a conservative latency ceiling. Calls that
exceed the budget log a warning so operators see slow paths in the
output channel without paging anyone.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from contextlib import contextmanager
from typing import Deque, Dict, Iterator, List, Optional

logger = logging.getLogger(__name__)

# Window size per tool. ~1000 keeps memory cheap (8 KB / tool) and gives a
# stable p99 for tools called more than a handful of times per session.
WINDOW_SIZE = 1000

# Default latency budgets in milliseconds. Used to surface slow calls in
# logs and in /api/metrics. Tune per-tool as we learn what's realistic.
DEFAULT_BUDGETS_MS: Dict[str, float] = {
    "search": 800.0,
    "search_code": 800.0,
    "find_symbol": 250.0,
    "get_code": 150.0,
    "get_callers": 250.0,
    "get_callees": 250.0,
    "get_impact": 500.0,
    "get_call_chain": 500.0,
    "get_file_structure": 200.0,
    "find_dead_code": 1000.0,
    "codebase_overview": 250.0,
    "diff_impact": 1000.0,
    "tests_for": 250.0,
    "tested_by": 250.0,
    "index_file": 5000.0,
    "remove_file": 500.0,
}

DEFAULT_BUDGET_MS = 1000.0


class _ToolStats:
    __slots__ = ("samples", "errors", "last_ms")

    def __init__(self) -> None:
        self.samples: Deque[float] = deque(maxlen=WINDOW_SIZE)
        self.errors: int = 0
        self.last_ms: float = 0.0


_lock = threading.Lock()
_stats: Dict[str, _ToolStats] = {}


def _percentile(sorted_values: List[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


def record(tool: str, elapsed_ms: float, error: bool = False) -> None:
    """Record a single call's latency for `tool`."""
    with _lock:
        bucket = _stats.get(tool)
        if bucket is None:
            bucket = _ToolStats()
            _stats[tool] = bucket
        bucket.samples.append(elapsed_ms)
        bucket.last_ms = elapsed_ms
        if error:
            bucket.errors += 1

    budget = DEFAULT_BUDGETS_MS.get(tool, DEFAULT_BUDGET_MS)
    if elapsed_ms > budget:
        logger.warning(
            f"[metrics] {tool} took {elapsed_ms:.0f}ms (budget {budget:.0f}ms)"
        )


@contextmanager
def time_tool(tool: str) -> Iterator[None]:
    """Context manager that records elapsed wall time for `tool`."""
    start = time.perf_counter()
    errored = False
    try:
        yield
    except Exception:
        errored = True
        raise
    finally:
        record(tool, (time.perf_counter() - start) * 1000.0, error=errored)


def snapshot() -> Dict[str, Dict[str, float]]:
    """Return a percentile snapshot for every tool with at least one sample."""
    out: Dict[str, Dict[str, float]] = {}
    with _lock:
        items = [(name, list(b.samples), b.errors, b.last_ms) for name, b in _stats.items()]

    for name, samples, errors, last_ms in items:
        if not samples:
            continue
        sorted_samples = sorted(samples)
        budget = DEFAULT_BUDGETS_MS.get(name, DEFAULT_BUDGET_MS)
        p99 = _percentile(sorted_samples, 99)
        out[name] = {
            "count": len(sorted_samples),
            "errors": errors,
            "last_ms": round(last_ms, 2),
            "p50_ms": round(_percentile(sorted_samples, 50), 2),
            "p95_ms": round(_percentile(sorted_samples, 95), 2),
            "p99_ms": round(p99, 2),
            "max_ms": round(sorted_samples[-1], 2),
            "mean_ms": round(sum(sorted_samples) / len(sorted_samples), 2),
            "budget_ms": budget,
            "over_budget": p99 > budget,
        }
    return out


def reset(tool: Optional[str] = None) -> None:
    """Reset stats for one tool or all tools (used by tests)."""
    with _lock:
        if tool is None:
            _stats.clear()
        else:
            _stats.pop(tool, None)
