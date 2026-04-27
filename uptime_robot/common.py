from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:  # pragma: no cover - fallback for pre-install bootstrap
    def load_dotenv(*args: Any, **kwargs: Any) -> bool:
        return False

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


@dataclass(slots=True)
class Settings:
    sites_file: Path
    state_file: Path
    history_file: Path
    log_file: Path
    check_interval_seconds: int
    request_timeout_seconds: int
    retry_count: int
    retry_backoff_seconds: float
    latency_alert_cooldown_minutes: int
    dashboard_host: str
    dashboard_port: int
    dashboard_refresh_seconds: int
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_use_tls: bool
    smtp_use_ssl: bool
    alert_from_email: str | None
    alert_to_emails: list[str]
    history_retention_days: int
    crash_retry_delay_seconds: int
    max_workers: int


@dataclass(slots=True)
class SiteConfig:
    name: str
    url: str
    expected_status: list[int]
    expected_text: str | None
    latency_threshold_ms: int | None
    check_cert: bool
    cert_warn_days: int
    enabled: bool

    def as_json(self) -> dict[str, Any]:
        payload = asdict(self)
        if len(self.expected_status) == 1:
            payload["expected_status"] = self.expected_status[0]
        return payload

    @property
    def is_https(self) -> bool:
        return urlparse(self.url).scheme.lower() == "https"


def configure_logging(log_file: Path, logger_name: str = "uptime_robot") -> logging.Logger:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not logger.handlers:
        formatter = logging.Formatter(LOG_FORMAT)

        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)

        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)

    return logger


def load_settings(env_path: str | Path = ".env") -> Settings:
    load_dotenv(dotenv_path=env_path, override=False)

    return Settings(
        sites_file=Path(os.getenv("SITES_FILE", "sites.json")),
        state_file=Path(os.getenv("STATE_FILE", "state.json")),
        history_file=Path(os.getenv("HISTORY_FILE", "history.json")),
        log_file=Path(os.getenv("LOG_FILE", "uptime.log")),
        check_interval_seconds=max(5, env_int("CHECK_INTERVAL_SECONDS", 60)),
        request_timeout_seconds=max(1, env_int("REQUEST_TIMEOUT_SECONDS", 15)),
        retry_count=max(0, env_int("RETRY_COUNT", 2)),
        retry_backoff_seconds=max(0.5, env_float("RETRY_BACKOFF_SECONDS", 5.0)),
        latency_alert_cooldown_minutes=max(1, env_int("LATENCY_ALERT_COOLDOWN_MINUTES", 60)),
        dashboard_host=os.getenv("DASHBOARD_HOST", "0.0.0.0"),
        dashboard_port=max(1, env_int("DASHBOARD_PORT", 5000)),
        dashboard_refresh_seconds=max(15, env_int("DASHBOARD_REFRESH_SECONDS", 45)),
        smtp_host=os.getenv("SMTP_HOST") or None,
        smtp_port=max(1, env_int("SMTP_PORT", 587)),
        smtp_username=os.getenv("SMTP_USERNAME") or None,
        smtp_password=os.getenv("SMTP_PASSWORD") or None,
        smtp_use_tls=env_bool("SMTP_USE_TLS", True),
        smtp_use_ssl=env_bool("SMTP_USE_SSL", False),
        alert_from_email=os.getenv("ALERT_FROM_EMAIL") or None,
        alert_to_emails=split_csv(os.getenv("ALERT_TO_EMAILS", "")),
        history_retention_days=max(1, env_int("HISTORY_RETENTION_DAYS", 30)),
        crash_retry_delay_seconds=max(5, env_int("CRASH_RETRY_DELAY_SECONDS", 30)),
        max_workers=max(1, env_int("MAX_WORKERS", 8)),
    )


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value in (None, ""):
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=False), encoding="utf-8")
    temp_path.replace(path)


def read_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed JSON in {path}: {exc}") from exc


def ensure_runtime_files(settings: Settings) -> None:
    if not settings.sites_file.exists():
        atomic_write_json(settings.sites_file, {"sites": []})
    if not settings.state_file.exists():
        atomic_write_json(settings.state_file, default_state_payload())
    if not settings.history_file.exists():
        atomic_write_json(settings.history_file, default_history_payload())


