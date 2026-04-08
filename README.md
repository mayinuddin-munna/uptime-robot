# Uptime Robot

A lightweight, dependency-free uptime monitor for DevOps teams. It performs scheduled HTTP checks, stores history in SQLite, tracks incidents, exposes a JSON API, and ships with a built-in dashboard.

## Features

- Config-driven monitors from a single JSON file
- Background health checks with configurable intervals and timeouts
- SQLite persistence for current status, check history, and incident tracking
- Built-in dashboard at `/`
- JSON API for status, checks, and incidents
- Optional webhook alerts for outages and recoveries
- No external Python packages required

## Project Layout

- `main.py`: entry point
- `uptime_robot/config.py`: config loading and validation
- `uptime_robot/monitor.py`: checker threads and alert dispatching
- `uptime_robot/storage.py`: SQLite persistence
- `uptime_robot/web.py`: dashboard and JSON API
- `monitors.sample.json`: sample configuration

## Quick Start

1. Copy the sample config:

```bash
cp monitors.sample.json monitors.json
```

2. Adjust the endpoints in `monitors.json`.

3. Start the service:

```bash
python3 main.py --config monitors.json --host 0.0.0.0 --port 8000
```

4. Open `http://127.0.0.1:8000` for the dashboard.

## Config Example

```json
{
  "data_dir": "data",
  "monitors": [
    {
      "name": "Production API",
      "url": "https://api.example.com/healthz",
      "interval_seconds": 30,
      "timeout_seconds": 5,
      "expected_status": [200, 204],
      "method": "GET",
      "headers": {
        "Authorization": "Bearer token"
      },
      "tags": ["prod", "api"]
    }
  ],
  "alerts": [
    {
      "type": "webhook",
      "url": "https://hooks.slack.com/services/...",
      "body_template": "{\"text\": \"[$event] $monitor is now ${ok}\"}"
    }
  ]
}
```

## API Endpoints

- `GET /healthz`: process health check
- `GET /api/status`: current status for all monitors
- `GET /api/checks?limit=100`: latest checks across all monitors
- `GET /api/checks?monitor=Production%20API&limit=50`: history for a single monitor
- `GET /api/incidents?limit=50`: latest incidents

## Notes

- Alerts are best-effort. Failed webhook deliveries are ignored so monitoring continues.
- SQLite data is stored in `data/uptime_robot.db` by default.
- If you want to run this as a service, place it behind `systemd`, Docker, or a process supervisor.
# uptime-robot
