"""Prodlysis - Flask application entry point.

Run:  python app.py   ->  http://127.0.0.1:5000
"""

import io
import json
import os
import secrets
from datetime import datetime

from dotenv import load_dotenv
from flask import (
    Flask, abort, jsonify, redirect, render_template, request, send_file,
    url_for,
)

from prodlysis import ai, compare, parsers, report, store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Use store's resilient DATA_DIR (falls back to a writable temp dir on
# read-only filesystems like Vercel serverless).
DATA_DIR = store.DATA_DIR

# Load API keys from .env in the product folder (never stored in the app).
load_dotenv(os.path.join(BASE_DIR, ".env"))

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
app.config["JSON_SORT_KEYS"] = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _read_payload(payload):
    """Given a source payload {text, filename}, return the raw text (or None).

    Also handles a 'path' upload style where the browser sends file contents
    as text (base64 not needed since we read client-side).

    PDF payloads (base64 bytes sent from the client, or a data URI) are
    detected and their text is extracted here, so downstream CSV/manual
    parsing works on the PDF's contents.
    """
    if not payload:
        return None
    if isinstance(payload, str):
        # A raw string that is a base64/data-URI PDF
        if payload.lstrip().startswith("data:application/pdf") or _is_base64_pdf(payload):
            return parsers.pdf_to_text(payload)
        return payload
    text = payload.get("text")
    if payload.get("is_pdf") or payload.get("kind") == "pdf":
        return parsers.pdf_to_text(payload)
    if text and text.lstrip().startswith("data:application/pdf"):
        return parsers.pdf_to_text(text)
    return text if text else None


def _is_base64_pdf(s):
    """Cheap heuristic: a base64 blob that decodes to a %PDF header."""
    if not s or not isinstance(s, str):
        return False
    s = s.strip()
    if len(s) < 8:
        return False
    if s.startswith("data:application/pdf"):
        return True
    try:
        import base64
        head = base64.b64decode(s[:64], validate=False)
        return head[:4] == b"%PDF"
    except Exception:
        return False


def _parse_source(payload, fmt_hint):
    """Parse a source payload into a normalized {key: float} metric dict."""
    text = _read_payload(payload)
    if not text or not text.strip():
        return {}

    fmt = (fmt_hint or "").lower()

    # JSON (explicit or sniffed)
    stripped = text.lstrip()
    if fmt == "json" or stripped.startswith("{") or stripped.startswith("["):
        pairs = parsers.parse_json(text)
        if pairs:
            return parsers.to_metric_dict(pairs)

    # Manual paste (explicit or sniffed)
    if fmt == "manual":
        return parsers.to_metric_dict(parsers.parse_manual(text))

    # CSV (explicit or default)
    if fmt == "csv":
        pairs = parsers.parse_csv(text)
    else:
        # Try CSV first, then manual
        pairs = parsers.parse_csv(text)
        if not pairs:
            pairs = parsers.parse_manual(text)
    return parsers.to_metric_dict(pairs)


def _split_combined(payload, fmt_hint=""):
    """Split a single 'Metric,Previous,Current' payload into (prev, curr).

    Returns (prev_dict, curr_dict) or None if it isn't a combined file.
    Supports CSV, JSON, and PDF payloads (base64 or data URI).
    """
    text = _read_payload(payload)
    if not text or not text.strip():
        return None

    # PDF combined file: pypdf table reconstruction (Metric / prev / curr).
    if isinstance(payload, dict) and (payload.get("is_pdf") or payload.get("kind") == "pdf"):
        split = parsers.parse_pdf_combined(payload)
        if split:
            return split

    # CSV combined file (all metrics present in the document are used)
    parsed = parsers.parse_csv_rows(text)
    if parsed:
        rows = parsed["rows"]
        mode = parsed["mode"]
        if mode == "long":
            # long format: each row is (key, [col1, col2, ...]) where col1 is
            # the FIRST period value and col2 (if present) is the SECOND.
            two_period = all(len(vals) >= 2 for _k, vals in rows)
            if two_period:
                prev = parsers.to_metric_dict([(k, v[0]) for k, v in rows])
                curr = parsers.to_metric_dict([(k, v[1]) for k, v in rows])
                if prev and curr:
                    return prev, curr
            # single-period long format -> treat as "current" only
            return None
        # transposed: each (key, vals) has one value per period row
        vals_per_metric = [v for _k, v in rows]
        n_periods = max((len(v) for v in vals_per_metric), default=0)
        if n_periods >= 2:
            prev = parsers.to_metric_dict([(k, v[0]) for k, v in rows if len(v) >= 2])
            curr = parsers.to_metric_dict([(k, v[1]) for k, v in rows if len(v) >= 2])
            if prev and curr:
                return prev, curr
        return None

    # JSON with previous/current keys
    try:
        import json as _json
        data = _json.loads(text)
    except Exception:
        return None
    if isinstance(data, dict) and "previous" in data and "current" in data:
        prev = _parse_source({"text": _json.dumps(data["previous"])}, "json")
        curr = _parse_source({"text": _json.dumps(data["current"])}, "json")
        if prev and curr:
            return prev, curr
    return None