def default_state_payload() -> dict[str, Any]:
    return {"updated_at": None, "sites": {}}


def default_history_payload() -> dict[str, Any]:
    return {"updated_at": None, "sites": {}}


def load_sites(path: Path) -> list[SiteConfig]:
    raw = read_json_file(path, {"sites": []})
    if isinstance(raw, list):
        sites_raw = raw
    elif isinstance(raw, dict):
        sites_raw = raw.get("sites", [])
    else:
        raise ValueError(f"{path} must contain either a list or an object with a 'sites' key")

    if not isinstance(sites_raw, list):
        raise ValueError(f"{path} field 'sites' must be a list")

    sites: list[SiteConfig] = []
    seen_names: set[str] = set()
    for index, item in enumerate(sites_raw, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"Site #{index} must be a JSON object")
        site = parse_site(item, index=index)
        lowered = site.name.strip().lower()
        if lowered in seen_names:
            raise ValueError(f"Duplicate site name: {site.name}")
        seen_names.add(lowered)
        sites.append(site)
    return sites


def save_sites(path: Path, sites: list[SiteConfig]) -> None:
    payload = {"sites": [site.as_json() for site in sites]}
    atomic_write_json(path, payload)


def parse_site(item: dict[str, Any], index: int | None = None) -> SiteConfig:
    label = f"site #{index}" if index is not None else "site"
    name = str(item.get("name", "")).strip()
    url = str(item.get("url", "")).strip()
    if not name:
        raise ValueError(f"Missing required field 'name' in {label}")
    if not url:
        raise ValueError(f"Missing required field 'url' in {label}")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid URL for {name}: {url}")

    expected_status = item.get("expected_status", 200)
    if isinstance(expected_status, int):
        status_codes = [expected_status]
    elif isinstance(expected_status, list) and expected_status:
        try:
            status_codes = [int(code) for code in expected_status]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid expected_status for {name}") from exc
    else:
        status_codes = [200]

    latency_threshold_ms = item.get("latency_threshold_ms")
    if latency_threshold_ms is not None:
        try:
            latency_threshold_ms = int(latency_threshold_ms)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Invalid latency_threshold_ms for {name}") from exc

    cert_warn_days = item.get("cert_warn_days", 30)
    try:
        cert_warn_days = int(cert_warn_days)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid cert_warn_days for {name}") from exc

    expected_text = item.get("expected_text")
    if expected_text is not None:
        expected_text = str(expected_text)

    return SiteConfig(
        name=name,
        url=url,
        expected_status=status_codes,
        expected_text=expected_text,
        latency_threshold_ms=latency_threshold_ms,
        check_cert=bool(item.get("check_cert", False)),
        cert_warn_days=max(1, cert_warn_days),
        enabled=bool(item.get("enabled", True)),
    )


def load_state(path: Path) -> dict[str, Any]:
    raw = read_json_file(path, default_state_payload())
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object")
    raw.setdefault("sites", {})
    raw.setdefault("updated_at", None)
    if not isinstance(raw["sites"], dict):
        raw["sites"] = {}
    return raw


def load_history(path: Path) -> dict[str, Any]:
    raw = read_json_file(path, default_history_payload())
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must contain a JSON object")
    raw.setdefault("sites", {})
    raw.setdefault("updated_at", None)
    if not isinstance(raw["sites"], dict):
        raw["sites"] = {}
    return raw


def ensure_site_state(state: dict[str, Any], site_name: str) -> dict[str, Any]:
    sites_state = state.setdefault("sites", {})
    current = sites_state.setdefault(
        site_name,
        {
            "status": "unknown",
            "last_check": None,
            "last_change": None,
            "last_error": None,
            "last_http_status": None,
            "last_latency_ms": None,
            "down_since": None,
            "last_down_alert_at": None,
            "last_recovery_alert_at": None,
            "last_latency_alert_at": None,
            "last_cert_alert_date": None,
            "cert_expires_at": None,
            "cert_days_left": None,
            "cert_error": None,
        },
    )
    return current


