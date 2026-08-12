"""Parsers for CSV, JSON, and manual-paste metric input.

All parsers return a list of (metric_key, raw_value) tuples, or None when
nothing parseable is found. compare.py turns these into normalized metrics.
"""

import csv
import io
import json
import re

from .metrics import resolve_key, parse_value, METRIC_DEFS

# Labels that signal a header / non-metric first cell.
_HEADER_WORDS = {
    "metric", "metrics", "previous", "current", "before", "after", "period",
    "month", "value", "values", "count", "clarity", "report",
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
}


def _clean(s):
    return re.sub(r"[^a-zA-Z0-9%]", "", str(s or "")).lower()


def _is_numeric(s):
    s = str(s or "").replace("%", "").replace(",", "").replace(":", "").strip()
    try:
        float(s)
        return True
    except ValueError:
        return False


def _looks_like_header(cell):
    c = _clean(cell)
    return c in _HEADER_WORDS


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def parse_csv(text):
    """Parse CSV text into a list of (key, raw_value) tuples.

    Supports:
      - Long format: Metric,Value / Metric,Previous,Current / Metric,June,July
      - Transposed pivot: metrics as column headers, periods as rows
      - Comma, semicolon, or tab delimiters; UTF-8 BOM; quoted values
    """
    rows = _read_rows(text)
    if not rows:
        return None
    pairs = _extract_long_rows(rows)
    if pairs:
        return pairs
    pairs = _extract_transposed(rows)
    return pairs or None


def parse_csv_full(text):
    """Parse a combined CSV with 3+ columns: returns (key, col2, col3) triples.

    Handles long format 'Metric,Previous,Current' and transposed pivots.
    Returns a list of (key, value_a, value_b) or None.
    """
    rows = _read_rows(text)
    if not rows:
        return None
    # Long format
    out = []
    header = rows[0]
    has_header = bool(header) and (
        _looks_like_header(header[0])
        or (len(header) > 1 and not _is_numeric(header[1]))
    )
    for row in rows[1:] if has_header else rows:
        if not row or not row[0].strip():
            continue
        key = resolve_key(row[0])
        if not key:
            continue
        if len(row) >= 3 and row[1].strip() and row[2].strip():
            out.append((key, row[1].strip(), row[2].strip()))
    if out:
        return out
    # Transposed pivot: first column holds period labels, other columns are metrics
    return _extract_transposed_full(rows)


# ---------------------------------------------------------------------------
# CSV internals
# ---------------------------------------------------------------------------
def _read_rows(text):
    if not text or not text.strip():
        return None
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")
    delim = _detect_delim(text)
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    return [r for r in reader if any(cell.strip() for cell in r)]


def _detect_delim(text):
    sample = text[:2000]
    lines = [ln for ln in sample.splitlines() if ln.strip()]
    if not lines:
        return ","
    best, best_count = ",", sum(ln.count(",") for ln in lines)
    for candidate in (";", "\t"):
        c = sum(ln.count(candidate) for ln in lines)
        if c > best_count:
            best, best_count = candidate, c
    return best


def _count_delim(line, delim):
    return line.count(delim)


def _extract_long_rows(rows):
    """Standard 'Metric,Value...' layout (metric label in first column)."""
    pairs = []
    header = rows[0]
    has_header = bool(header) and (
        _looks_like_header(header[0])
        or (len(header) > 1 and not _is_numeric(header[1]))
    )
    for row in rows[1:] if has_header else rows:
        if not row or not row[0].strip():
            continue
        key = resolve_key(row[0])
        if not key:
            continue
        val = ""
        if len(row) >= 3 and _clean(row[1]) in ("previous", "before", "past"):
            val = row[2].strip()
        elif len(row) >= 2:
            val = row[1].strip()
        if val:
            pairs.append((key, val))
    return pairs or None