def _label(data, key, default):
    lbl = data.get(key)
    return str(lbl).strip() if lbl else default


def _build_full_analysis(previous, current, previous_label, current_label,
                         combined_payload=None):
    """Run the compare + AI pipeline and return a serializable analysis.

    `combined_payload` (optional) is the raw source text of a single uploaded
    file that contains BOTH periods; it is used for the drop-off analysis and
    (as a fallback) for deriving the periods themselves.
    """
    # If one period is missing but a combined source was provided, derive it.
    if combined_payload is not None and (not previous or not current):
        split = _split_combined(combined_payload, "")
        if split:
            if not previous:
                previous = split[0]
            if not current:
                current = split[1]

    comparison = compare.compare(previous, current)
    analysis = ai.analyze(comparison, previous, current)
    analysis = ai.llm_enrich(analysis, comparison)

    # User drop-off analysis, built from the CURRENT period's metrics
    dropoff = compare.analyze_dropoffs(current)

    # Most-changed metric sentence
    most_changed = None
    changed = [
        m for m in comparison["metrics"]
        if m["change"] is not None
        and m["status"] in ("improved", "regressed")
        and abs(m["change"]) >= 1.0
    ]
    if changed:
        top = max(changed, key=lambda m: abs(m["change"]))
        direction = "improved" if top["status"] == "improved" else "regressed"
        most_changed = (
            f"{top['label']} {direction} the most: "
            f"{top['previous_display']} → {top['current_display']} ({top['change_display']})."
        )

    return {
        "previous_label": previous_label,
        "current_label": current_label,
        "health": comparison["health"],
        "health_delta": comparison["health_delta"],
        "num_compared": comparison["num_compared"],
        "num_improved": comparison["num_improved"],
        "num_regressed": comparison["num_regressed"],
        "num_unchanged": comparison["num_unchanged"],
        "most_changed": most_changed,
        "metrics": comparison["metrics"],
        "summary": analysis["summary"],
        "findings": analysis["findings"],
        "recommendations": analysis["recommendations"],
        "next_steps": analysis["next_steps"],
        "questions": analysis["questions"],
        # User drop-off analysis (from the current period)
        "dropoff_analysis": dropoff,
        # DeepSeek comprehensive sections (present only when LLM provided them)
        "per_metric": analysis.get("per_metric"),
        "cross_metric": analysis.get("cross_metric"),
        "ab_tests": analysis.get("ab_tests"),
        "business_impact": analysis.get("business_impact"),
        "stakeholder_summary": analysis.get("stakeholder_summary"),
        "risk_watchlist": analysis.get("risk_watchlist"),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }


def _analysis_from_report(r):
    """Return the analysis dict stored inside a saved report.

    Normalizes older/legacy reports (including single-period reports saved
    before normalization existed) so history, the report view, and exports
    always render correctly.
    """
    analysis = r.get("analysis") or r
    rtype = r.get("type") or ("single" if "health_label" in analysis else "compare")
    return _normalize_analysis(analysis, rtype)


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.route("/")
def dashboard():
    latest = None
    if "latest_report_id" in request.cookies:
        r = store.get_report(request.cookies["latest_report_id"])
        if r:
            latest = _analysis_from_report(r)
    history = store.list_reports()[:6]
    return render_template("dashboard.html", latest=latest, history=history,
                           has_ai=_has_ai())


@app.route("/compare")
def compare_page():
    history = store.list_reports()[:8]
    return render_template("compare.html", history=history, has_ai=_has_ai())


@app.route("/history")
def history_page():
    reports = store.list_reports()
    return render_template("history.html", reports=reports)


@app.route("/about")
def about_page():
    return render_template("about.html")


@app.route("/reports/<report_id>")
def report_page(report_id):
    r = store.get_report(report_id)
    if not r:
        abort(404)
    analysis = _analysis_from_report(r)
    return render_template("report_view.html", report=r, analysis=analysis)


@app.route("/settings", methods=["GET"])
def settings_page():
    return render_template("settings.html",
                           profile=store.load_profile(),
                           has_ai=_has_ai(),
                           configured_provider=_configured_provider())


