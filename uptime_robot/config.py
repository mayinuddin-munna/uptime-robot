from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AlertConfig:
    type: str
    url: str
    method: str = "POST"
    headers: dict[str, str] = field(default_factory=dict)
    body_template: str | None = None


@dataclass(slots=True)
class MonitorConfig:
    name: str
    url: str
    interval_seconds: int = 60
    timeout_seconds: int = 10
    expected_status: list[int] = field(default_factory=lambda: [200])
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    body: str | None = None
    verify_ssl: bool = True
    tags: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AppConfig:
    monitors: list[MonitorConfig]
    alerts: list[AlertConfig] = field(default_factory=list)
    data_dir: str = "data"


def _require_keys(data: dict[str, Any], required: list[str], scope: str) -> None:
    missing = [key for key in required if key not in data]
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"Missing required keys in {scope}: {joined}")


def load_config(config_path: str | Path) -> AppConfig:
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Top-level config must be a JSON object")

    monitors_raw = raw.get("monitors", [])
    if not monitors_raw:
        raise ValueError("Config must define at least one monitor")

    monitors: list[MonitorConfig] = []
    for index, item in enumerate(monitors_raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Monitor #{index} must be an object")
        _require_keys(item, ["name", "url"], f"monitor #{index}")
        monitors.append(
            MonitorConfig(
                name=item["name"],
                url=item["url"],
                interval_seconds=int(item.get("interval_seconds", 60)),
                timeout_seconds=int(item.get("timeout_seconds", 10)),
                expected_status=list(item.get("expected_status", [200])),
                method=str(item.get("method", "GET")).upper(),
                headers=dict(item.get("headers", {})),
                body=item.get("body"),
                verify_ssl=bool(item.get("verify_ssl", True)),
                tags=list(item.get("tags", [])),
            )
        )

    alerts_raw = raw.get("alerts", [])
    alerts: list[AlertConfig] = []
    for index, item in enumerate(alerts_raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Alert #{index} must be an object")
        _require_keys(item, ["type", "url"], f"alert #{index}")
        alerts.append(
            AlertConfig(
                type=item["type"],
                url=item["url"],
                method=str(item.get("method", "POST")).upper(),
                headers=dict(item.get("headers", {})),
                body_template=item.get("body_template"),
            )
        )

    return AppConfig(
        monitors=monitors,
        alerts=alerts,
        data_dir=str(raw.get("data_dir", "data")),
    )
