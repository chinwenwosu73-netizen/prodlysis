/* ============================================================
   Prodlysis frontend logic
   ============================================================ */

function showToast(msg, isError) {
  var t = document.getElementById('toast');
  if (!t) return;
  t.textContent = msg;
  t.classList.toggle('error', !!isError);
  t.hidden = false;
  clearTimeout(t._timer);
  t._timer = setTimeout(function () { t.hidden = true; }, 2600);
}

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

/* ---------- File dropzone handling ---------- */
function initDropzone(id, inputId, fileNameSelector) {
  var dz = document.getElementById(id);
  var input = document.getElementById(inputId);
  // fileNameSelector is a CSS selector (e.g. '#dz-prev .dz-file')
  var fileName = document.querySelector(fileNameSelector);
  if (!dz || !input) return;

  // The dropzone is a <label for="..."> so clicking it natively opens the
  // file picker — no JS click handler needed (and none to interfere).
  // We only wire drag-and-drop and the filename feedback.
  dz.addEventListener('dragover', function (e) { e.preventDefault(); dz.classList.add('drag'); });
  dz.addEventListener('dragleave', function () { dz.classList.remove('drag'); });
  dz.addEventListener('drop', function (e) {
    e.preventDefault();
    dz.classList.remove('drag');
    if (e.dataTransfer.files.length) {
      input.files = e.dataTransfer.files;
      onFileSelected(input, fileName);
    }
  });
  input.addEventListener('change', function () {
    onFileSelected(input, fileName);
    clearResults();
  });
}

function onFileSelected(input, fileNameEl) {
  var f = input.files && input.files[0];
  if (!f) return;
  if (fileNameEl) {
    fileNameEl.innerHTML = '✓ ' + escapeHtml(f.name) +
      ' <button type="button" class="dz-remove" title="Remove attachment" aria-label="Remove attachment">✕</button>';
    fileNameEl.classList.remove('hidden');
    var rm = fileNameEl.querySelector('.dz-remove');
    if (rm) rm.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      input.value = '';
      fileNameEl.innerHTML = '';
      fileNameEl.classList.add('hidden');
      clearResults();
    });
  }
}

function readFileText(input) {
  return new Promise(function (resolve, reject) {
    var f = input.files && input.files[0];
    if (!f) { resolve(null); return; }
    var isPdf = /\.pdf$/i.test(f.name) || (f.type && f.type === 'application/pdf');
    if (isPdf) {
      // Read PDFs as base64 so the server can decode + extract their text.
      var fr = new FileReader();
      fr.onload = function () { resolve({ text: fr.result, is_pdf: true }); };
      fr.onerror = function () { reject(fr.error); };
      fr.readAsDataURL(f);
      return;
    }
    var reader = new FileReader();
    reader.onload = function () { resolve(reader.result); };
    reader.onerror = function () { reject(reader.error); };
    reader.readAsText(f);
  });
}

/* ---------- Results reset ---------- */
// Hides the results panel so no metrics comparison or insights are shown
// until a document has been uploaded AND an analysis has been run.
// Called whenever the inputs (files or pasted text) change or are cleared.
function clearResults() {
  var results = document.getElementById('results');
  var resultsCard = document.getElementById('results-card');
  if (results) results.innerHTML = '';
  if (resultsCard) resultsCard.hidden = true;
  var actions = document.getElementById('report-actions');
  if (actions) actions.classList.add('hidden');
  window.__analysis = null;
}

/* ---------- File dropzone handling ---------- */
function renderFindings(findings) {
  return (findings || []).map(function (f) {
    var icon = f.severity === 'positive' ? '✓' : (f.severity === 'neutral' ? '·' : '!');
    var evidence = (f.evidence || []).map(function (e) { return '<li>' + escapeHtml(e) + '</li>'; }).join('');
    var confidence = f.confidence != null ? '<span class="confidence">' + f.confidence + '% confidence</span>' : '';
    var cause = f.cause ? '<div class="cause"><b>Likely cause:</b> ' + escapeHtml(f.cause) + '</div>' : '';
    return '<div class="finding">' +
      '<div class="f-title"><span class="badge ' + escapeHtml(f.severity) + '">' + icon + '</span>' +
      escapeHtml(f.title) + confidence + '</div>' +
      '<ul>' + evidence + '</ul>' + cause +
      '</div>';
  }).join('') || '<div class="empty"><p>No findings generated.</p></div>';
}

