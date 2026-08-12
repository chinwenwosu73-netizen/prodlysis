# Prodlysis

**AI-Powered UX Analysis & Product Insights for data teams**

Prodlysis compares two periods of UX metrics from any analytics platform (CSV, JSON, or pasted text), automatically:
- Detects UX issues
- Compares previous vs current performance
- Calculates percentage changes (improvements / regressions)
- Explains likely causes with confidence scores
- Suggests prioritized UI improvements
- Generates an executive-ready report (PDF / Markdown / clipboard)
- Computes an overall UX Health Score

Built with Python (Flask) per the PRD's FastAPI suggestion — the architecture is
a pure-Python pipeline, so it runs anywhere Python 3.10+ runs.

---

## Quick start

```bash
# one-click launcher (creates venv, installs deps, starts server)
./run.sh
```

Or manually:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Then open **http://127.0.0.1:5000**.

---

## How it works

1. Open **Compare** and either upload two CSV/JSON reports or paste metrics.
2. Press **Analyze Reports** — Prodlysis parses, compares, and explains.
3. Review the **UX Health score**, metric table, AI findings, and recommendations.
4. **Save to History**, then export as **PDF**, **Markdown**, or **Copy** to clipboard.

### Supported metrics
Sessions, Drop-offs, Drop-off Rate, Rage Clicks, Dead Clicks, Scroll Depth,
Session Duration (e.g. `1m 10s`), Bounce Rate, Conversion Rate.

### Input formats
- **Manual paste**: `Drop-off Rate: 42%` or `Rage Clicks = 118` (one per line)
- **CSV**: header row `Metric,Previous,Current` (or two columns `Metric,Value`)
- **JSON**: flat object `{"Sessions": 1200, ...}` or array of
  `[{"metric": "...", "value": 100}, ...]`

---

## AI engine

Three modes:

- **Built-in deterministic engine** (no key required) — structured findings,
  likely causes, confidence scores, and recommendations.
- **LLM-enriched** — set `AI_PROVIDER` + an API key in the `.env` file (in the
  product folder) to have OpenAI (`gpt-4o-mini`), Anthropic Claude, or
  **DeepSeek** generate the narrative.

API keys are **never** entered in the app's Settings page — they live only in
`.env` inside the product folder (gitignored).

```bash
cp .env.example .env
# edit .env, e.g. DeepSeek (OpenAI-compatible):
#   AI_PROVIDER=deepseek
#   DEEPSEEK_API_KEY=sk-...
#   DEEPSEEK_MODEL=deepseek-chat     # or deepseek-reasoner
```

---

## Project structure

```
app.py                  Flask app + routes + JSON API
prodlysis/
  metrics.py            metric catalog, parsing, formatting
  parsers.py            CSV / JSON / manual-paste parsers
  compare.py            comparison engine + health score
  ai.py                 deterministic analysis + optional LLM enrichment
  store.py              JSON-file report history + settings
  report.py             Markdown + PDF report generation
templates/              Jinja2 pages (dashboard, compare, history, report, settings)
static/                 CSS + JS
tests/smoke_test.py     end-to-end pipeline test
data/                   saved reports + settings (gitignored)
```

---

## Tests

```bash
.venv/bin/python tests/smoke_test.py
```

---

## Roadmap (from PRD)

- **Phase 2**: Direct Microsoft Clarity integration, heatmap interpretation,
  funnel analysis, session recording summaries, weekly email reports.
- **Phase 3**: Google Analytics / Mixpanel / Hotjar / PostHog integrations,
  Jira/Linear ticket generation, Slack notifications.
