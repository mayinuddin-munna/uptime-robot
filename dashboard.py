from __future__ import annotations

from flask import Flask, jsonify, render_template_string, request
from waitress import serve

from uptime_robot.common import (
    configure_logging,
    ensure_runtime_files,
    load_history,
    load_settings,
    load_sites,
    load_state,
    history_snapshot,
    status_snapshot,
)

HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Uptime Robot</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns"></script>
  <style>
    :root {
      --bg: #f4efe7;
      --panel: rgba(255, 252, 247, 0.92);
      --line: rgba(77, 56, 33, 0.14);
      --text: #2d241b;
      --muted: #7d6850;
      --up: #177245;
      --down: #b33624;
      --paused: #8b6b2f;
      --accent: #d98f2b;
      --chart-1: #d46a4c;
      --chart-2: #0b7a75;
      --chart-3: #5b4eb5;
      --chart-4: #9b7d24;
      --chart-5: #bd4f6c;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Trebuchet MS", "Segoe UI", sans-serif;
      color: var(--text);
      background:
        radial-gradient(circle at top left, rgba(217, 143, 43, 0.22), transparent 28%),
        linear-gradient(180deg, #efe5d5 0%, #f8f4ee 52%, #ece1d0 100%);
    }
    .shell {
      max-width: 1300px;
      margin: 0 auto;
      padding: 32px 18px 48px;
    }
    .hero {
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 20px;
      flex-wrap: wrap;
      margin-bottom: 24px;
    }
    .hero h1 {
      margin: 0;
      font-size: clamp(2.2rem, 4vw, 4rem);
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }
    .hero p {
      margin: 8px 0 0;
      max-width: 760px;
      color: var(--muted);
    }
    .stamp {
      padding: 12px 14px;
      border-radius: 16px;
      background: var(--panel);
      border: 1px solid var(--line);
      color: var(--muted);
      min-width: 240px;
      text-align: right;
      box-shadow: 0 18px 40px rgba(57, 44, 26, 0.08);
    }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 22px;
      box-shadow: 0 18px 40px rgba(57, 44, 26, 0.08);
      overflow: hidden;
    }
    .panel + .panel {
      margin-top: 22px;
    }
    .panel-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
      padding: 18px 20px 0;
    }
    .panel-header h2 {
      margin: 0;
      font-size: 1.2rem;
    }
    .panel-header p {
      margin: 6px 0 0;
      color: var(--muted);
    }
    .table-wrap {
      overflow-x: auto;
      padding: 14px 18px 18px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      min-width: 1050px;
    }
    th, td {
      text-align: left;
      padding: 12px 10px;
      border-bottom: 1px solid rgba(77, 56, 33, 0.08);
      vertical-align: top;
      font-size: 0.95rem;
    }
    th {
      color: var(--muted);
      font-weight: 700;
    }
    .site-name {
      font-weight: 700;
    }
    .site-url {
      color: var(--muted);
      word-break: break-all;
      display: block;
      margin-top: 4px;
      font-size: 0.86rem;
    }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      border-radius: 999px;
      padding: 6px 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      font-size: 0.74rem;
    }
    .badge.up { background: rgba(23, 114, 69, 0.12); color: var(--up); }
    .badge.down { background: rgba(179, 54, 36, 0.12); color: var(--down); }
    .badge.paused { background: rgba(139, 107, 47, 0.16); color: var(--paused); }
    .badge.unknown { background: rgba(125, 104, 80, 0.12); color: var(--muted); }
    .latency {
      min-width: 180px;
    }
    .bar {
      position: relative;
      margin-top: 8px;
      height: 10px;
      border-radius: 999px;
      background: rgba(77, 56, 33, 0.08);
      overflow: hidden;
    }
    .bar > span {
      display: block;
      height: 100%;
      border-radius: 999px;
      background: linear-gradient(90deg, #1b8e57 0%, #d98f2b 70%, #b33624 100%);
    }
    .metric {
      white-space: nowrap;
    }
    .muted {
      color: var(--muted);
    }
    .error {
      color: var(--down);
      max-width: 320px;
      word-break: break-word;
    }
    .chart-wrap {
      padding: 12px 20px 20px;
    }
    .controls {
      display: flex;
      gap: 10px;
      align-items: center;
      flex-wrap: wrap;
    }
    select {
      border: 1px solid var(--line);
      background: white;
      color: var(--text);
      border-radius: 999px;
      padding: 10px 14px;
      font: inherit;
    }
    @media (max-width: 900px) {
      .shell { padding: 20px 12px 36px; }
      .stamp { text-align: left; width: 100%; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <section class="hero">
      <div>
        <h1>Uptime Robot</h1>
        <p>Live uptime, latency, and certificate visibility driven directly from <code>sites.json</code>. The table and chart refresh automatically without restarting the monitor.</p>
      </div>
      <div class="stamp">
        <div>Refresh interval: {{ refresh_seconds }}s</div>
        <div id="updatedAt">Loading...</div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>Current Status</h2>
          <p>UP/DOWN state, latest latency, uptime windows, and SSL expiry countdown.</p>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Site</th>
              <th>Status</th>
              <th>Latency</th>
              <th>Last Check</th>
              <th>Uptime</th>
              <th>Certificate</th>
              <th>Notes</th>
            </tr>
          </thead>
          <tbody id="statusRows">
            <tr><td colspan="7" class="muted">Loading status...</td></tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel">
      <div class="panel-header">
        <div>
          <h2>Latency History</h2>
          <p>Last 24 hours of latency data, selectable per site or across all sites.</p>
        </div>
        <div class="controls">
          <label for="siteFilter" class="muted">View</label>
          <select id="siteFilter">
            <option value="__all__">All sites</option>
          </select>
        </div>
      </div>
      <div class="chart-wrap">
        <canvas id="latencyChart" height="110"></canvas>
      </div>
    </section>
  </div>

  <script>
    const refreshSeconds = {{ refresh_seconds }};
    let latencyChart = null;
    let lastStatus = [];
    let lastHistory = {};

    function badgeClass(status) {
      return ['up', 'down', 'paused'].includes(status) ? status : 'unknown';
    }

    function textOrDash(value) {
      return value === null || value === undefined || value === '' ? '—' : value;
    }

    function certificateText(site) {
      if (!site.cert_expires_at) {
        return site.cert_error ? `Unavailable (${site.cert_error})` : 'Not enabled';
      }
      return `${site.cert_status || 'Tracked'} (${site.cert_expires_at})`;
    }

    function renderTable(rows) {
      const tbody = document.getElementById('statusRows');
      if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="muted">No sites configured.</td></tr>';
        return;
      }

      tbody.innerHTML = rows.map((site) => `
        <tr>
          <td>
            <span class="site-name">${site.name}</span>
            <span class="site-url">${site.url}</span>
          </td>
          <td><span class="badge ${badgeClass(site.status)}">${site.status}</span></td>
          <td class="latency">
            <div class="metric">${textOrDash(site.last_latency_ms)} ${site.last_latency_ms ? 'ms' : ''}</div>
            <div class="muted">threshold: ${textOrDash(site.latency_threshold_ms)} ${site.latency_threshold_ms ? 'ms' : ''}</div>
            <div class="bar"><span style="width:${site.latency_bar_percent || 0}%"></span></div>
          </td>
          <td>
            <div>${textOrDash(site.last_check)}</div>
            <div class="muted">Changed: ${textOrDash(site.last_change)}</div>
          </td>
          <td>
            <div>24h: ${site.uptime_24h}%</div>
            <div>7d: ${site.uptime_7d}%</div>
            <div>30d: ${site.uptime_30d}%</div>
          </td>
          <td>
            <div>${certificateText(site)}</div>
          </td>
          <td class="${site.last_error ? 'error' : 'muted'}">${textOrDash(site.last_error)}</td>
        </tr>
      `).join('');
    }

    function rebuildFilter(statusRows) {
      const select = document.getElementById('siteFilter');
      const previous = select.value;
      select.innerHTML = '<option value="__all__">All sites</option>' + statusRows
        .map((site) => `<option value="${site.name}">${site.name}</option>`)
        .join('');
      if ([...select.options].some((option) => option.value === previous)) {
        select.value = previous;
      }
    }

    function buildDatasets(selectedSite) {
      const palette = ['var(--chart-1)', 'var(--chart-2)', 'var(--chart-3)', 'var(--chart-4)', 'var(--chart-5)'];
      const siteNames = selectedSite === '__all__'
        ? Object.keys(lastHistory)
        : [selectedSite];

      return siteNames
        .filter((name) => Array.isArray(lastHistory[name]))
        .map((name, index) => ({
          label: name,
          data: lastHistory[name]
            .filter((point) => point.latency_ms !== null && point.latency_ms !== undefined)
            .map((point) => ({ x: point.checked_at, y: point.latency_ms })),
          borderColor: palette[index % palette.length],
          backgroundColor: palette[index % palette.length],
          tension: 0.24,
          spanGaps: true
        }));
    }

    function renderChart() {
      const selected = document.getElementById('siteFilter').value;
      const datasets = buildDatasets(selected);
      const ctx = document.getElementById('latencyChart');
      if (latencyChart) {
        latencyChart.destroy();
      }
      latencyChart = new Chart(ctx, {
        type: 'line',
        data: { datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          parsing: false,
          scales: {
            x: {
              type: 'time',
              time: { tooltipFormat: 'yyyy-MM-dd HH:mm:ss' }
            },
            y: {
              beginAtZero: true,
              title: { display: true, text: 'Latency (ms)' }
            }
          },
          plugins: {
            legend: { display: true }
          }
        }
      });
    }

    async function fetchJson(url) {
      const response = await fetch(url, { cache: 'no-store' });
      if (!response.ok) {
        throw new Error(`Request failed for ${url}: ${response.status}`);
      }
      return response.json();
    }

    async function refreshData() {
      const [statusPayload, historyPayload] = await Promise.all([
        fetchJson('/api/status'),
        fetchJson('/api/history?hours=24')
      ]);
      lastStatus = statusPayload.sites || [];
      lastHistory = historyPayload.history || {};
      renderTable(lastStatus);
      rebuildFilter(lastStatus);
      renderChart();
      document.getElementById('updatedAt').textContent = `Updated: ${statusPayload.updated_at || 'unknown'}`;
    }

    document.getElementById('siteFilter').addEventListener('change', renderChart);
    refreshData().catch((error) => {
      document.getElementById('statusRows').innerHTML = `<tr><td colspan="7" class="error">${error.message}</td></tr>`;
    });
    setInterval(() => refreshData().catch(console.error), refreshSeconds * 1000);
  </script>
</body>
</html>
"""

app = Flask(__name__)


def read_runtime():
    settings = load_settings()
    ensure_runtime_files(settings)
    logger = configure_logging(settings.log_file, logger_name="uptime_dashboard")

    try:
        sites = load_sites(settings.sites_file)
    except Exception:
        logger.exception("Failed to load sites for dashboard")
        sites = []

    try:
        state = load_state(settings.state_file)
    except Exception:
        logger.exception("Failed to load state for dashboard")
        state = {"updated_at": None, "sites": {}}

    try:
        history = load_history(settings.history_file)
    except Exception:
        logger.exception("Failed to load history for dashboard")
        history = {"updated_at": None, "sites": {}}

    return settings, sites, state, history


@app.get("/")
def index():
    settings, _, _, _ = read_runtime()
    return render_template_string(HTML_TEMPLATE, refresh_seconds=settings.dashboard_refresh_seconds)


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.get("/api/status")
def api_status():
    _, sites, state, history = read_runtime()
    return jsonify(
        {
            "updated_at": state.get("updated_at"),
            "sites": status_snapshot(sites, state, history),
        }
    )


@app.get("/api/history")
def api_history():
    _, sites, _, history = read_runtime()
    try:
        hours = max(1, min(int(request.args.get("hours", "24")), 24 * 30))
    except ValueError:
        hours = 24
    return jsonify({"hours": hours, "history": history_snapshot(sites, history, hours=hours)})


def main() -> None:
    settings, _, _, _ = read_runtime()
    app.logger.handlers.clear()
    serve(app, host=settings.dashboard_host, port=settings.dashboard_port)


if __name__ == "__main__":
    main()
