"""Persistent report store (report history) using a local JSON file.

Data is stored under <project>/data/reports.json. This keeps the MVP
dependency-free; swapping to PostgreSQL later only touches this module.

On serverless platforms (e.g. Vercel) the filesystem is read-only outside
/tmp, so we gracefully fall back to a temp directory for the session.
"""

import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _data_dir():
    """Return a writable data dir: project/data, else a temp dir (Vercel)."""
    candidate = os.path.join(_BASE, "data")
    try:
        os.makedirs(candidate, exist_ok=True)
        test = os.path.join(candidate, ".write_test")
        with open(test, "w") as f:
            f.write("ok")
        os.remove(test)
        return candidate
    except OSError:
        return tempfile.mkdtemp(prefix="prodlysis-data-")


DATA_DIR = _data_dir()
REPORTS_FILE = os.path.join(DATA_DIR, "reports.json")
PROFILE_FILE = os.path.join(DATA_DIR, "profile.json")
_lock = threading.Lock()

DEFAULT_PROFILE = {
    "name": "",
    "email": "",
    "job_title": "",
    "avatar": "",            # data URI (base64), kept small via client resize
    "preferences": {
        "theme": "light",          # light | dark
        "language": "en",          # en | es | fr | de | pt
        "default_export": "markdown",  # markdown | pdf
        "notifications": True,     # weekly email summary (future)
    },
    "password_hash": "",
    "created_at": None,
    "updated_at": None,
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _load():
    if not os.path.exists(REPORTS_FILE):
        return []
    try:
        with open(REPORTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save(reports):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(REPORTS_FILE, "w", encoding="utf-8") as f:
        json.dump(reports, f, ensure_ascii=False, indent=2)


def list_reports():
    with _lock:
        reports = _load()
    reports.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return reports


def get_report(report_id):
    with _lock:
        reports = _load()
    for r in reports:
        if r.get("id") == report_id:
            return r
    return None


def save_report(report):
    """Insert a report; returns the report with id/created_at filled."""
    with _lock:
        reports = _load()
        report = dict(report)
        report["id"] = str(uuid.uuid4())
        report["created_at"] = report.get("created_at") or _now()
        reports.append(report)
        _save(reports)
    return report


def delete_report(report_id):
    with _lock:
        reports = _load()
        new = [r for r in reports if r.get("id") != report_id]
        removed = len(reports) != len(new)
        if removed:
            _save(new)
    return removed


# ---------------------------------------------------------------------------
# User profile (single local user for now — no Google auth yet)
# ---------------------------------------------------------------------------
UPLOADS_DIR = os.path.join(DATA_DIR, "uploads")


def load_profile():
    with _lock:
        data = _load_json(PROFILE_FILE, {})
    profile = dict(DEFAULT_PROFILE)
    profile.update(data)
    profile["preferences"] = {**DEFAULT_PROFILE["preferences"],
                              **(profile.get("preferences") or {})}
    return profile


def save_profile(profile):
    with _lock:
        profile = dict(profile)
        profile["updated_at"] = _now()
        if not profile.get("created_at"):
            profile["created_at"] = _now()
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(PROFILE_FILE, "w", encoding="utf-8") as f:
            json.dump(profile, f, ensure_ascii=False, indent=2)
    return profile


def _load_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return default


def clear_all_data():
    """Wipe reports + profile (danger zone)."""
    with _lock:
        for f in (REPORTS_FILE, PROFILE_FILE):
            if os.path.exists(f):
                try:
                    os.remove(f)
                except OSError:
                    pass
    return True
