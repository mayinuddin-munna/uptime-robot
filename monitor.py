from __future__ import annotations

import logging
import signal
import smtplib
import socket
import ssl
import threading
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from email.message import EmailMessage
from urllib.parse import urlparse

import requests

from uptime_robot.common import (
    Settings,
    append_history_entry,
    atomic_write_json,
    certificate_days_left,
    configure_logging,
    default_history_payload,
    default_state_payload,
    duration_seconds,
    ensure_runtime_files,
    ensure_site_state,
    human_duration,
    load_history,
    load_settings,
    load_sites,
    load_state,
    parse_iso,
    to_iso,
    utc_now,
)


@dataclass(slots=True)
class CheckResult:
    name: str
    url: str
    ok: bool
    checked_at: str
    latency_ms: float | None
    status_code: int | None
    error: str | None
    expected_status: list[int]
    expected_text: str | None
    cert_expires_at: str | None = None
    cert_days_left: int | None = None
    cert_error: str | None = None


class EmailAlertSender:
    def __init__(self, settings: Settings, logger: logging.Logger) -> None:
        self.settings = settings
        self.logger = logger

    def enabled(self) -> bool:
        return bool(
            self.settings.smtp_host
            and self.settings.alert_from_email
            and self.settings.alert_to_emails
        )

    def send(self, subject: str, body: str) -> None:
        if not self.enabled():
            self.logger.info("Email alert skipped because SMTP is not fully configured")
            return

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.settings.alert_from_email
        message["To"] = ", ".join(self.settings.alert_to_emails)
        message.set_content(body)

        try:
            if self.settings.smtp_use_ssl:
                with smtplib.SMTP_SSL(
                    self.settings.smtp_host,
                    self.settings.smtp_port,
                    timeout=20,
                ) as smtp:
                    self._login(smtp)
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(
                    self.settings.smtp_host,
                    self.settings.smtp_port,
                    timeout=20,
                ) as smtp:
                    if self.settings.smtp_use_tls:
                        smtp.starttls(context=ssl.create_default_context())
                    self._login(smtp)
                    smtp.send_message(message)
        except Exception:
            self.logger.exception("Failed to send email alert")

    def _login(self, smtp: smtplib.SMTP) -> None:
        if self.settings.smtp_username and self.settings.smtp_password:
            smtp.login(self.settings.smtp_username, self.settings.smtp_password)