function renderRecs(recs) {
  return (recs || []).map(function (r) {
    return '<li><span class="rec-check">✓</span><div>' +
      '<span class="badge ' + escapeHtml(r.priority) + '">' + escapeHtml(r.priority) + '</span>' +
      '<div style="margin-top:2px">' + escapeHtml(r.text) + '</div></div></li>';
  }).join('') || '<li>No recommendations.</li>';
}

function healthRing(health) {
  return '<div class="health-ring" style="background:conic-gradient(var(--pink) ' + health + '%, var(--border) 0)">' +
    '<div class="ring-inner"><div class="stat score">' + health + '</div><div class="max">/ 100</div></div></div>';
}

/* ---------- DeepSeek comprehensive sections ---------- */
function renderComprehensive(a) {
  var html = '';
  if (a.per_metric && a.per_metric.length) {
    var rows = a.per_metric.map(function (m) {
      var conf = m.confidence != null ? ' <span class="confidence">' + m.confidence + '%</span>' : '';
      return '<div class="finding">' +
        '<div class="f-title">' + escapeHtml(m.metric || '') + conf + '</div>' +
        '<div class="muted" style="font-size:13px">' + escapeHtml(m.change || '') + '</div>' +
        '<p style="margin:6px 0 0;font-size:14px">' + escapeHtml(m.interpretation || '') + '</p>' +
        '</div>';
    }).join('');
    html += '<h3 style="margin:22px 0 10px">🔍 Per-Metric Deep Dive</h3>' +
      '<div class="card" style="background:var(--bg);padding:16px 20px">' + rows + '</div>';
  }
  if (a.cross_metric) {
    html += '<h3 style="margin:22px 0 10px">🔗 Cross-Metric Analysis</h3>' +
      '<div class="card" style="background:var(--bg);padding:16px 20px"><p style="margin:0">' +
      escapeHtml(a.cross_metric) + '</p></div>';
  }
  if (a.business_impact) {
    html += '<h3 style="margin:22px 0 10px">📈 Business Impact</h3>' +
      '<div class="card" style="background:var(--bg);padding:16px 20px"><p style="margin:0">' +
      escapeHtml(a.business_impact) + '</p></div>';
  }
  if (a.ab_tests && a.ab_tests.length) {
    var tests = a.ab_tests.map(function (t) {
      return '<div class="finding"><div class="f-title">🧪 ' + escapeHtml(t.name || '') + '</div>' +
        '<div class="muted" style="font-size:13px">Hypothesis: ' + escapeHtml(t.hypothesis || '') + '</div>' +
        '<div class="muted" style="font-size:13px">Success metric: <b>' + escapeHtml(t.success_metric || '') + '</b></div></div>';
    }).join('');
    html += '<h3 style="margin:22px 0 10px">🧪 A/B Test Suggestions</h3>' +
      '<div class="card" style="background:var(--bg);padding:16px 20px">' + tests + '</div>';
  }
  if (a.risk_watchlist && a.risk_watchlist.length) {
    var risks = a.risk_watchlist.map(function (r) {
      return '<li><span class="rec-check" style="background:rgba(245,158,11,.15);color:var(--warning)">!</span><div>' +
        '<b>' + escapeHtml(r.risk || '') + '</b>' +
        '<div class="muted" style="font-size:13px;margin-top:2px">Watch: ' + escapeHtml(r.watch || '') + '</div></div></li>';
    }).join('');
    html += '<h3 style="margin:22px 0 10px">⚠️ Risk &amp; Watch-List</h3>' +
      '<div class="card" style="background:var(--bg);padding:16px 20px"><ul class="rec-list">' + risks + '</ul></div>';
  }
  return html;
}