def _configured_provider():
    provider = (os.environ.get("AI_PROVIDER") or "none").lower()
    keys = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }
    key_env = keys.get(provider)
    if key_env and os.environ.get(key_env):
        return provider
    # fall back to whichever key is set
    for name, env in keys.items():
        if os.environ.get(env):
            return name
    return "none"


def _has_ai():
    provider = (os.environ.get("AI_PROVIDER") or "").lower()
    keys = (
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
    )
    return bool(provider and keys)


# ---------------------------------------------------------------------------
# Profile & preferences API
# ---------------------------------------------------------------------------
@app.route("/api/profile", methods=["GET", "POST"])
def api_profile():
    if request.method == "GET":
        return jsonify(_public_profile())
    data = request.get_json(force=True, silent=True) or {}
    profile = store.load_profile()
    for key in ("name", "email", "job_title"):
        if key in data:
            profile[key] = str(data[key] or "").strip()
    if data.get("avatar"):
        avatar = _sanitize_avatar(data["avatar"])
        if avatar:
            profile["avatar"] = avatar
    store.save_profile(profile)
    return jsonify(_public_profile())


@app.route("/api/preferences", methods=["POST"])
def api_preferences():
    data = request.get_json(force=True, silent=True) or {}
    profile = store.load_profile()
    prefs = dict(profile.get("preferences") or {})
    allowed = {"theme", "language", "default_export", "notifications"}
    for key in allowed:
        if key in data:
            prefs[key] = data[key]
    profile["preferences"] = prefs
    store.save_profile(profile)
    return jsonify({"ok": True})


@app.route("/api/password", methods=["POST"])
def api_password():
    """Reset/set the local password (placeholder until Google auth)."""
    from werkzeug.security import generate_password_hash, check_password_hash

    data = request.get_json(force=True, silent=True) or {}
    current = str(data.get("current_password") or "")
    new = str(data.get("new_password") or "")
    confirm = str(data.get("confirm_password") or "")
    if len(new) < 8:
        return jsonify({"error": "New password must be at least 8 characters."}), 400
    if new != confirm:
        return jsonify({"error": "Passwords do not match."}), 400
    profile = store.load_profile()
    if profile.get("password_hash"):
        if not check_password_hash(profile["password_hash"], current):
            return jsonify({"error": "Current password is incorrect."}), 400
    profile["password_hash"] = generate_password_hash(new)
    store.save_profile(profile)
    return jsonify({"ok": True})


@app.route("/api/clear-data", methods=["POST"])
def api_clear_data():
    store.clear_all_data()
    return jsonify({"ok": True})


def _public_profile():
    """Profile JSON without the password hash."""
    p = store.load_profile()
    return {k: v for k, v in p.items() if k != "password_hash"}


def _sanitize_avatar(data_uri):
    """Validate a base64 data-URI avatar; return it or None."""
    if not data_uri or not isinstance(data_uri, str):
        return None
    if not data_uri.startswith("data:image/"):
        return None
    if len(data_uri) > 2_000_000:  # 2MB cap
        return None
    return data_uri


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
@app.route("/api/analyze", methods=["POST"])
def api_analyze():
    data = request.get_json(force=True, silent=True) or {}
    previous = _parse_source(data.get("previous"), data.get("previous_format"))
    current = _parse_source(data.get("current"), data.get("current_format"))

    # Combined source: a single file/paste that holds both periods
    # ("Metric,Previous,Current" CSV, two-row pivot, {previous, current} JSON,
    # or a PDF table).
    combined_payload = None
    if data.get("combined"):
        combined_src = data["combined"]
        combined_payload = _read_payload(combined_src)
        if combined_payload:
            # Keep the original payload shape so PDF flags are preserved.
            split = _split_combined(combined_src, "auto")
            if split:
                previous = previous or split[0]
                current = current or split[1]

    # Back-compat: if only one period provided, try to auto-split a combined
    # "Metric,Previous,Current" CSV / JSON into both periods.
    if (not previous or not current) and (data.get("previous") or data.get("current")):
        combined = data.get("previous") or data.get("current")
        fmt = data.get("previous_format") or data.get("current_format") or ""
        split = _split_combined(combined, fmt)
        if split:
            if not previous:
                previous = split[0]
            if not current:
                current = split[1]

    if not previous and not current:
        return jsonify({"error": "Provide at least one period of metrics."}), 400
    if not previous or not current:
        return jsonify({
            "error": "Both previous and current periods are required. Upload a "
                     "combined file with Metric,Previous,Current columns, or "
                     "upload two files (one per period)."
        }), 400

    prev_label = _label(data, "previous_label", "Previous")
    curr_label = _label(data, "current_label", "Current")
    analysis = _build_full_analysis(previous, current, prev_label, curr_label,
                                    combined_payload)
    return jsonify(analysis)


