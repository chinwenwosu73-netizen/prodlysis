"""Comparison engine: turn two metric dicts into a full analysis."""

from .metrics import (
    METRIC_DEFS, METRIC_ORDER, format_value, format_change, parse_value,
)

UNCHANGED_THRESHOLD = 0.01  # |change| below 1% is "unchanged"


def _pct_change(prev, curr):
    """Signed percentage change: ((curr - prev) / prev) * 100."""
    if prev is None or curr is None or prev == 0:
        return None
    return (curr - prev) / prev * 100


def _status(prev, curr, higher_better):
    """Classify a metric as improved / regressed / unchanged."""
    if prev is None or curr is None:
        return None
    if prev == curr:
        return "unchanged"
    if higher_better:
        return "improved" if curr > prev else "regressed"
    return "improved" if curr < prev else "regressed"


def compare(previous, current):
    """Compare two normalized {key: float} dicts.

    Returns a dict with:
      - metrics: list of per-metric results (ordered)
      - health: overall UX health score 0..100
      - health_delta: point change vs the previous baseline (50)
      - summary stats used by the AI layer
    """
    previous = previous or {}
    current = current or {}
    keys = set(previous) | set(current)
    metrics = []

    for key in METRIC_ORDER:
        if key not in keys:
            continue
        defn = METRIC_DEFS[key]
        p = previous.get(key)
        c = current.get(key)
        if p is None and c is None:
            continue
        change = _pct_change(p, c)
        status = _status(p, c, defn["higher_better"])
        # Only call it "unchanged" when change is negligible
        if change is not None and abs(change) < UNCHANGED_THRESHOLD * 100:
            status = "unchanged"
        metrics.append({
            "key": key,
            "label": defn["label"],
            "group": defn["group"],
            "kind": defn["kind"],
            "unit": defn["unit"],
            "higher_better": defn["higher_better"],
            "previous": p,
            "current": c,
            "previous_display": format_value(p, defn["kind"]),
            "current_display": format_value(c, defn["kind"]),
            "change": change,
            "change_display": format_change(change),
            "status": status,
        })

    health, health_delta = _health_score(metrics)
    return {
        "metrics": metrics,
        "health": health,
        "health_delta": health_delta,
        "num_compared": len(metrics),
        "num_improved": sum(1 for m in metrics if m["status"] == "improved"),
        "num_regressed": sum(1 for m in metrics if m["status"] == "regressed"),
        "num_unchanged": sum(1 for m in metrics if m["status"] == "unchanged"),
    }


def _health_score(metrics):
    """Compute an overall UX health score in 0..100.

    Each metric contributes a normalized improvement in [-1, 1]:
      improvement = (curr - prev) / prev  (signed by whether higher is better)
    clamped to [-1, 1]. Health = 50 + 40 * avg(improvement) -> ~10..90.
    A baseline "previous" health is 50, so delta = health - 50.
    """
    if not metrics:
        return 50, 0
    improvements = []
    for m in metrics:
        p, c = m["previous"], m["current"]
        if p is None or c is None or p == 0:
            continue
        ratio = (c - p) / abs(p)
        if not m["higher_better"]:
            ratio = -ratio
        improvements.append(max(-1.0, min(1.0, ratio)))
    if not improvements:
        return 50, 0
    avg = sum(improvements) / len(improvements)
    health = round(50 + 40 * avg)
    health = max(5, min(98, health))
    delta = health - 50
    return health, delta