/* ---------- User drop-off analysis rendering ---------- */
function renderDropoffs(a) {
  var d = a.dropoff_analysis;
  if (!d || !d.dropoff_points || !d.dropoff_points.length) return '';

  var rows = d.dropoff_points.map(function (p) {
    return '<tr>' +
      '<td>' + escapeHtml(p.label) + '</td>' +
      '<td class="stat">' + escapeHtml(p.display) + '</td>' +
      '<td><span class="badge ' + escapeHtml(p.status) + '">' + escapeHtml(p.status) + '</span></td>' +
      '<td class="muted" style="font-size:13px">' + escapeHtml(p.point) + '</td>' +
      '</tr>';
  }).join('');

  var causes = (d.causes || []).map(function (c) {
    return '<li><span class="rec-check" style="background:rgba(239,68,68,.12);color:var(--danger)">!</span><div>' + escapeHtml(c) + '</div></li>';
  }).join('');

  var recs = (d.recommendations || []).map(function (r) {
    return '<li><span class="rec-check">✓</span><div><span class="badge ' + escapeHtml(r.priority) + '">' + escapeHtml(r.priority) + '</span>' +
      '<div style="margin-top:2px">' + escapeHtml(r.text) + '</div></div></li>';
  }).join('');

  return '<h3 style="margin:22px 0 10px">🧭 User Drop-off Analysis</h3>' +
    '<div class="card" style="background:var(--bg);padding:16px 20px">' +
      '<p style="margin:0 0 10px"><b>Worst drop-off point:</b> ' + escapeHtml(d.worst || '—') + '</p>' +
      '<p style="margin:0 0 12px">' + escapeHtml(d.summary) + '</p>' +
      '<div class="table-wrap"><table class="metrics">' +
        '<thead><tr><th>Drop-off Point</th><th>Value</th><th>Status</th><th>Where it happens</th></tr></thead>' +
        '<tbody>' + rows + '</tbody>' +
      '</table></div>' +
      (causes ? '<h4 style="margin:16px 0 8px">Likely causes</h4><ul class="rec-list">' + causes + '</ul>' : '') +
      (recs ? '<h4 style="margin:16px 0 8px">Fix these to reduce drop-off</h4><ul class="rec-list">' + recs + '</ul>' : '') +
    '</div>';
}

/* ---------- Compare results rendering ---------- */
function renderAnalysis(a) {
  var el = document.getElementById('results');
  if (!el) return;

  var metricRows = (a.metrics || []).map(function (m) {
    var change = m.change == null ? '—' : (m.change >= 0 ? '+' : '') + m.change.toFixed(1) + '%';
    return '<tr>' +
      '<td>' + escapeHtml(m.label) + '</td>' +
      '<td class="stat">' + escapeHtml(m.previous_display) + '</td>' +
      '<td class="stat">' + escapeHtml(m.current_display) + '</td>' +
      '<td class="stat">' + escapeHtml(change) + '</td>' +
      '<td><span class="badge ' + escapeHtml(m.status) + '">' + escapeHtml(m.status) + '</span></td>' +
      '</tr>';
  }).join('');

  var deltaCls = a.health_delta >= 0 ? 'up' : 'down';
  var deltaTxt = (a.health_delta >= 0 ? '↑ +' : '↓ ') + Math.abs(a.health_delta) + ' points';

  el.innerHTML =
    '<div class="grid grid-2">' +
      '<div class="health-card" style="background:var(--bg);border:1px solid var(--border);border-radius:16px">' +
        '<h3>UX Health</h3>' + healthRing(a.health) +
        '<div class="health-delta ' + deltaCls + '">' + deltaTxt + '</div>' +
        '<div class="muted mt">' + a.num_compared + ' metrics compared · ' +
          '<b>' + a.num_improved + '</b> improved · <b>' + a.num_regressed + '</b> regressed</div>' +
      '</div>' +
      '<div>' +
        '<div class="finding" style="border:none">' +
          '<div class="f-title">✨ Executive Summary</div>' +
          '<p style="margin:6px 0 0">' + escapeHtml(a.summary) + '</p>' +
        '</div>' +
        '<h3 style="margin:16px 0 8px">Most Changed</h3>' +
        '<p class="muted" style="margin:0">' + (a.most_changed ? escapeHtml(a.most_changed) : '—') + '</p>' +
      '</div>' +
    '</div>' +

    '<h3 style="margin:22px 0 10px">Metrics Comparison</h3>' +
    '<div class="table-wrap"><table class="metrics">' +
      '<thead><tr><th>Metric</th><th>' + escapeHtml(a.previous_label) + '</th><th>' + escapeHtml(a.current_label) + '</th><th>Change</th><th>Status</th></tr></thead>' +
      '<tbody>' + metricRows + '</tbody>' +
    '</table></div>' +

    '<h3 style="margin:22px 0 10px">AI Findings</h3>' +
    '<div class="card" style="background:var(--bg);padding:16px 20px">' + renderFindings(a.findings) + '</div>' +

    '<h3 style="margin:22px 0 10px">Recommendations</h3>' +
    '<div class="card" style="background:var(--bg);padding:16px 20px"><ul class="rec-list">' + renderRecs(a.recommendations) + '</ul></div>' +

    renderDropoffs(a) +

    renderComprehensive(a);

  window.__analysis = a;
  window.__analysisType = 'compare';

  var resultsCard = document.getElementById('results-card');
  if (resultsCard) resultsCard.hidden = false;
  var actions = document.getElementById('report-actions');
  if (actions) actions.classList.remove('hidden');
}

