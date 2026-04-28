# Uptime Robot

Production-grade uptime monitor and dashboard implemented in Python.

Quick start

1. Create a virtualenv and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

2. Copy `.env` and configure SMTP & optional settings:

```bash
cp .env .env.local
# Edit .env.local and set SMTP_USERNAME, SMTP_PASSWORD, ALERT_TO_EMAILS, etc.
```

3. Start the monitor (recommended to run as a service):

```bash
python3 monitor.py
```

4. Start the dashboard (runs on configured host/port):

```bash
python3 dashboard.py
```

Manage sites

- Add a site:

```bash
python3 manage_sites.py add --name "My Site" --url "https://example.com" --expected-status 200 --latency-threshold-ms 800 --check-cert --cert-warn-days 14
```

- List sites:

```bash
python3 manage_sites.py list
```

Testing alerts

- To test email delivery, configure SMTP settings in `.env.local`, then trigger an alert by setting `enabled: false` for a site and running the monitor or by temporarily setting a very low `latency_threshold_ms`.

Files of interest

- `monitor.py` — long-running monitor with retries, cert checks, email alerts, history and state management.
- `dashboard.py` — Flask dashboard with `/api/status` and `/api/history` endpoints and Chart.js frontend.
- `manage_sites.py` — CLI for managing `sites.json`.
- `sites.json` — configure monitored sites (example included).
- `.env` — template for environment variables.

Notes

- The monitor reads `sites.json` on each cycle — changes take effect without a restart.
- `history.json` stores a rolling 30-day history used to compute uptime windows.
- `state.json` prevents repeated alerts and tracks last alert times.
- Logs are written to `uptime.log` and stdout.
# Uptime Robot

This project is a production-oriented uptime monitoring robot built in Python. It watches sites defined in `sites.json`, hot-reloads configuration changes without a restart, stores rolling history in JSON, sends deduplicated email alerts, checks SSL expiry, and exposes a Flask dashboard plus JSON APIs.

## What It Does

- Hot-reloads `sites.json` on every monitoring cycle
- Checks HTTP/HTTPS targets with retry and backoff
- Validates expected HTTP status codes and optional response text
- Tracks latency for every check and stores 30 days of history in `history.json`
- Stores live state and alert timestamps in `state.json`
- Sends SMTP email alerts for:
  - site DOWN
  - site recovery
  - high latency with cooldown
  - SSL certificate nearing expiry once per day
  - monitor-loop crashes
- Shows a dashboard with:
  - live UP/DOWN state
  - latency bar
  - last check time
  - 24h / 7d / 30d uptime percentages
  - SSL certificate countdown
  - Chart.js latency history
- Provides JSON APIs at `/api/status` and `/api/history`
- Logs to both console and `uptime.log`

## Files

- `monitor.py`: background monitoring loop
- `dashboard.py`: Flask dashboard served by Waitress
- `manage_sites.py`: CLI to add, remove, enable, disable, and update sites
- `sites.json`: monitored site definitions
- `.env.example` and `.env.template`: environment templates, copy one to `.env`
- `state.json`: live status and alert persistence, created automatically
- `history.json`: rolling 30-day check history, created automatically
- `uptime.log`: rotating-by-OS log target used by both processes
- `uptime_robot/common.py`: shared config, JSON, and uptime helpers

## Requirements

- Python 3.11+
- Network access from the host to the monitored sites and SMTP server

Install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Configuration

Create your runtime environment file:

```bash
cp .env.example .env
```

Important `.env` values:

- `CHECK_INTERVAL_SECONDS`: monitor loop interval
- `REQUEST_TIMEOUT_SECONDS`: per-request timeout
- `RETRY_COUNT`: retries after a failed check
- `RETRY_BACKOFF_SECONDS`: linear retry backoff multiplier
- `LATENCY_ALERT_COOLDOWN_MINUTES`: suppress repeated latency alerts
- `SMTP_*`: SMTP transport settings
- `ALERT_FROM_EMAIL`, `ALERT_TO_EMAILS`: alert sender and recipients
- `DASHBOARD_HOST`, `DASHBOARD_PORT`: dashboard bind settings

## Site Definition Format

