"""AI engine: deterministic rule-based analysis + optional LLM enrichment.

When an OpenAI or Anthropic API key is configured, the deterministic findings
are sent to the model to produce richer narratives. Otherwise the built-in
engine returns high-quality, structured insights that mirror the PRD examples.
"""

import json
import os
import ssl
import urllib.request


def _ssl_context():
    """Build an SSL context using certifi's CA bundle (fixes macOS Python
    'CERTIFICATE_VERIFY_FAILED' errors when calling the LLM APIs)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()

# ---------------------------------------------------------------------------
# Deterministic engine
# ---------------------------------------------------------------------------

# Cause hypotheses keyed by metric group
_CAUSES = {
    "Friction": (
        "Users likely encountered friction in a critical flow (e.g. checkout "
        "or onboarding) - broken interactive elements, unclear next steps, or "
        "a hard-to-find action button."
    ),
    "Engagement": (
        "Content or page layout likely changed how deeply users engage - "
        "above-the-fold content, load speed, or mobile spacing are common factors."
    ),
    "Conversion": (
        "The conversion path may have gained or lost clarity - CTA placement, "
        "form length, and trust signals are typical drivers."
    ),
    "Traffic": (
        "Changes in traffic volume may reflect marketing or onboarding shifts "
        "rather than UI changes alone."
    ),
}

# Recommendation templates per metric
_RECOMMENDATIONS = {
    "dropoffs": [
        "Review the flow where users drop off (checkout / onboarding) and simplify the steps.",
        "Move key information (e.g. shipping details) above the payment section.",
    ],
    "dropoff_rate": [
        "Reduce the number of steps required to complete the primary action.",
        "Add progress indicators so users understand how much is left.",
    ],
    "rage_clicks": [
        "Inspect rage-click hotspots and fix elements that look clickable but do nothing.",
        "Ensure loading states and buttons respond instantly to clicks.",
    ],
    "dead_clicks": [
        "Investigate dead-click zones - add clickable affordances or tooltips.",
        "Remove decorative elements that users mistakenly try to interact with.",
    ],
    "scroll_depth": [
        "Move the most important content above the fold.",
        "Improve content hierarchy and add visual anchors to encourage scrolling.",
    ],
    "session_duration": [
        "Increase content relevance with clearer navigation and internal links.",
        "Reduce page load time to keep users engaged longer.",
    ],
    "bounce_rate": [
        "Tighten the value proposition and headline on landing pages.",
        "Make the first screen instantly understandable to new visitors.",
    ],
    "conversion_rate": [
        "Increase CTA button visibility and contrast.",
        "Reduce form fields to the minimum needed.",
    ],
    "sessions": [
        "Drive more qualified traffic via improved SEO and onboarding.",
    ],
}


def analyze(comparison, previous=None, current=None):
    """Build the full structured analysis (findings, recommendations, summary).

    `comparison` is the output of compare.compare(). `previous`/`current` are
    the raw normalized dicts (used for context, currently optional).
    """
    metrics = comparison["metrics"]
    findings = _build_findings(comparison)
    recommendations = _build_recommendations(comparison)
    summary = _build_summary(comparison, findings, recommendations)
    return {
        "summary": summary,
        "findings": findings,
        "recommendations": recommendations,
        "next_steps": recommendations[:3],
        "questions": _answer_questions(comparison, findings, recommendations),
    }


def _pct(value):
    if value is None:
        return None
    return round(value, 1)


def _build_findings(comparison):
    findings = []
    metrics = comparison["metrics"]
    improved = [m for m in metrics if m["status"] == "improved"]
    regressed = [m for m in comparison["metrics"] if m["status"] == "regressed"]

    # 1) Biggest change (only when there is a real change)
    changed = [
        m for m in comparison["metrics"]
        if m["change"] is not None
        and m["status"] in ("improved", "regressed")
        and abs(m["change"]) >= 1.0
    ]
    if changed:
        biggest = max(changed, key=lambda m: abs(m["change"]))
        direction = "improved" if biggest["status"] == "improved" else "regressed"
        findings.append({
            "title": (
                f"{biggest['label']} {'improved' if direction=='improved' else 'regressed'} "
                f"by {abs(_pct(biggest['change']))}%"
            ),
            "evidence": [
                f"{biggest['label']}: {biggest['previous_display']} → {biggest['current_display']}",
                f"Change: {biggest['change_display']}",
            ],
            "cause": _CAUSES.get(biggest["group"], _CAUSES["Engagement"]),
            "confidence": _confidence(biggest, comparison),
            "severity": "positive" if direction == "improved" else "negative",
        })

    # 2) Friction cluster (rage + dead + dropoffs)
    friction = [
        m for m in metrics
        if m["group"] == "Friction" and m["status"] in ("improved", "regressed")
        and m["change"] is not None and abs(m["change"]) >= 1.0
    ]
    if friction:
        moved = [m for m in friction if m["status"] == "regressed"]
        if moved:
            findings.append({
                "title": "Friction cluster is trending worse",
                "evidence": [
                    f"{m['label']}: {m['change_display']} ({m['previous_display']} → {m['current_display']})"
                    for m in moved
                ],
                "cause": "Multiple friction signals moved together - the recent change may have introduced a usability problem.",
                "confidence": _confidence(moved[0], comparison, min(95, 80 + 5 * len(moved))),
                "severity": "negative",
            })
        else:
            names = ", ".join(m["label"] for m in friction[:3])
            findings.append({
                "title": "User friction is decreasing",
                "evidence": [
                    f"{m['label']}: {m['change_display']} ({m['previous_display']} → {m['current_display']})"
                    for m in friction[:3]
                ],
                "cause": "Recent UI changes appear to have reduced friction in the primary flow.",
                "confidence": _confidence(friction[0], comparison),
                "severity": "positive",
            })

    # 3) Regression detail (if any regressions beyond friction)
    other_regressed = [m for m in regressed if m not in friction]
    if other_regressed:
        findings.append({
            "title": f"{other_regressed[0]['label']} regressed",
            "evidence": [
                f"{other_regressed[0]['label']}: {other_regressed[0]['previous_display']} → {other_regressed[0]['current_display']}",
                f"Change: {other_regressed[0]['change_display']}",
            ],
            "cause": _CAUSES.get(other_regressed[0]["group"], _CAUSES["Engagement"]),
            "confidence": _confidence(other_regressed[0], comparison),
            "severity": "negative",
        })

    # 4) Engagement cluster
    engagement = [
        m for m in metrics
        if m["group"] == "Engagement" and m["status"] in ("improved", "regressed")
        and m["change"] is not None and abs(m["change"]) >= 1.0
    ]
    if engagement and all(m["status"] == "improved" for m in engagement):
        names = ", ".join(m["label"] for m in engagement[:3])
        findings.append({
            "title": "Engagement is up",
            "evidence": [
                f"{m['label']}: {m['change_display']} ({m['previous_display']} → {m['current_display']})"
                for m in engagement[:3]
            ],
            "cause": "Users are spending more time and engaging more deeply - the layout/content changes are working.",
            "confidence": _confidence(engagement[0], comparison),
            "severity": "positive",
        })

    # 5) Headline takeaway
    if comparison["num_regressed"] == 0 and comparison["num_improved"] > 0 and comparison["num_compared"] >= 3:
        findings.append({
            "title": "All tracked metrics moved in the right direction",
            "evidence": [
                f"{comparison['num_improved']} of {comparison['num_compared']} metrics improved.",
                "No metric regressed between the two periods.",
            ],
            "cause": "The latest product update appears to be a clear success.",
            "confidence": 90,
            "severity": "positive",
        })
    elif comparison["num_regressed"] > comparison["num_improved"]:
        findings.append({
            "title": "Overall UX is trending down",
            "evidence": [
                f"{comparison['num_regressed']} metric(s) regressed vs {comparison['num_improved']} improved.",
            ],
            "cause": "The recent changes may need to be rolled back or reworked.",
            "confidence": 88,
            "severity": "negative",
        })
    elif not findings and comparison["num_compared"] > 0:
        findings.append({
            "title": "No significant UX changes detected",
            "evidence": [
                "All compared metrics were effectively unchanged between the two periods.",
            ],
            "cause": "The product update appears to be neutral - no measurable UX impact either way.",
            "confidence": 80,
            "severity": "neutral",
        })

    # Sort: negative first (more urgent), then by |change|
    def sort_key(f):
        order = {"negative": 0, "positive": 1, "neutral": 2}
        return (order.get(f.get("severity"), 1),)
    findings.sort(key=sort_key)
    return findings


def _confidence(metric, comparison, base=None):
    """Heuristic confidence: more corroborating metrics -> higher confidence."""
    if base is None:
        base = 70
    boost = 0
    if metric["change"] is not None and abs(metric["change"]) > 30:
        boost += 10
    elif metric["change"] is not None and abs(metric["change"]) > 15:
        boost += 5
    if comparison["num_compared"] >= 5:
        boost += 5
    if comparison["num_improved"] + comparison["num_regressed"] >= 4:
        boost += 5
    return min(97, base + boost)


def _build_recommendations(comparison):
    """Build prioritized, actionable recommendations."""
    recs = []
    seen = set()

    def add(text, priority):
        if text not in seen:
            seen.add(text)
            recs.append({"text": text, "priority": priority})

    regressed = sorted(
        [m for m in comparison["metrics"] if m["status"] == "regressed"],
        key=lambda m: -(abs(m["change"]) if m["change"] else 0),
    )
    improved = [m for m in comparison["metrics"] if m["status"] == "improved"]

    # 1) Any regressions get top priority
    for m in regressed:
        for t in _RECOMMENDATIONS.get(m["key"], []):
            add(t, "High")

    # 2) If nothing regressed, praise what's working + address remaining risk
    if not regressed:
        add("Keep the current layout - the changes are working.", "Low")
        add("Monitor onboarding Step 4 for continued drop-offs.", "Medium")

    # 3) Balanced recommendations so we always return useful, non-generic items
    for key, texts in _RECOMMENDATIONS.items():
        present = any(m["key"] == key for m in improved)
        if present and not regressed:
            for t in texts[:1]:
                add(t, "Medium")

    # Fill with a couple of generally-valuable actions if we have too few
    if len(recs) < 3:
        extras = [
            "Increase CTA button visibility and contrast.",
            "Improve mobile spacing and touch targets.",
        ]
        for t in extras:
            add(t, "Medium")

    return recs[:6]


def _build_summary(comparison, findings, recommendations):
    """A short executive-style summary paragraph."""
    n_imp = comparison["num_improved"]
    n_reg = comparison["num_regressed"]
    n = comparison["num_compared"]
    if n == 0:
        return "No comparable metrics were provided. Add values for both periods to generate insights."

    if n_reg == 0 and n_imp >= 2:
        biggest = max(
            (m for m in comparison["metrics"] if m["change"] is not None),
            key=lambda m: abs(m["change"]), default=None,
        )
        parts = [f"Overall UX improved significantly."]
        if biggest and biggest["change"]:
            parts.append(
                f"{biggest['label']} changed by {biggest['change_display']} "
                f"({biggest['previous_display']} → {biggest['current_display']})."
            )
        parts.append(f"{n_imp} of {n} tracked metrics moved in a positive direction.")
        return " ".join(parts)

    if n_reg > n_imp:
        worst = max(
            (m for m in comparison["metrics"] if m["change"] is not None),
            key=lambda m: abs(m["change"]), default=None,
        )
        parts = ["Overall UX regressed."]
        if worst:
            parts.append(
                f"{worst['label']} worsened by {worst['change_display']} "
                f"({worst['previous_display']} → {worst['current_display']})."
            )
        parts.append(f"{n_reg} metric(s) regressed; prioritize the fixes above.")
        return " ".join(parts)

    mixed = [
        m for m in comparison["metrics"]
        if m["status"] in ("improved", "regressed") and m["change"] is not None
    ]
    mixed.sort(key=lambda m: -(abs(m["change"]) if m["change"] else 0))
    if mixed:
        top = mixed[0]
        trend = "improved" if top["status"] == "improved" else "regressed"
        return (
            f"Mixed results this period. The largest change was {top['label']} "
            f"{trend} by {top['change_display']}. Overall UX "
            f"improved in {n_imp} area(s) but regressed in {n_reg}."
        )
    return "No meaningful changes were detected between the two periods."


def _answer_questions(comparison, findings, recommendations):
    """Answers for the AI prompt logic section of the PRD."""
    metrics = [m for m in comparison["metrics"] if m["change"] is not None]
    if not metrics:
        return []
    biggest = max(metrics, key=lambda m: abs(m["change"]))
    worst = max(
        [m for m in metrics if m["status"] == "regressed"],
        key=lambda m: abs(m["change"]), default=None,
    )
    return [
        {
            "question": "What improved?",
            "answer": ", ".join(
                m["label"] for m in comparison["metrics"] if m["status"] == "improved"
            ) or "Nothing improved.",
        },
        {
            "question": "What became worse?",
            "answer": ", ".join(
                m["label"] for m in comparison["metrics"] if m["status"] == "regressed"
            ) or "Nothing regressed.",
        },
        {
            "question": "Which screen likely has UX problems?",
            "answer": (
                f"The flow tied to {worst['label']} - likely checkout or onboarding."
                if worst else "No screen stands out as problematic."
            ),
        },
        {
            "question": "Which metric changed the most?",
            "answer": f"{biggest['label']} ({biggest['change_display']}).",
        },
        {
            "question": "Did previous fixes work?",
            "answer": (
                "Yes - friction metrics improved, so recent changes appear effective."
                if comparison["num_regressed"] == 0
                else "Partially - some metrics improved but others still need attention."
            ),
        },
        {
            "question": "What should the PM prioritize next?",
            "answer": (
                recommendations[0]["text"]
                if recommendations
                else "Continue monitoring the primary funnel."
            ),
        },
    ]


# ---------------------------------------------------------------------------
# Single-report insights (no comparison needed)
# ---------------------------------------------------------------------------
# Health thresholds for absolute (single-period) scoring, keyed by metric.
# Each entry: (good_floor, ok_floor) where good = above good_floor for
# higher-better metrics, and BELOW the threshold for lower-better ones.
_GOOD_LIMITS = {
    "dropoff_rate": (20.0, 35.0),        # good < 20, ok < 35
    "bounce_rate": (30.0, 45.0),
    "rage_clicks": (50.0, 150.0),
    "dead_clicks": (50.0, 150.0),
    "scroll_depth": (60.0, 40.0),        # good > 60, ok > 40
    "session_duration": (90.0, 45.0),    # seconds
    "conversion_rate": (5.0, 2.0),
    "sessions": (None, None),            # informational only
}

_REVAMP_TIPS = {
    "Friction": [
        "Add a visible, high-contrast primary CTA above the fold.",
        "Reduce the number of steps in the checkout / signup flow.",
        "Move critical information (shipping, pricing) above the payment section.",
        "Make interactive elements obviously clickable with hover/active states.",
    ],
    "Engagement": [
        "Improve above-the-fold content so value is clear within 5 seconds.",
        "Add clear visual anchors (headers, cards, images) to encourage scrolling.",
        "Reduce page load time and image weight to keep users engaged.",
    ],
    "Conversion": [
        "Increase CTA button size, contrast, and whitespace around it.",
        "Cut form fields to the minimum needed to convert.",
        "Add trust signals (reviews, guarantees, security badges) near conversion points.",
    ],
    "Traffic": [
        "Improve SEO titles/meta and internal linking to grow qualified traffic.",
        "Strengthen onboarding so new visitors reach the core value faster.",
    ],
}


def analyze_single(metrics):
    """Analyze ONE report and return UX problems + UI revamp suggestions.

    `metrics` is a normalized {key: float} dict from one period.
    Returns: {health, health_label, findings, recommendations, summary,
    priority_metric}
    """
    from .metrics import METRIC_DEFS, format_value, METRIC_ORDER

    if not metrics:
        return {
            "health": 50, "health_label": "Neutral",
            "findings": [], "recommendations": [],
            "summary": "No metrics provided.",
            "priority_metric": None,
        }

    # Score each known metric against absolute thresholds.
    scored = []
    for key in METRIC_ORDER:
        if key not in metrics:
            continue
        defn = METRIC_DEFS.get(key)
        if not defn:
            continue
        value = metrics[key]
        limits = _GOOD_LIMITS.get(key)
        if not limits or limits[0] is None:
            scored.append({
                "key": key, "label": defn["label"], "value": value,
                "display": format_value(value, defn["kind"]),
                "status": "info", "group": defn["group"],
            })
            continue
        good, ok = limits
        higher = defn["higher_better"]
        if higher:
            status = "good" if value >= good else ("ok" if value >= ok else "poor")
        else:
            status = "good" if value <= good else ("ok" if value <= ok else "poor")
        scored.append({
            "key": key, "label": defn["label"], "value": value,
            "display": format_value(value, defn["kind"]),
            "status": status, "group": defn["group"], "higher_better": higher,
        })

    good_n = sum(1 for s in scored if s["status"] == "good")
    ok_n = sum(1 for s in scored if s["status"] == "ok")
    poor_n = sum(1 for s in scored if s["status"] == "poor")
    total = len([s for s in scored if s["status"] != "info"])

    # Health = weighted by status
    if total:
        health = round(50 + 50 * ((good_n - poor_n) / total))
        health = max(5, min(98, health))
    else:
        health = 50
    if health >= 70:
        health_label = "Good"
    elif health >= 45:
        health_label = "Fair"
    else:
        health_label = "Needs work"

    # Findings for poor/ok metrics
    findings = []
    problem = [s for s in scored if s["status"] == "poor"]
    for s in problem[:4]:
        cause = _CAUSES.get(s["group"], _CAUSES["Engagement"])
        findings.append({
            "title": f"{s['label']} is {'low' if s['higher_better'] else 'high'} ({s['display']})",
            "evidence": [
                f"{s['label']}: {s['display']} — outside the healthy range.",
                f"Area: {s['group']}",
            ],
            "cause": cause,
            "confidence": 85,
            "severity": "negative",
        })
    if not problem:
        ok_ones = [s for s in scored if s["status"] == "ok"]
        if ok_ones:
            findings.append({
                "title": "Some metrics are within a healthy range",
                "evidence": [
                    f"{s['label']}: {s['display']}" for s in ok_ones[:3]
                ],
                "cause": "These areas are performing acceptably but have room to improve.",
                "confidence": 80,
                "severity": "neutral",
            })
        else:
            findings.append({
                "title": "No critical UX issues detected",
                "evidence": ["All provided metrics are within healthy ranges."],
                "cause": "Keep monitoring and watch for regressions after changes.",
                "confidence": 88,
                "severity": "positive",
            })

    # UI revamp recommendations grouped by the most affected areas
    recommendations = []
    seen = set()
    groups = []
    for s in problem:
        if s["group"] not in groups:
            groups.append(s["group"])
    for s in problem:
        for tip in _REVAMP_TIPS.get(s["group"], []):
            if tip not in seen:
                seen.add(tip)
                recommendations.append({"text": tip, "priority": "High"})
    if not recommendations:
        for s in scored:
            if s["status"] == "ok":
                for tip in _REVAMP_TIPS.get(s["group"], []):
                    if tip not in seen:
                        seen.add(tip)
                        recommendations.append({"text": tip, "priority": "Medium"})
    if len(recommendations) < 3:
        extras = [
            "Increase CTA button visibility and contrast.",
            "Improve mobile spacing and touch targets.",
            "Reduce form fields to the minimum needed.",
        ]
        for t in extras:
            if t not in seen:
                seen.add(t)
                recommendations.append({"text": t, "priority": "Medium"})

    priority = problem[0] if problem else (scored[0] if scored else None)

    # Summary
    if poor_n == 0:
        summary = (
            f"Overall UX looks healthy ({health_label}, {health}/100). "
            f"{good_n} metric(s) are in a good range. Apply the polish suggestions "
            f"below to push engagement further, then run a comparison to verify impact."
        )
    elif poor_n >= 2:
        summary = (
            f"UX needs attention ({health_label}, {health}/100). "
            f"{poor_n} metric(s) are outside healthy ranges — most notably "
            f"{problem[0]['label']} ({problem[0]['display']}). Use the UI revamp "
            f"suggestions below, make changes, then compare this report with a new one."
        )
    else:
        label_lower = health_label.lower()
        summary = (
            f"Overall UX is {label_lower} ({health}/100). "
            f"{problem[0]['label']} ({problem[0]['display']}) is the main problem area. "
            f"Apply the recommended fixes, then run a comparison to measure the impact."
        )

    return {
        "health": health,
        "health_label": health_label,
        "findings": findings,
        "recommendations": recommendations,
        "summary": summary,
        "priority_metric": priority["label"] if priority else None,
        "metrics_status": scored,
    }


# ---------------------------------------------------------------------------
# Optional LLM enrichment
# ---------------------------------------------------------------------------

def _env(key):
    return os.environ.get(key, "").strip()


def llm_enrich(analysis, comparison):
    """If a provider+key is configured, replace narratives with model output.

    Returns the (possibly updated) analysis dict. Never raises: on any error
    the deterministic analysis is returned unchanged.
    """
    import logging
    log = logging.getLogger("prodlysis.ai")
    provider = (_env("AI_PROVIDER") or "none").lower()
    openai_key = _env("OPENAI_API_KEY")
    anthropic_key = _env("ANTHROPIC_API_KEY")
    deepseek_key = _env("DEEPSEEK_API_KEY")
    try:
        if provider == "openai" and openai_key:
            return _call_openai(analysis, comparison, openai_key)
        if provider == "anthropic" and anthropic_key:
            return _call_anthropic(analysis, comparison, anthropic_key)
        if provider == "deepseek" and deepseek_key:
            log.info("LLM: calling DeepSeek (key len %d)", len(deepseek_key))
            result = _call_deepseek(analysis, comparison, deepseek_key)
            log.info("LLM: DeepSeek succeeded")
            return result
    except Exception as e:
        log.warning("LLM: provider %s failed (%s: %s) — using deterministic engine",
                    provider, type(e).__name__, e)
    return analysis


def _build_prompt(analysis, comparison):
    metric_lines = []
    for m in comparison["metrics"]:
        metric_lines.append(
            f"- {m['label']}: {m['previous_display']} → {m['current_display']} "
            f"({m['change_display']}, {m['status']})"
        )
    health_line = ""
    if comparison.get("health") is not None:
        health_line = (
            f"\nOverall UX Health Score: {comparison['health']}/100 "
            f"(Δ {comparison.get('health_delta', 0):+d} points)"
        )
    prompt = f"""You are Prodlysis, an AI UX analyst for product and data teams. Compare these product analytics metrics between two periods.{health_line}