class MonitorService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = configure_logging(settings.log_file, logger_name="uptime_monitor")
        self.alerts = EmailAlertSender(settings, self.logger)
        ensure_runtime_files(settings)
        self.state = self._safe_load_state()
        self.history = self._safe_load_history()
        self._stop_event = threading.Event()
        self._last_known_sites: list = []

    def run_forever(self) -> None:
        self.logger.info("Monitor starting with sites file %s", self.settings.sites_file.resolve())
        while not self._stop_event.is_set():
            cycle_started = time.monotonic()
            try:
                self._run_cycle()
            except Exception as exc:
                self.logger.exception("Monitoring loop crashed")
                self.alerts.send(
                    "[Uptime Robot] Monitor crash detected",
                    (
                        "The monitoring loop crashed and will retry automatically.\n\n"
                        f"Error: {exc}\n"
                        f"Time: {to_iso(utc_now())}\n"
                    ),
                )
                if self._stop_event.wait(self.settings.crash_retry_delay_seconds):
                    break
                continue

            elapsed = time.monotonic() - cycle_started
            sleep_for = max(1.0, self.settings.check_interval_seconds - elapsed)
            if self._stop_event.wait(sleep_for):
                break

        self.logger.info("Monitor stopped")

    def stop(self) -> None:
        self._stop_event.set()

    def _run_cycle(self) -> None:
        sites = self._load_sites_with_fallback()
        enabled_sites = [site for site in sites if site.enabled]
        if not enabled_sites:
            self.logger.info("No enabled sites found in %s", self.settings.sites_file)
            self._persist()
            return

        max_workers = min(self.settings.max_workers, len(enabled_sites))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self._check_site, site): site for site in enabled_sites}
            for future in as_completed(futures):
                site = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    self.logger.exception("Unexpected worker failure for %s", site.name)
                    result = CheckResult(
                        name=site.name,
                        url=site.url,
                        ok=False,
                        checked_at=to_iso(utc_now()),
                        latency_ms=None,
                        status_code=None,
                        error=f"Worker failure: {exc}",
                        expected_status=site.expected_status,
                        expected_text=site.expected_text,
                    )
                self._apply_result(site, result)

        self._persist()

    def _load_sites_with_fallback(self):
        try:
            sites = load_sites(self.settings.sites_file)
            self._last_known_sites = sites
            return sites
        except Exception:
            self.logger.exception("Failed to load %s", self.settings.sites_file)
            return self._last_known_sites

    def _check_site(self, site) -> CheckResult:
        attempts = self.settings.retry_count + 1
        last_result: CheckResult | None = None

        for attempt in range(1, attempts + 1):
            result = self._perform_single_http_check(site)
            self._attach_certificate_status(site, result)
            last_result = result
            if result.ok:
                break
            if attempt < attempts and not self._stop_event.wait(self.settings.retry_backoff_seconds * attempt):
                self.logger.warning(
                    "Retrying %s after failure (%s/%s): %s",
                    site.name,
                    attempt,
                    attempts,
                    result.error or "unknown error",
                )

        assert last_result is not None
        return last_result

    def _perform_single_http_check(self, site) -> CheckResult:
        started = time.perf_counter()
        checked_at = to_iso(utc_now())
        status_code = None
        error_message = None
        latency_ms: float | None = None
        try:
            response = requests.get(
                site.url,
                timeout=self.settings.request_timeout_seconds,
                allow_redirects=True,
                verify=True,
                headers={"User-Agent": "uptime-robot/2.0"},
            )
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            status_code = response.status_code
            if status_code not in site.expected_status:
                error_message = f"Expected HTTP {site.expected_status}, received {status_code}"
            elif site.expected_text and site.expected_text not in response.text:
                error_message = f"Expected text not found: {site.expected_text!r}"

            return CheckResult(
                name=site.name,
                url=site.url,
                ok=error_message is None,
                checked_at=checked_at,
                latency_ms=latency_ms,
                status_code=status_code,
                error=error_message,
                expected_status=site.expected_status,
                expected_text=site.expected_text,
            )
        except requests.RequestException as exc:
            latency_ms = round((time.perf_counter() - started) * 1000, 2)
            return CheckResult(
                name=site.name,
                url=site.url,
                ok=False,
                checked_at=checked_at,
                latency_ms=latency_ms,
                status_code=status_code,
                error=str(exc),
                expected_status=site.expected_status,
                expected_text=site.expected_text,
            )

    def _attach_certificate_status(self, site, result: CheckResult) -> None:
        if not site.check_cert or not site.is_https:
            result.cert_expires_at = None
            result.cert_days_left = None
            result.cert_error = None
            return

        try:
            expires_at = fetch_certificate_expiry(site.url, self.settings.request_timeout_seconds)
            result.cert_expires_at = to_iso(expires_at)
            result.cert_days_left = certificate_days_left(expires_at)
            result.cert_error = None
        except Exception as exc:
            result.cert_error = str(exc)
            self.logger.warning("Certificate check failed for %s: %s", site.name, exc)

    def _apply_result(self, site, result: CheckResult) -> None:
        site_state = ensure_site_state(self.state, site.name)
        previous_status = site_state.get("status", "unknown")
        current_status = "up" if result.ok else "down"
        checked_at = result.checked_at

        site_state["status"] = current_status
        site_state["last_check"] = checked_at
        site_state["last_error"] = result.error
        site_state["last_http_status"] = result.status_code
        site_state["last_latency_ms"] = result.latency_ms
        site_state["cert_error"] = result.cert_error

        if result.cert_expires_at:
            site_state["cert_expires_at"] = result.cert_expires_at
            site_state["cert_days_left"] = result.cert_days_left

        if previous_status != current_status:
            site_state["last_change"] = checked_at

        if current_status == "down":
            if previous_status != "down":
                site_state["down_since"] = checked_at
                site_state["last_down_alert_at"] = checked_at
                self._send_down_alert(site, result)
        else:
            if previous_status == "down":
                site_state["last_recovery_alert_at"] = checked_at
                self._send_recovery_alert(site, result, site_state.get("down_since"))
                site_state["down_since"] = None
            elif previous_status == "unknown":
                site_state["down_since"] = None

        self._maybe_send_latency_alert(site, result, site_state)
        self._maybe_send_cert_alert(site, result, site_state)

        append_history_entry(
            self.history,
            site.name,
            {
                "checked_at": checked_at,
                "ok": result.ok,
                "latency_ms": result.latency_ms,
                "status_code": result.status_code,
                "error": result.error,
            },
            retention_days=self.settings.history_retention_days,
        )
        self.logger.info(
            "Checked %s | status=%s | latency_ms=%s | code=%s | error=%s",
            site.name,
            current_status,
            result.latency_ms,
            result.status_code,
            result.error,
        )

    def _maybe_send_latency_alert(self, site, result: CheckResult, site_state: dict[str, object]) -> None:
        threshold = site.latency_threshold_ms
        if threshold is None or result.latency_ms is None or not result.ok:
            return
        if result.latency_ms <= threshold:
            return

        now = parse_iso(result.checked_at) or utc_now()
        last_alert = parse_iso(site_state.get("last_latency_alert_at"))
        cooldown_seconds = self.settings.latency_alert_cooldown_minutes * 60
        if last_alert and (now - last_alert).total_seconds() < cooldown_seconds:
            return

        site_state["last_latency_alert_at"] = result.checked_at
        self.alerts.send(
            f"[Uptime Robot] High latency: {site.name}",
            (
                f"Site: {site.name}\n"
                f"URL: {site.url}\n"
                f"Observed latency: {result.latency_ms} ms\n"
                f"Threshold: {threshold} ms\n"
                f"Checked at: {result.checked_at}\n"
            ),
        )

    def _maybe_send_cert_alert(self, site, result: CheckResult, site_state: dict[str, object]) -> None:
        if not site.check_cert or not site.is_https:
            return
        if result.cert_days_left is None or result.cert_expires_at is None:
            return
        if result.cert_days_left > site.cert_warn_days:
            site_state["last_cert_alert_date"] = None
            return

        today = utc_now().date().isoformat()
        if site_state.get("last_cert_alert_date") == today:
            return

        site_state["last_cert_alert_date"] = today
        self.alerts.send(
            f"[Uptime Robot] SSL certificate warning: {site.name}",
            (
                f"Site: {site.name}\n"
                f"URL: {site.url}\n"
                f"Certificate expires at: {result.cert_expires_at}\n"
                f"Days left: {result.cert_days_left}\n"
                f"Warning threshold: {site.cert_warn_days} days\n"
            ),
        )

    def _send_down_alert(self, site, result: CheckResult) -> None:
        self.alerts.send(
            f"[Uptime Robot] DOWN: {site.name}",
            (
                f"Site: {site.name}\n"
                f"URL: {site.url}\n"
                f"Time: {result.checked_at}\n"
                f"Failure reason: {result.error or 'unknown'}\n"
                f"Status code: {result.status_code}\n"
                f"Latency: {result.latency_ms} ms\n"
            ),
        )

    def _send_recovery_alert(self, site, result: CheckResult, down_since: str | None) -> None:
        downtime_seconds = duration_seconds(down_since, ended_at=parse_iso(result.checked_at) or utc_now())
        self.alerts.send(
            f"[Uptime Robot] RECOVERED: {site.name}",
            (
                f"Site: {site.name}\n"
                f"URL: {site.url}\n"
                f"Recovered at: {result.checked_at}\n"
                f"Downtime: {human_duration(downtime_seconds)}\n"
                f"Status code: {result.status_code}\n"
                f"Latency: {result.latency_ms} ms\n"
            ),
        )

    def _safe_load_state(self) -> dict:
        try:
            return load_state(self.settings.state_file)
        except Exception:
            self.logger.exception("Failed to load state file; starting with empty state")
            return default_state_payload()

    def _safe_load_history(self) -> dict:
        try:
            return load_history(self.settings.history_file)
        except Exception:
            self.logger.exception("Failed to load history file; starting with empty history")
            return default_history_payload()

    def _persist(self) -> None:
        self.state["updated_at"] = to_iso(utc_now())
        self.history["updated_at"] = to_iso(utc_now())
        atomic_write_json(self.settings.state_file, self.state)
        atomic_write_json(self.settings.history_file, self.history)


def fetch_certificate_expiry(url: str, timeout_seconds: int) -> datetime:
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        raise ValueError("URL does not contain a hostname")
    port = parsed.port or 443

    pem_certificate = ssl.get_server_certificate((host, port), timeout=timeout_seconds)
    with tempfile.NamedTemporaryFile("w+", encoding="utf-8") as temp_file:
        temp_file.write(pem_certificate)
        temp_file.flush()
        cert = ssl._ssl._test_decode_cert(temp_file.name)

    not_after = cert.get("notAfter")
    if not not_after:
        raise ValueError("Certificate expiry date not found")
    expires_at = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
    return expires_at.replace(tzinfo=UTC)


def main() -> None:
    settings = load_settings()
    service = MonitorService(settings)

    def shutdown(*_: object) -> None:
        service.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    service.run_forever()


if __name__ == "__main__":
    main()
