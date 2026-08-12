"""Metric definitions, parsing, and formatting helpers."""

import re

# ---------------------------------------------------------------------------
# Metric catalog
# ---------------------------------------------------------------------------
METRIC_DEFS = {
    "sessions": {
        "label": "Sessions", "unit": "", "kind": "count",
        "higher_better": True, "group": "Traffic",
    },
    "dropoffs": {
        "label": "Drop-offs", "unit": "", "kind": "count",
        "higher_better": False, "group": "Friction",
    },
    "dropoff_rate": {
        "label": "Drop-off Rate", "unit": "%", "kind": "percent",
        "higher_better": False, "group": "Friction",
    },
    "rage_clicks": {
        "label": "Rage Clicks", "unit": "", "kind": "count",
        "higher_better": False, "group": "Friction",
    },
    "dead_clicks": {
        "label": "Dead Clicks", "unit": "", "kind": "count",
        "higher_better": False, "group": "Friction",
    },
    "scroll_depth": {
        "label": "Scroll Depth", "unit": "%", "kind": "percent",
        "higher_better": True, "group": "Engagement",
    },
    "session_duration": {
        "label": "Session Duration", "unit": "", "kind": "duration",
        "higher_better": True, "group": "Engagement",
    },
    "bounce_rate": {
        "label": "Bounce Rate", "unit": "%", "kind": "percent",
        "higher_better": False, "group": "Engagement",
    },
    "conversion_rate": {
        "label": "Conversion Rate", "unit": "%", "kind": "percent",
        "higher_better": True, "group": "Conversion",
    },
}

# Order used for display in tables / reports.
METRIC_ORDER = [
    "sessions", "dropoffs", "dropoff_rate", "rage_clicks", "dead_clicks",
    "scroll_depth", "session_duration", "bounce_rate", "conversion_rate",
]

# ---------------------------------------------------------------------------
# Text -> metric key resolution
# ---------------------------------------------------------------------------
_ALIASES = {
    "sessions": {"sessions", "session", "total sessions", "visits", "users",
                 "unique visitors", "page views", "pageviews", "traffic"},
    "dropoffs": {"dropoffs", "drop offs", "drop off count", "drop-offs"},
    "dropoff_rate": {
        "dropoff rate", "drop off rate", "drop-off rate", "dropoff",
        "drop off percentage", "dropoff %", "drop-off %", "funnel drop",
        "funnel dropoff", "checkout drop off",
    },
    "rage_clicks": {"rage clicks", "rage click", "rageclick", "rage clicks"},
    "dead_clicks": {"dead clicks", "dead click", "deadclick", "dead clicks"},
    "scroll_depth": {"scroll depth", "scroll depth %", "avg scroll depth",
                     "average scroll depth", "scroll"},
    "session_duration": {
        "session duration", "avg session duration", "time on site",
        "avg time on page", "average session duration", "avg session time",
        "time spent", "average time on site",
    },
    "bounce_rate": {"bounce rate", "bounce rate %", "bounce"},
    "conversion_rate": {"conversion rate", "conversion", "conversion rate %",
                        "conversions", "conversion %"},
}

def _norm(text: str) -> str:
    """Normalize a label for fuzzy matching: lowercase, alnum only."""
    return re.sub(r"[^a-z0-9%]", "", str(text).lower())


_ALIAS_LOOKUP = {}
for _key, _aliases in _ALIASES.items():
    for _a in _aliases:
        _ALIAS_LOOKUP[_norm(_a)] = _key
    _ALIAS_LOOKUP[_norm(_key)] = _key
    _ALIAS_LOOKUP[_norm(METRIC_DEFS[_key]["label"])] = _key


def resolve_key(text: str):
    """Resolve a human label (or key) to a canonical metric key, or None."""
    if not text:
        return None
    n = _norm(str(text))
    if n in _ALIAS_LOOKUP:
        return _ALIAS_LOOKUP[n]
    # fall back to substring matching against known labels
    for key in METRIC_DEFS:
        label = _norm(METRIC_DEFS[key]["label"])
        if label and (label in n or n in label):
            return key
    return None


# ---------------------------------------------------------------------------
# Value parsing / formatting
# ---------------------------------------------------------------------------
_DURATION_RE = re.compile(
    r"^\s*(?:(\d+)\s*(?:h|hr|hrs|hours))?\s*(?:(\d+)\s*(?:m|min|mins|minutes))?"
    r"\s*(?:(\d+)\s*(?:s|sec|secs|seconds))?\s*$", re.IGNORECASE,
)


def parse_duration(text: str):
    """Parse things like '1m 10s', '2:05', '90s', '1h 5m' into seconds."""
    s = str(text).strip().lower()
    if not s:
        return None
    if ":" in s:
        parts = s.split(":")
        try:
            if len(parts) == 2:
                return int(parts[0]) * 60 + int(parts[1])
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        except (TypeError, ValueError):
            return None
    m = _DURATION_RE.match(s)
    if m and any(m.groups()):
        h, mi, sec = (int(g) if g else 0 for g in m.groups())
        return h * 3600 + mi * 60 + sec
    return None


def parse_value(raw, kind="count"):
    """Parse a raw value into a float, according to the metric kind."""
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip()
    if not s:
        return None
    if kind == "duration":
        v = parse_duration(s)
        return float(v) if v is not None else None
    s = s.replace(",", "").replace("%", "").replace(" ", "").strip()
    if s in ("", "-", "--", "n/a", "na", "null", "none"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def format_value(value, kind="count"):
    """Format a numeric value for display."""
    if value is None:
        return "—"
    if kind == "duration":
        v = int(round(value))
        h, rem = divmod(v, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}h {m}m"
        if m:
            return f"{m}m {s:02d}s"
        return f"{s}s"
    if kind == "percent":
        return f"{value:g}%"
    # count
    return f"{int(round(value)):,}"


def format_change(change_pct, decimals=1):
    """Format a percentage change with an explicit sign."""
    if change_pct is None:
        return "—"
    return f"{change_pct:+.{decimals}f}%"


def metric_def(key):
    return METRIC_DEFS.get(key)