/* ---------- Single-report insights rendering ---------- */
function renderInsights(a) {
  var el = document.getElementById('results');
  if (!el) return;

  // Metric status chips
  var statusRows = (a.metrics_status || []).map(function (m) {
    return '<tr>' +
      '<td>' + escapeHtml(m.label) + '</td>' +
      '<td class="stat">' + escapeHtml(m.display) + '</td>' +
      '<td><span class="badge ' + escapeHtml(m.status) + '">' + escapeHtml(m.status) + '</span></td>' +
      '</tr>';
  }).join('');

  var label = document.getElementById('single-label').value || 'Current';

  el.innerHTML =
    '<div class="grid grid-2">' +
      '<div class="health-card" style="background:var(--bg);border:1px solid var(--border);border-radius:16px">' +
        '<h3>UX Health</h3>' + healthRing(a.health) +
        '<div class="health-delta ' + (a.health >= 50 ? 'up' : 'down') + '">' + escapeHtml(a.health_label) + '</div>' +
        '<div class="muted mt">Single report · ' + escapeHtml(label) + '</div>' +
      '</div>' +
      '<div>' +
        '<div class="finding" style="border:none">' +
          '<div class="f-title">✨ AI Summary</div>' +
          '<p style="margin:6px 0 0">' + escapeHtml(a.summary) + '</p>' +
        '</div>' +
        (a.priority_metric ? '<h3 style="margin:16px 0 8px">🎯 Priority area</h3>' +
          '<p class="muted" style="margin:0">Focus on <b>' + escapeHtml(a.priority_metric) + '</b> first.</p>' : '') +
      '</div>' +
    '</div>' +

    '<h3 style="margin:22px 0 10px">Metric Health</h3>' +
    '<div class="table-wrap"><table class="metrics">' +
      '<thead><tr><th>Metric</th><th>Value</th><th>Status</th></tr></thead>' +
      '<tbody>' + statusRows + '</tbody>' +
    '</table></div>' +

    '<h3 style="margin:22px 0 10px">What\'s Wrong</h3>' +
    '<div class="card" style="background:var(--bg);padding:16px 20px">' + renderFindings(a.findings) + '</div>' +

    '<h3 style="margin:22px 0 10px">UI Revamp Suggestions</h3>' +
    '<div class="card" style="background:var(--bg);padding:16px 20px"><ul class="rec-list">' + renderRecs(a.recommendations) + '</ul></div>' +

    '<div class="card mt" style="background:var(--pink-soft);border-style:dashed">' +
      '<div class="row" style="justify-content:space-between">' +
        '<div><b>Made the changes?</b><div class="muted" style="font-size:13px">Compare this report with a new one to measure the impact.</div></div>' +
        '<button class="btn btn-primary btn-sm" id="goto-compare">Compare Reports →</button>' +
      '</div>' +
    '</div>';

  window.__analysis = a;
  window.__analysisType = 'single';

  var resultsCard = document.getElementById('results-card');
  if (resultsCard) resultsCard.hidden = false;
  var actions = document.getElementById('report-actions');
  if (actions) actions.classList.remove('hidden');

  var gc = document.getElementById('goto-compare');
  if (gc) gc.addEventListener('click', function () { switchMode('compare'); });
}