def append_history_entry(
    history: dict[str, Any],
    site_name: str,
    record: dict[str, Any],
    retention_days: int,
) -> None:
    sites_history = history.setdefault("sites", {})
    entries = sites_history.setdefault(site_name, [])
    entries.append(record)
    cutoff = utc_now() - timedelta(days=retention_days)
    filtered: list[dict[str, Any]] = []
    for entry in entries:
        checked_at = parse_iso(str(entry.get("checked_at")))
        if checked_at and checked_at >= cutoff:
            filtered.append(entry)
    sites_history[site_name] = filtered


def compute_uptime(records: list[dict[str, Any]], window: timedelta) -> float:
    cutoff = utc_now() - window
    filtered: list[dict[str, Any]] = []
    for record in records:
        checked_at = parse_iso(str(record.get("checked_at")))
        if checked_at and checked_at >= cutoff:
            filtered.append(record)
    if not filtered:
        return 100.0
    successes = sum(1 for record in filtered if bool(record.get("ok")))
    return round((successes / len(filtered)) * 100, 2)


def status_snapshot(
    sites: list[SiteConfig],
    state: dict[str, Any],
    history: dict[str, Any],
) -> list[dict[str, Any]]:
    history_sites = history.get("sites", {})
    rows: list[dict[str, Any]] = []
    for site in sorted(sites, key=lambda item: item.name.lower()):
        site_state = ensure_site_state(state, site.name)
        records = history_sites.get(site.name, [])
        latency = site_state.get("last_latency_ms")
        threshold = site.latency_threshold_ms or 1000
        latency_percent = min(100, int((float(latency or 0) / max(threshold, 1)) * 100))
        cert_days_left = site_state.get("cert_days_left")

        rows.append(
            {
                "name": site.name,
                "url": site.url,
                "enabled": site.enabled,
                "status": "paused" if not site.enabled else site_state.get("status", "unknown"),
                "last_check": site_state.get("last_check"),
                "last_change": site_state.get("last_change"),
                "last_error": site_state.get("last_error"),
                "last_http_status": site_state.get("last_http_status"),
                "last_latency_ms": latency,
                "latency_threshold_ms": site.latency_threshold_ms,
                "latency_bar_percent": latency_percent,
                "expected_status": site.expected_status,
                "expected_text": site.expected_text,
                "uptime_24h": compute_uptime(records, timedelta(hours=24)),
                "uptime_7d": compute_uptime(records, timedelta(days=7)),
                "uptime_30d": compute_uptime(records, timedelta(days=30)),
                "cert_expires_at": site_state.get("cert_expires_at"),
                "cert_days_left": cert_days_left,
                "cert_status": certificate_status(cert_days_left, site_state.get("cert_expires_at")),
                "cert_error": site_state.get("cert_error"),
            }
        )
    return rows


def history_snapshot(
    sites: list[SiteConfig],
    history: dict[str, Any],
    hours: int = 24,
) -> dict[str, list[dict[str, Any]]]:
    cutoff = utc_now() - timedelta(hours=hours)
    history_sites = history.get("sites", {})
    payload: dict[str, list[dict[str, Any]]] = {}
    for site in sites:
        entries = history_sites.get(site.name, [])
        filtered: list[dict[str, Any]] = []
        for entry in entries:
            checked_at = parse_iso(str(entry.get("checked_at")))
            if checked_at and checked_at >= cutoff:
                filtered.append(entry)
        payload[site.name] = filtered
    return payload


def certificate_status(days_left: int | None, expires_at: str | None) -> str | None:
    if not expires_at or days_left is None:
        return None
    if days_left < 0:
        return "expired"
    if days_left == 0:
        return "expires today"
    if days_left == 1:
        return "1 day left"
    return f"{days_left} days left"


def duration_seconds(started_at: str | None, ended_at: datetime | None = None) -> int | None:
    started = parse_iso(started_at)
    if started is None:
        return None
    finish = ended_at or utc_now()
    return max(0, int((finish - started).total_seconds()))


def human_duration(total_seconds: int | None) -> str:
    if total_seconds is None:
        return "unknown"
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def certificate_days_left(expires_at: datetime) -> int:
    delta = expires_at - utc_now()
    return math.floor(delta.total_seconds() / 86400)
