"""Parsers for CSV, JSON, manual-paste, and PDF metric input.

All parsers return a list of (metric_key, raw_value) tuples, or None when
nothing parseable is found. compare.py turns these into normalized metrics.
"""

import base64
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
# PDF
# ---------------------------------------------------------------------------
def pdf_to_text(payload):
    """Extract the text content of a PDF report.

    `payload` may be:
      - bytes of a PDF file
      - a base64 string of a PDF file
      - a dict {"text": ...} / {"data": ...} carrying base64 bytes
      - a dict with {"is_pdf": true, "text": <base64>} (as sent by the client)

    Returns the extracted plain text (as CSV/manual lines) or None when the
    payload is not a readable PDF.
    """
    raw = payload
    if isinstance(payload, dict):
        if payload.get("is_pdf") or payload.get("kind") == "pdf":
            raw = payload.get("text") or payload.get("data") or payload.get("content")
        else:
            raw = payload.get("data") or payload.get("content") or payload.get("text")

    if raw is None:
        return None

    data = raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None
        # data URI (data:application/pdf;base64,....)
        if s.startswith("data:"):
            try:
                s = s.split(",", 1)[1]
            except IndexError:
                return None
        try:
            data = base64.b64decode(s, validate=False)
        except Exception:
            data = s.encode("utf-8", "ignore")

    if isinstance(data, str):
        data = data.encode("utf-8", "ignore")
    if not data or not data[:4] == b"%PDF":
        return None

    try:
        from pypdf import PdfReader
    except Exception:
        return None

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = []
        for page in reader.pages:
            txt = page.extract_text() or ""
            if txt.strip():
                pages.append(txt)
        text = "\n".join(pages)
        return text.strip() or None
    except Exception:
        return None


def parse_pdf_metrics(payload):
    """Parse a PDF report directly into a normalized {key: float} metric dict.

    Extracts text with pdf_to_text() then runs the same CSV-then-manual
    parsing used for other sources, so a PDF that contains a metrics table
    (e.g. 'Metric,Previous,Current' rows or 'Sessions: 2400' lines) is read
    the same way as a CSV or pasted report.
    """
    text = pdf_to_text(payload)
    if not text:
        return None
    pairs = parse_csv(text) or parse_manual(text)
    return parsers_pdf_to_metric_dict(pairs) if pairs else None


def parsers_pdf_to_metric_dict(pairs):
    return to_metric_dict(pairs)


def parse_pdf_combined(payload):
    """Parse a two-period PDF report into (prev_dict, curr_dict) or None.

    pypdf usually extracts table cells one-per-line, e.g.::

        Metric
        Previous
        Current
        Sessions
        2400
        2650
        Drop-off Rate
        42%
        27%
        ...

    This reconstructs those ``Metric / prev / curr`` triples (and also falls
    back to CSV/JSON parsing of the extracted text for other layouts).
    """
    text = pdf_to_text(payload)
    if not text:
        return None

    # 1) Try the normal CSV / JSON combined paths on the extracted text.
    parsed = parse_csv_rows(text)
    if parsed:
        rows = parsed["rows"]
        if parsed["mode"] == "long" and all(len(v) >= 2 for _k, v in rows):
            prev = to_metric_dict([(k, v[0]) for k, v in rows])
            curr = to_metric_dict([(k, v[1]) for k, v in rows])
            if prev and curr:
                return prev, curr
        if parsed["mode"] == "transposed":
            n_periods = max((len(v) for v in rows), default=0)
            if n_periods >= 2:
                prev = to_metric_dict([(k, v[0]) for k, v in rows if len(v) >= 2])
                curr = to_metric_dict([(k, v[1]) for k, v in rows if len(v) >= 2])
                if prev and curr:
                    return prev, curr
        # single-period fall through

    # 2) PDF table reconstruction: one token per line -> Metric / prev / curr
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    # drop a leading title line (not a metric) and any standalone header words
    metric_lines = []
    i = 0
    while i < len(lines):
        key = resolve_key(lines[i])
        # a metric label followed by at least two more tokens
        if key and i + 2 < len(lines):
            prev_raw = lines[i + 1]
            curr_raw = lines[i + 2]
            # The following line is likely a value (not another metric label).
            if not resolve_key(prev_raw) and not resolve_key(curr_raw):
                metric_lines.append((key, prev_raw, curr_raw))
                i += 3
                continue
        i += 1

    if metric_lines:
        prev = to_metric_dict([(k, v) for k, v, _c in metric_lines])
        curr = to_metric_dict([(k, c) for k, _v, c in metric_lines])
        if prev and curr:
            return prev, curr

    return None


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def parse_csv(text):
    """Parse CSV text into a list of (key, raw_value) tuples.

    Supports:
      - Long format: Metric,Value / Metric,Previous,Current / Metric,June,July
      - Transposed pivot: metrics as column headers, periods as rows
      - Comma, semicolon, or tab delimiters; UTF-8 BOM; quoted values

    NOTE: when a file contains multiple periods, only the LAST period's values
    are returned here (single-period callers). Use parse_csv_full() for a
    full previous/current split.
    """
    rows = _read_rows(text)
    if not rows:
        return None
    pairs = _extract_long_rows(rows)
    if pairs:
        return pairs
    pairs = _extract_transposed(rows)
    return pairs or None


def parse_csv_rows(text):
    """Parse CSV and return ALL parsed metric rows with their column layout.

    Returns a dict::

        {
          "rows": [(key, [raw_col0, raw_col1, ...]), ...],  # one entry per metric
          "header": ["Metric", "Previous", "Current"],       # first row (or [])
          "has_header": bool,
          "mode": "long" | "transposed",
        }

    This is the authoritative parser used to build BOTH periods from a single
    Clarity/GA4 style export, so every metric present in the document is
    included. Rows whose metric label does not resolve to a known metric are
    skipped. `None` is returned when nothing parseable is found.
    """
    rows = _read_rows(text)
    if not rows:
        return None

    # --- Long format: metric labels in column 0 ---------------------------
    long_out = []
    header = rows[0]
    has_header = bool(header) and (
        _looks_like_header(header[0])
        or (len(header) > 1 and not _is_numeric(header[1]))
    )
    body = rows[1:] if has_header else rows
    for row in body:
        if not row or not row[0].strip():
            continue
        key = resolve_key(row[0])
        if not key:
            continue
        # keep every column value (as raw strings) for this metric
        vals = [c.strip() for c in row[1:] if c.strip()]
        if vals:
            long_out.append((key, vals))
    if long_out:
        return {
            "rows": long_out,
            "header": header if has_header else [],
            "has_header": has_header,
            "mode": "long",
        }

    # --- Transposed pivot: metrics across the header row -------------------
    if len(rows) < 2:
        return None
    header = rows[0]
    # skip the period label in col 0, then resolve metric columns
    start = 1 if not resolve_key(header[0]) and len(header) > 1 else 0
    metric_cols = [
        (i, resolve_key(c)) for i, c in enumerate(header)
        if i >= start and resolve_key(c)
    ]
    if len(metric_cols) < 1:
        return None
    # gather each metric's values across ALL data rows (each row = one period)
    per_metric_vals = {key: [] for _i, key in metric_cols}
    for row in rows[1:]:
        if not row:
            continue
        for i, key in metric_cols:
            if i < len(row) and row[i].strip():
                per_metric_vals[key].append(row[i].strip())
    rebuilt = [(key, vals) for key, vals in per_metric_vals.items() if vals]
    return {
        "rows": rebuilt,
        "header": header,
        "has_header": True,
        "mode": "transposed",
    }


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