`sites.json` supports a top-level object with a `sites` array:

```json
{
  "sites": [
    {
      "name": "Production API",
      "url": "https://api.example.com/health",
      "expected_status": 200,
      "expected_text": "ok",
      "latency_threshold_ms": 800,
      "check_cert": true,
      "cert_warn_days": 21,
      "enabled": true
    }
  ]
}
```

Supported fields:

- `name`: display name, unique
- `url`: `http://` or `https://` URL
- `expected_status`: integer or list of integers
- `expected_text`: optional string that must appear in the response body
- `latency_threshold_ms`: optional latency alert threshold
- `check_cert`: enable SSL expiry tracking for HTTPS targets
- `cert_warn_days`: days remaining threshold for SSL alerts
- `enabled`: enable or pause monitoring for the site

Changes to `sites.json` are picked up automatically on the next loop. No restart is required.

## Running

Start the monitor:

```bash
python3 monitor.py
```

Start the dashboard in a second terminal:

```bash
python3 dashboard.py
```

Open `http://127.0.0.1:5000`.

## Site Management CLI

List sites:

```bash
python3 manage_sites.py list
```

Add a site:

```bash
python3 manage_sites.py add \
  --name "Example API" \
  --url "https://api.example.com/health" \
  --expected-status 200 \
  --expected-text ok \
  --latency-threshold-ms 900 \
  --check-cert \
  --cert-warn-days 21
```

Disable or enable a site:

```bash
python3 manage_sites.py disable --name "Example API"
python3 manage_sites.py enable --name "Example API"
```

Update an existing site:

```bash
python3 manage_sites.py update \
  --name "Example API" \
  --latency-threshold-ms 1200 \
  --expected-status 200 \
  --expected-status 204
```

Remove a site:

```bash
python3 manage_sites.py remove --name "Example API"
```

## API Endpoints

- `GET /healthz`
- `GET /api/status`
- `GET /api/history?hours=24`

`/api/status` returns the current view shown in the dashboard, including uptime windows and SSL countdowns. `/api/history` returns the latency and status history grouped by site.

## Alert Behavior

- DOWN alerts are sent only on transition into a new incident.
- Recovery alerts are sent only when a site moves from DOWN back to UP.
- High-latency alerts respect `LATENCY_ALERT_COOLDOWN_MINUTES`.
- SSL expiry alerts are sent once per UTC day per site while the certificate remains inside the warning window.
- If the monitor loop throws an unhandled exception, it logs the error, sends a crash email, waits, and resumes automatically.

## Data Files

- `state.json`: current status, timestamps, and last alert bookkeeping
- `history.json`: every check result kept for the rolling retention window

Both files are written atomically to reduce corruption risk if the process is interrupted mid-write.

## systemd Example

Create `/etc/systemd/system/uptime-monitor.service`:

```ini
[Unit]
Description=Uptime Robot Monitor
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/uptime-robot
EnvironmentFile=/opt/uptime-robot/.env
ExecStart=/opt/uptime-robot/.venv/bin/python monitor.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Create `/etc/systemd/system/uptime-dashboard.service`:

```ini
[Unit]
Description=Uptime Robot Dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/uptime-robot
EnvironmentFile=/opt/uptime-robot/.env
ExecStart=/opt/uptime-robot/.venv/bin/python dashboard.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now uptime-monitor.service uptime-dashboard.service
sudo systemctl status uptime-monitor.service uptime-dashboard.service
```

## Docker Example

Build with a simple image:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "monitor.py"]
```

Run the monitor:

```bash
docker build -t uptime-robot .
docker run -d --name uptime-monitor --env-file .env -v "$PWD:/app" uptime-robot
```

Run the dashboard:

```bash
docker run -d --name uptime-dashboard --env-file .env -p 5000:5000 -v "$PWD:/app" uptime-robot python dashboard.py
```

## Operational Notes

- Keep `sites.json`, `state.json`, and `history.json` on persistent storage.
- If SMTP is not configured, monitoring still runs but email alerts are skipped.
- The dashboard reads JSON files directly, so it does not depend on monitor process memory.
- The included sample targets in `sites.json` are examples only. Replace them with your own production endpoints before relying on alerts.
