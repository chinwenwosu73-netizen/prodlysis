"""Prodlysis smoke test — validates the core pipeline end to end.

Run:  .venv/bin/python tests/smoke_test.py
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prodlysis import ai, compare, parsers, report  # noqa: E402


def test_manual_parse():
    text = """Drop-off Rate: 42%
Rage Clicks: 118
Dead Clicks: 91
Scroll Depth: 46%
Session Duration: 1m 10s
Sessions = 2400
Bounce Rate: 38%
Conversion Rate: 3.2%"""
    pairs = parsers.parse_manual(text)
    d = parsers.to_metric_dict(pairs)
    assert d.get("dropoff_rate") == 42.0, d
    assert d.get("rage_clicks") == 118.0
    assert d.get("scroll_depth") == 46.0
    assert d.get("session_duration") == 70.0
    assert d.get("sessions") == 2400.0
    print("  manual parse: OK")


def test_csv_parse():
    text = """Metric,Previous,Current
Drop-off Rate,42%,27%
Rage Clicks,118,64
Dead Clicks,91,52
Scroll Depth,46%,68%
Session Duration,1m 10s,2m 01s"""
    pairs = parsers.parse_csv(text)
    d = parsers.to_metric_dict(pairs)
    assert d.get("dropoff_rate") == 42.0
    assert d.get("session_duration") == 70.0
    print("  csv parse: OK")


def test_json_parse():
    text = json.dumps({
        "Sessions": 2400, "Drop-off Rate": 42, "Rage Clicks": 118,
        "Dead Clicks": 91, "Scroll Depth": 46, "Session Duration": "1m 10s",
    })
    pairs = parsers.parse_json(text)
    d = parsers.to_metric_dict(pairs)
    assert d.get("rage_clicks") == 118.0
    assert d.get("session_duration") == 70.0
    print("  json parse: OK")


def test_compare_and_ai():
    prev = {
        "dropoff_rate": 42.0, "rage_clicks": 118.0, "dead_clicks": 91.0,
        "scroll_depth": 46.0, "session_duration": 70.0, "sessions": 2400.0,
        "bounce_rate": 38.0, "conversion_rate": 3.2,
    }
    curr = {
        "dropoff_rate": 27.0, "rage_clicks": 64.0, "dead_clicks": 52.0,
        "scroll_depth": 68.0, "session_duration": 121.0, "sessions": 2650.0,
        "bounce_rate": 31.0, "conversion_rate": 4.1,
    }
    c = compare.compare(prev, curr)
    assert c["num_compared"] == 8
    assert c["num_regressed"] == 0
    assert 50 < c["health"] <= 98, c["health"]
    assert c["health_delta"] > 0

    a = ai.analyze(c)
    assert a["summary"]
    assert a["findings"]
    assert a["recommendations"]
    assert len(a["questions"]) == 6

    # Drop-off change should be -35.7%
    dropoff = next(m for m in c["metrics"] if m["key"] == "dropoff_rate")
    assert abs(dropoff["change"] - -35.7) < 0.2, dropoff["change"]
    assert dropoff["status"] == "improved"

    print(f"  compare: OK (health={c['health']}, delta={c['health_delta']:+.0f})")
    print(f"  summary: {a['summary']}")
    print(f"  recommendations: {len(a['recommendations'])}")
    return a


def test_single_insights():
    metrics = {
        "dropoff_rate": 42.0, "rage_clicks": 118.0, "dead_clicks": 91.0,
        "scroll_depth": 46.0, "session_duration": 70.0, "sessions": 2400.0,
        "bounce_rate": 38.0, "conversion_rate": 3.2,
    }
    r = ai.analyze_single(metrics)
    assert 0 <= r["health"] <= 100
    assert r["summary"]
    assert r["findings"]
    assert r["recommendations"]
    assert r["priority_metric"]
    print(f"  single insights: OK (health={r['health']}, "
          f"findings={len(r['findings'])}, recs={len(r['recommendations'])})")


def test_report_generation(a):
    md = report.build_markdown(a, "June", "July")
    assert "# Prodlysis" in md
    assert "## Executive Summary" in md
    pdf = report.build_pdf_bytes(a, "June", "July")
    assert pdf[:5] == b"%PDF-"
    print("  markdown + pdf: OK")


if __name__ == "__main__":
    print("Prodlysis smoke test")
    test_manual_parse()
    test_csv_parse()
    test_json_parse()
    a = test_compare_and_ai()
    test_single_insights()
    test_report_generation(a)
    print("\nAll tests passed ✓")
