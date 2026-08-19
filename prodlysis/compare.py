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


# ---------------------------------------------------------------------------
# User drop-off analysis
# ---------------------------------------------------------------------------
# The metric keys that tell us where/how many users abandon a flow.
_DROPOFF_KEYS = ("dropoffs", "dropoff_rate")

# Human-friendly descriptions per metric, used to name the drop-off point.
_DROPOFF_POINT_NAMES = {
    "dropoffs": "users abandoning the flow",
    "dropoff_rate": "the share of users who drop off",
}


def analyze_dropoffs(metrics):
    """Analyze the drop-off points for the current period's metrics.

    `metrics` is a normalized {key: float} dict (the CURRENT period). It reads
    whichever drop-off related metrics are actually present in the document and
    produces a structured, prioritized drop-off analysis:

      {
        "dropoff_points": [ {metric, label, display, status, note, point}, ... ],
        "worst": "Drop-off Rate",            # the most alarming metric (or None)
        "summary": str,
        "causes": [str, ...],
        "recommendations": [ {text, priority}, ... ],
      }
    """
    from .metrics import METRIC_DEFS, format_value

    defn_dropoffs = METRIC_DEFS.get("dropoffs")
    defn_dropoff_rate = METRIC_DEFS.get("dropoff_rate")

    points = []
    statuses = []
    dropoff_abs = None
    if "dropoffs" in metrics and metrics["dropoffs"] is not None:
        val = metrics["dropoffs"]
        dropoff_abs = val
        status = "poor" if val > 1000 else ("ok" if val > 300 else "good")
        statuses.append(status)
        points.append({
            "metric": "dropoffs",
            "label": defn_dropoffs["label"],
            "display": format_value(val, defn_dropoffs["kind"]),
            "status": status,
            "note": "Total number of sessions that left before completing the primary action.",
            "point": "Sessions are dropping out before reaching the end of the primary flow.",
        })
    if "dropoff_rate" in metrics and metrics["dropoff_rate"] is not None:
        val = metrics["dropoff_rate"]
        status = "poor" if val > 35 else ("ok" if val > 20 else "good")
        statuses.append(status)
        points.append({
            "metric": "dropoff_rate",
            "label": defn_dropoff_rate["label"],
            "display": format_value(val, defn_dropoff_rate["kind"]),
            "status": status,
            "note": "Percentage of sessions that drop off before completing the primary action.",
            "point": "A large share of users abandons the primary flow before conversion.",
        })

    if not points:
        return None

    worst_metric = None
    if "dropoff_rate" in metrics:
        worst_metric = "dropoff_rate"
    elif "dropoffs" in metrics:
        worst_metric = "dropoffs"
    worst = next((p for p in points if p["metric"] == worst_metric), points[0])

    # Severity summary
    poor_n = statuses.count("poor")
    ok_n = statuses.count("ok")
    good_n = statuses.count("good")
    if poor_n:
        summary = (
            f"User drop-off is HIGH. {worst['label']} is {worst['display']}, which is "
            f"above the healthy range and signals real friction in the primary flow. "
            f"Address the drop-off points below before the next release."
        )
    elif ok_n and not good_n:
        summary = (
            f"User drop-off is moderate. {worst['label']} is {worst['display']} - "
            f"inside the acceptable range but still worth improving before the next release."
        )
    else:
        summary = (
            f"User drop-off is low. {worst['label']} is {worst['display']}, within a "
            f"healthy range. Keep monitoring and protect this flow when shipping changes."
        )

    causes = _dropoff_causes(worst)
    recommendations = _dropoff_recommendations(metrics, worst)

    return {
        "dropoff_points": points,
        "worst": worst["label"],
        "summary": summary,
        "causes": causes,
        "recommendations": recommendations,
    }


def _dropoff_causes(worst):
    """Likely causes for the drop-off, based on the metrics present."""
    if not worst:
        return []
    causes = []
    if worst["metric"] == "dropoff_rate":
        causes.append(
            "A high drop-off rate usually means the primary flow is too long, confusing, "
            "or asks for too much at once (checkout / signup forms are the usual suspects)."
        )
    if worst["metric"] == "dropoffs":
        causes.append(
            "A high absolute number of drop-offs often indicates a specific step where "
            "users repeatedly abandon (loading, missing feedback, or an unclear next action)."
        )
    causes.append(
        "Inspect the funnel where sessions are lost and look for broken elements, "
        "unexpected costs, or missing trust signals at the abandonment step."
    )
    return causes


def _dropoff_recommendations(metrics, worst):
    """Prioritized, specific fixes to reduce user drop-off."""
    recs = []
    seen = set()

    def add(text, priority):
        if text not in seen:
            seen.add(text)
            recs.append({"text": text, "priority": priority})

    # Friction signals that commonly accompany drop-off
    if metrics.get("rage_clicks") is not None and metrics["rage_clicks"] > 60:
        add("High rage clicks near the drop-off step - fix elements that look clickable but do nothing.", "High")
    if metrics.get("dead_clicks") is not None and metrics["dead_clicks"] > 60:
        add("Dead-click clusters at the abandonment point - make the intended action obvious and clickable.", "High")
    if metrics.get("bounce_rate") is not None and metrics["bounce_rate"] > 40:
        add("Entry pages bounce quickly - tighten the value proposition above the fold.", "Medium")

    if worst and worst["metric"] == "dropoff_rate":
        add("Reduce the number of steps in the primary flow and add a progress indicator.", "High")
        add("Shorten forms to the minimum fields needed to complete the action.", "High")
    if worst and worst["metric"] == "dropoffs":
        add("Add a visible, high-contrast primary CTA at the exact point where users drop off.", "High")
        add("Add reassurance (guarantees, shipping info, reviews) right before the step where users leave.", "Medium")

    if len(recs) < 3:
        extras = [
            "Review the latest change to the primary flow - it may have introduced friction.",
            "Add an exit-intent survey or feedback prompt at the drop-off step to learn why users leave.",
        ]
        for t in extras:
            add(t, "Medium")

    return recs[:5]