def _extract_transposed(rows):
    """Transposed pivot: the FIRST ROW holds metric labels in columns.

    Handles:
      - With a period label in col 0:
          Metric,Sessions,Drop-off Rate,Rage Clicks
          June,2400,42%,118
      - Without a period label (single-period export):
          Sessions,Drop-off Rate,Rage Clicks
          2400,42%,118
    """
    if len(rows) < 2:
        return None
    header = rows[0]

    # How many header cells (across ALL columns) resolve to a metric label?
    metric_cols = [(i, c) for i, c in enumerate(header) if resolve_key(c)]
    # A transposed file has metric labels in the header row (2+ of them).
    if len(metric_cols) < 2:
        return None

    # If col 0 is a period label (not a metric) it's a pivot with periods.
    # If col 0 IS a metric (single-period export), that's fine too.
    data_row = rows[1]
    pairs = []
    for i, label in metric_cols:
        if i < len(data_row) and data_row[i].strip():
            pairs.append((resolve_key(label), data_row[i].strip()))
    return pairs or None


def _extract_transposed_full(rows):
    """Transposed pivot with two periods as rows -> (key, row1_val, row2_val).

    Expects a period label in col 0 and metric labels across columns 1..n:
      Metric,Sessions,Drop-off Rate,Rage Clicks
      June,2400,42%,118
      July,2650,27%,64
    """
    if len(rows) < 3:
        return None
    header = rows[0]
    # col 0 must be a period label (NOT a metric) to be a pivot with periods
    if resolve_key(header[0]):
        return None
    metric_cols = [(i, c) for i, c in enumerate(header)
                   if i >= 1 and resolve_key(c)]
    if len(metric_cols) < 2:
        return None
    out = []
    r1, r2 = rows[1], rows[2]
    for i, label in metric_cols:
        if i < len(r1) and i < len(r2) and r1[i].strip() and r2[i].strip():
            out.append((resolve_key(label), r1[i].strip(), r2[i].strip()))
    return out or None


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------
def parse_json(text):
    """Parse JSON into a list of (key, raw_value) tuples.

    Supports:
      - Flat object: {"Sessions": 1200, "Drop-offs": 42, ...}
      - Nested: {"previous": {...}, "current": {...}}  -> uses "current" or
        first object
      - Array of objects with metric/value keys:
        [{"metric": "Sessions", "value": 1200}, ...]
      - Array of two flat objects (takes the last one by default -> caller
        can pass 'current'/'previous' selection).
    """
    if not text or not text.strip():
        return None
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
    pairs = _extract_json_pairs(data)
    return pairs or None


def _extract_json_pairs(data):
    """Recursively find (key, raw) metric pairs in arbitrary JSON."""
    pairs = []
    if isinstance(data, dict):
        for k, v in data.items():
            key = resolve_key(k)
            if key and isinstance(v, (int, float)) and not isinstance(v, bool):
                pairs.append((key, v))
            elif key and isinstance(v, str) and any(ch.isdigit() for ch in v):
                pairs.append((key, v))
            elif isinstance(v, dict):
                pairs.extend(_extract_json_pairs(v))
            elif isinstance(v, list):
                pairs.extend(_extract_json_pairs(v))
    elif isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                metric = item.get("metric") or item.get("name") or item.get("label")
                value = item.get("value") or item.get("count") or item.get("amount")
                if metric is not None and value is not None:
                    key = resolve_key(metric)
                    if key:
                        pairs.append((key, value))
                else:
                    pairs.extend(_extract_json_pairs(item))
            elif isinstance(item, (int, float, str)):
                continue
    return pairs


# ---------------------------------------------------------------------------
# Manual paste
# ---------------------------------------------------------------------------
def parse_manual(text):
    """Parse pasted text like:

        Drop-offs: 42%
        Rage Clicks 118
        Sessions = 1200

    into (key, raw) tuples.
    """
    if not text or not text.strip():
        return None
    pairs = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # "label: value", "label = value", "label value"
        m = re.match(r"^(.+?)\s*[:=]\s*(.+)$", line)
        if m:
            label, val = m.group(1).strip(), m.group(2).strip()
        else:
            parts = line.split()
            if len(parts) >= 2:
                label, val = " ".join(parts[:-1]), parts[-1]
            else:
                continue
        key = resolve_key(label)
        if key and any(ch.isdigit() for ch in val):
            pairs.append((key, val))
    return pairs or None


def to_metric_dict(pairs):
    """Convert (key, raw) pairs into a normalized {key: float} dict."""
    out = {}
    if not pairs:
        return out
    for key, raw in pairs:
        if key is None:
            continue
        defn = METRIC_DEFS.get(key)
        if not defn:
            continue
        value = parse_value(raw, defn["kind"])
        if value is not None:
            out[key] = value
    return out