@app.route("/api/insights", methods=["POST"])
def api_insights():
    """Single-report analysis: UX problems + UI revamp suggestions."""
    data = request.get_json(force=True, silent=True) or {}
    metrics = _parse_source(data.get("source"), data.get("format"))
    if not metrics:
        return jsonify({"error": "No metrics could be parsed. Paste metrics or upload a report."}), 400
    result = ai.analyze_single(metrics)
    result["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    return jsonify(result)


@app.route("/api/save", methods=["POST"])
def api_save():
    data = request.get_json(force=True, silent=True) or {}
    analysis = data.get("analysis")
    if not analysis:
        return jsonify({"error": "No analysis to save."}), 400
    rtype = data.get("type") or analysis.get("type") or (
        "single" if "health_label" in analysis else "compare"
    )
    title = (data.get("title") or "").strip() or (
        f"{analysis.get('previous_label', 'Previous')} vs "
        f"{analysis.get('current_label', 'Current')}"
    )
    # Normalize so history/report templates work for both report types.
    normalized = _normalize_analysis(analysis, rtype)
    report = store.save_report({
        "title": title,
        "type": rtype,
        "analysis": normalized,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    resp = jsonify(report)
    resp.set_cookie("latest_report_id", report["id"], max_age=60 * 60 * 24 * 30)
    return resp


def _normalize_analysis(analysis, rtype):
    """Fill in compare-style fields so a single analysis renders in history,
    the report view, and exports as if it were a one-period comparison."""
    a = dict(analysis)
    if rtype == "single":
        label = a.get("label") or "Current"
        a.setdefault("previous_label", "Baseline")
        a.setdefault("current_label", label)
        a.setdefault("health_delta", a.get("health", 50) - 50)
        a.setdefault("num_compared", len(a.get("metrics_status", [])))
        a.setdefault("num_improved", sum(1 for m in a.get("metrics_status", []) if m.get("status") in ("good", "ok")))
        a.setdefault("num_regressed", sum(1 for m in a.get("metrics_status", []) if m.get("status") == "poor"))
        a.setdefault("num_unchanged", 0)
        # Build a single-period metrics list so tables/export render.
        if not a.get("metrics"):
            status_rows = a.get("metrics_status", [])
            a["metrics"] = []
            for s in status_rows:
                a["metrics"].append({
                    "key": s.get("key"),
                    "label": s.get("label"),
                    "group": s.get("group", ""),
                    "kind": "count",
                    "unit": "",
                    "higher_better": s.get("higher_better", True),
                    "previous": None,
                    "current": s.get("value"),
                    "previous_display": "—",
                    "current_display": s.get("display"),
                    "change": None,
                    "change_display": "—",
                    "status": s.get("status"),
                })
        a.setdefault("questions", [])
        a.setdefault("next_steps", a.get("recommendations", [])[:3])
    return a


@app.route("/api/reports", methods=["GET"])
def api_reports():
    return jsonify(store.list_reports())


@app.route("/api/reports/<report_id>", methods=["DELETE"])
def api_delete_report(report_id):
    if store.delete_report(report_id):
        return jsonify({"ok": True})
    return jsonify({"error": "Not found"}), 404


@app.route("/api/reports/<report_id>/markdown", methods=["GET"])
def api_report_markdown(report_id):
    r = store.get_report(report_id)
    if not r:
        abort(404)
    analysis = _analysis_from_report(r)
    md = report.build_markdown(analysis, analysis.get("previous_label", "Previous"),
                               analysis.get("current_label", "Current"))
    return send_file(
        io.BytesIO(md.encode("utf-8")),
        mimetype="text/markdown",
        as_attachment=True,
        download_name=f"prodlysis-report-{report_id[:8]}.md",
    )


@app.route("/api/reports/<report_id>/pdf", methods=["GET"])
def api_report_pdf(report_id):
    r = store.get_report(report_id)
    if not r:
        abort(404)
    analysis = _analysis_from_report(r)
    pdf = report.build_pdf_bytes(analysis, analysis.get("previous_label", "Previous"),
                                 analysis.get("current_label", "Current"))
    return send_file(
        io.BytesIO(pdf),
        mimetype="application/pdf",
        as_attachment=True,
        download_name=f"prodlysis-report-{report_id[:8]}.pdf",
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=True)