/* ---------- Analyze flow ---------- */
function buildPayload() {
  return {
    previous_label: document.getElementById('prev-label').value || 'Previous',
    current_label: document.getElementById('curr-label').value || 'Current',
    previous_format: 'auto',
    current_format: 'auto',
    previous: { text: document.getElementById('manual-prev').value },
    current: { text: document.getElementById('manual-curr').value },
  };
}

/* Which input method is active for a mode: 'upload' or 'manual' */
function activeMethod(mode) {
  var btn = document.querySelector('.im-btn.active[data-for="' + mode + '"]');
  return btn ? btn.getAttribute('data-method') : 'upload';
}

function runAnalysis() {
  var btn = document.getElementById('analyze-btn');
  if (!btn) return;
  btn.disabled = true;
  var orig = btn.innerHTML;
  btn.innerHTML = '<span class="loading"></span> Analyzing...';

  var payload = buildPayload();
  var filePrev = document.getElementById('file-prev');
  var fileCurr = document.getElementById('file-curr');
  var method = activeMethod('compare');

  if (method === 'manual') {
    // Only the manually-entered metrics are used.
    payload.previous = { text: document.getElementById('manual-prev').value };
    payload.current = { text: document.getElementById('manual-curr').value };
    return submitAnalysis(payload, btn, orig, 'compare');
  }

  // Upload mode: previous and current are uploaded as two separate reports.
  Promise.all([readFileText(filePrev), readFileText(fileCurr)])
    .then(function (texts) {
      if (texts[0] != null) {
        payload.previous = (texts[0].is_pdf) ? texts[0] : { text: texts[0] };
      }
      if (texts[1] != null) {
        payload.current = (texts[1].is_pdf) ? texts[1] : { text: texts[1] };
      }
      return fetch('/api/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    })
    .then(function (res) { return res.json().then(function (d) { return { ok: res.ok, data: d }; }); })
    .then(function (r) {
      if (!r.ok) throw new Error(r.data.error || 'Analysis failed');
      renderAnalysis(r.data);
      autoSaveToHistory(r.data, 'compare');
      showToast('Analysis complete');
    })
    .catch(function (err) {
      showToast(err.message || 'Failed to analyze', true);
    })
    .finally(function () {
      btn.disabled = false;
      btn.innerHTML = orig;
    });
}

/* Shared POST + render for the compare analysis (used by manual mode) */
function submitAnalysis(payload, btn, orig, mode) {
  return fetch('/api/analyze', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
    .then(function (res) { return res.json().then(function (d) { return { ok: res.ok, data: d }; }); })
    .then(function (r) {
      if (!r.ok) throw new Error(r.data.error || 'Analysis failed');
      renderAnalysis(r.data);
      autoSaveToHistory(r.data, 'compare');
      showToast('Analysis complete');
    })
    .catch(function (err) {
      showToast(err.message || 'Failed to analyze', true);
    })
    .finally(function () {
      btn.disabled = false;
      btn.innerHTML = orig;
    });
}

/* ---------- Single-report insights flow ---------- */
function runInsights() {
  var btn = document.getElementById('insights-btn');
  if (!btn) return;
  btn.disabled = true;
  var orig = btn.innerHTML;
  btn.innerHTML = '<span class="loading"></span> Analyzing...';

  var method = activeMethod('single');
  var fileSingle = document.getElementById('file-single');

  if (method === 'manual') {
    // Only the manually-entered metrics are used.
    var payload = {
      format: 'auto',
      source: { text: document.getElementById('manual-single').value },
    };
    return submitInsights(payload, btn, orig);
  }

  readFileText(fileSingle)
    .then(function (text) {
      var payload = { format: 'auto', source: null };
      if (text != null) {
        payload.source = (text.is_pdf) ? text : { text: text };
      }
      return fetch('/api/insights', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    })
    .then(function (res) { return res.json().then(function (d) { return { ok: res.ok, data: d }; }); })
    .then(function (r) {
      if (!r.ok) throw new Error(r.data.error || 'Analysis failed');
      renderInsights(r.data);
      autoSaveToHistory(r.data, 'single');
      showToast('Insights ready');
    })
    .catch(function (err) {
      showToast(err.message || 'Failed to analyze', true);
    })
    .finally(function () {
      btn.disabled = false;
      btn.innerHTML = orig;
    });
}

/* Shared POST + render for the single-report flow (manual mode) */
function submitInsights(payload, btn, orig) {
  return fetch('/api/insights', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
    .then(function (res) { return res.json().then(function (d) { return { ok: res.ok, data: d }; }); })
    .then(function (r) {
      if (!r.ok) throw new Error(r.data.error || 'Analysis failed');
      renderInsights(r.data);
      autoSaveToHistory(r.data, 'single');
      showToast('Insights ready');
    })
    .catch(function (err) {
      showToast(err.message || 'Failed to analyze', true);
    })
    .finally(function () {
      btn.disabled = false;
      btn.innerHTML = orig;
    });
}

/* Auto-save every analyzed report to history so it shows under /history. */
function autoSaveToHistory(a, type) {
  if (!a) return;
  var title = type === 'single'
    ? (a.label || 'Single Report Insights')
    : a.previous_label + ' vs ' + a.current_label;
  fetch('/api/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ analysis: a, title: title, type: type }),
  }).catch(function () { /* non-blocking; user can Save manually */ });
}

/* ---------- Mode switching ---------- */
function switchMode(mode) {
  var single = document.getElementById('single-input');
  var compare = document.getElementById('compare-input');
  var bSingle = document.getElementById('mode-single');
  var bCompare = document.getElementById('mode-compare');
  var results = document.getElementById('results');

  var isSingle = mode === 'single';
  if (single) single.classList.toggle('hidden', !isSingle);
  if (compare) compare.classList.toggle('hidden', isSingle);
  if (bSingle) bSingle.classList.toggle('active', isSingle);
  if (bCompare) bCompare.classList.toggle('active', !isSingle);

  var actions = document.getElementById('report-actions');
  if (actions) actions.classList.add('hidden');
  clearResults();
  window.__analysis = null;
}

/* ---------- Save to history ---------- */
function saveToHistory() {
  var a = window.__analysis;
  if (!a) { showToast('Run an analysis first', true); return; }
  var title = window.__analysisType === 'single'
    ? (a.label || 'Single Report Insights')
    : a.previous_label + ' vs ' + a.current_label;
  fetch('/api/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ analysis: a, title: title, type: window.__analysisType }),
  })
    .then(function (res) { return res.json(); })
    .then(function (d) {
      if (d.id) { showToast('Saved to history'); window.location = '/reports/' + d.id; }
      else showToast(d.error || 'Failed to save', true);
    })
    .catch(function () { showToast('Failed to save', true); });
}

/* ---------- Export ---------- */
function copyReport() {
  var a = window.__analysis;
  if (!a) return;
  var lines;
  if (window.__analysisType === 'single') {
    lines = ['# Prodlysis UX Insights', '',
      '**Report:** ' + (document.getElementById('single-label').value || 'Current'),
      '**UX Health Score:** ' + a.health + '/100 (' + a.health_label + ')', '',
      '## Summary', a.summary || '', '',
      '## Findings', ''];
    (a.findings || []).forEach(function (f) { lines.push('- ' + f.title); });
    lines.push('', '## UI Revamp Suggestions');
    (a.recommendations || []).forEach(function (r, i) { lines.push((i + 1) + '. [' + r.priority + '] ' + r.text); });
  } else {
    lines = ['# Prodlysis UX Comparison Report', '',
      '**Periods:** ' + a.previous_label + ' vs ' + a.current_label,
      '**UX Health Score:** ' + a.health + '/100 (Δ ' + (a.health_delta >= 0 ? '+' : '') + a.health_delta + ' points)', '',
      '## Executive Summary', a.summary || '', '',
      '## Metrics Comparison', ''];
    (a.metrics || []).forEach(function (m) {
      lines.push('- ' + m.label + ': ' + m.previous_display + ' → ' + m.current_display +
        ' (' + (m.change >= 0 ? '+' : '') + m.change.toFixed(1) + '%, ' + m.status + ')');
    });
    lines.push('', '## Recommendations');
    (a.recommendations || []).forEach(function (r, i) { lines.push((i + 1) + '. [' + r.priority + '] ' + r.text); });
    if (a.dropoff_analysis && a.dropoff_analysis.dropoff_points) {
      lines.push('', '## User Drop-off Analysis');
      lines.push('Worst drop-off point: ' + a.dropoff_analysis.worst);
      lines.push(a.dropoff_analysis.summary || '');
      (a.dropoff_analysis.dropoff_points || []).forEach(function (p) {
        lines.push('- ' + p.label + ': ' + p.display + ' (' + p.status + ') - ' + p.point);
      });
      (a.dropoff_analysis.recommendations || []).forEach(function (r, i) {
        lines.push((i + 1) + '. [' + r.priority + '] ' + r.text);
      });
    }
  }
  navigator.clipboard.writeText(lines.join('\n'))
    .then(function () { showToast('Copied to clipboard'); })
    .catch(function () { showToast('Copy failed', true); });
}

function saveForExport() {
  var a = window.__analysis;
  if (!a) return Promise.reject('Run an analysis first');
  var title = window.__analysisType === 'single'
    ? (a.label || 'Single Report Insights')
    : a.previous_label + ' vs ' + a.current_label;
  return fetch('/api/save', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ analysis: a, title: title, type: window.__analysisType }),
  }).then(function (res) { return res.json(); });
}

function downloadMarkdown() {
  saveForExport().then(function (d) {
    if (d.id) window.location = '/api/reports/' + d.id + '/markdown';
  }).catch(function (e) { showToast(e || 'Export failed', true); });
}

function downloadPdf() {
  saveForExport().then(function (d) {
    if (d.id) window.location = '/api/reports/' + d.id + '/pdf';
  }).catch(function (e) { showToast(e || 'Export failed', true); });
}

/* ---------- Wire up ---------- */
document.addEventListener('DOMContentLoaded', function () {
  initDropzone('dz-prev', 'file-prev', '#dz-prev .dz-file');
  initDropzone('dz-curr', 'file-curr', '#dz-curr .dz-file');
  initDropzone('dz-single', 'file-single', '#dz-single .dz-file');

  // Input method toggle: either upload the document(s) OR enter metrics.
  document.querySelectorAll('.im-btn').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var mode = btn.getAttribute('data-for');
      var method = btn.getAttribute('data-method');
      document.querySelectorAll('.im-btn[data-for="' + mode + '"]').forEach(function (b) {
        b.classList.toggle('active', b === btn);
      });
      document.querySelectorAll('[data-method-panel="' + mode + '-upload"], [data-method-panel="' + mode + '-manual"]').forEach(function (p) {
        p.classList.toggle('hidden', p.getAttribute('data-method-panel') !== mode + '-' + method);
      });
      clearResults();
    });
  });

  // Changing any input source clears previously rendered results, so no
  // metrics comparison shows until a fresh analysis is run.
  ['manual-prev', 'manual-curr', 'manual-single'].forEach(function (id) {
    var ta = document.getElementById(id);
    if (ta) ta.addEventListener('input', clearResults);
  });

  var modeSingle = document.getElementById('mode-single');
  var modeCompare = document.getElementById('mode-compare');
  if (modeSingle) modeSingle.addEventListener('click', function () { switchMode('single'); });
  if (modeCompare) modeCompare.addEventListener('click', function () { switchMode('compare'); });

  var insightsBtn = document.getElementById('insights-btn');
  if (insightsBtn) insightsBtn.addEventListener('click', runInsights);

  var analyzeBtn = document.getElementById('analyze-btn');
  if (analyzeBtn) analyzeBtn.addEventListener('click', runAnalysis);

  var saveBtn = document.getElementById('save-btn');
  if (saveBtn) saveBtn.addEventListener('click', saveToHistory);

  var copyBtn = document.getElementById('copy-btn');
  if (copyBtn) copyBtn.addEventListener('click', copyReport);

  var mdBtn = document.getElementById('md-btn');
  if (mdBtn) mdBtn.addEventListener('click', downloadMarkdown);

  var pdfBtn = document.getElementById('pdf-btn');
  if (pdfBtn) pdfBtn.addEventListener('click', downloadPdf);
});