Metrics:
{chr(10).join(metric_lines)}

Return STRICT JSON with EXACTLY these keys:
- "summary": 1-2 concise sentences on overall UX trend (exec-friendly).
- "findings": array of {{"title": str, "evidence": [str], "cause": str, "confidence": 0-100}} — focus on the biggest and most important changes.
- "recommendations": array of {{"text": str, "priority": "High"|"Medium"|"Low"}} — prioritized, specific UI fixes.
- "per_metric": array of {{"metric": str, "change": str, "interpretation": str, "confidence": 0-100}} — one entry per metric explaining what the change means.
- "cross_metric": str — a short paragraph connecting signals across metrics (e.g. rage clicks + dead clicks + drop-offs pointing to one screen/flow).
- "ab_tests": array of {{"name": str, "hypothesis": str, "success_metric": str}} — 2-3 concrete experiment ideas to validate the top fixes.
- "business_impact": str — a concise estimate of how the changes may affect conversions, retention, or revenue.
- "stakeholder_summary": str — 1-2 sentences ready to paste into Slack or email.
- "risk_watchlist": array of {{"risk": str, "watch": str}} — 2-4 regressions/areas to monitor.

Be specific, concise, and actionable. No markdown code fences, no extra text."""
    return prompt


def _call_openai(analysis, comparison, key):
    body = {
        "model": "gpt-4o-mini",
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": _build_prompt(analysis, comparison)}],
    }
    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:
        data = json.loads(resp.read().decode())
    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return _merge(analysis, parsed)


def _call_deepseek(analysis, comparison, key):
    """DeepSeek exposes an OpenAI-compatible chat completions API.

    Defaults to the deepseek-chat (V3) model; override with
    DEEPSEEK_MODEL env var (e.g. deepseek-reasoner).
    """
    model = _env("DEEPSEEK_MODEL") or "deepseek-chat"
    body = {
        "model": model,
        "messages": [{"role": "user", "content": _build_prompt(analysis, comparison)}],
    }
    req = urllib.request.Request(
        "https://api.deepseek.com/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:
        data = json.loads(resp.read().decode())
    content = data["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return _merge(analysis, parsed)


def _call_anthropic(analysis, comparison, key):
    body = {
        "model": "claude-3-5-sonnet-latest",
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": _build_prompt(analysis, comparison)}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=json.dumps(body).encode(),
        headers={
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=30, context=_ssl_context()) as resp:
        data = json.loads(resp.read().decode())
    content = data["content"][0]["text"]
    # strip code fences if present
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    parsed = json.loads(content)
    return _merge(analysis, parsed)


def _merge(analysis, parsed):
    out = dict(analysis)
    if isinstance(parsed.get("summary"), str) and parsed["summary"].strip():
        out["summary"] = parsed["summary"].strip()
    if isinstance(parsed.get("findings"), list) and parsed["findings"]:
        out["findings"] = parsed["findings"]
    if isinstance(parsed.get("recommendations"), list) and parsed["recommendations"]:
        out["recommendations"] = parsed["recommendations"]
        out["next_steps"] = parsed["recommendations"][:3]
    # New comprehensive sections (optional; fall back to deterministic content)
    for key in ("per_metric", "ab_tests", "risk_watchlist"):
        if isinstance(parsed.get(key), list) and parsed[key]:
            out[key] = parsed[key]
    for key in ("cross_metric", "business_impact", "stakeholder_summary"):
        if isinstance(parsed.get(key), str) and parsed[key].strip():
            out[key] = parsed[key].strip()
    return out
