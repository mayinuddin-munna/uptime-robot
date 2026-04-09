from __future__ import annotations

import json
import socket
import ssl
import smtplib
import threading
import time
from datetime import UTC, datetime
from email.message import EmailMessage
from string import Template
from urllib import error, request

from .config import AlertConfig, AppConfig, MonitorConfig
from .storage import CheckResult, Storage


class AlertDispatcher:
    def __init__(self, alerts: list[AlertConfig]) -> None:
        self._alerts = alerts

    def send(self, event: str, result: CheckResult) -> None:
        payload = {
            "event": event,
            "monitor": result.monitor_name,
            "url": result.url,
            "target": result.url,
            "checked_at": result.checked_at,
            "ok": result.ok,
            "status_code": result.status_code,
            "latency_ms": result.latency_ms,
            "error": result.error,
        }
        for alert in self._alerts:
            try:
                if alert.type == "email":
                    self._send_email(alert, payload)
                else:
                    self._send_webhook(alert, payload)
            except Exception:
                # Alert transport should never crash monitoring.
                continue

    def _build_body(self, alert: AlertConfig, payload: dict) -> str:
        if alert.body_template:
            return Template(alert.body_template).safe_substitute(payload)
        return json.dumps(payload)

    def _build_subject(self, alert: AlertConfig, payload: dict) -> str:
        template = alert.subject_template or "[${event}] ${monitor} status change"
        return Template(template).safe_substitute(payload)

    def _send_webhook(self, alert: AlertConfig, payload: dict) -> None:
        body = self._build_body(alert, payload)
        data = body.encode("utf-8") if body else None
        req = request.Request(
            url=alert.url or "",
            data=data,
            method=alert.method,
            headers={"Content-Type": "application/json", **alert.headers},
        )
        with request.urlopen(req, timeout=5):
            pass

    def _send_email(self, alert: AlertConfig, payload: dict) -> None:
        message = EmailMessage()
        message["Subject"] = self._build_subject(alert, payload)
        message["From"] = alert.from_email or ""
        message["To"] = ", ".join(alert.to_emails)
        message.set_content(self._build_body(alert, payload))

        if alert.use_ssl:
            with smtplib.SMTP_SSL(alert.smtp_host, alert.smtp_port, timeout=10) as smtp:
                self._smtp_login(alert, smtp)
                smtp.send_message(message)
            return

        with smtplib.SMTP(alert.smtp_host, alert.smtp_port, timeout=10) as smtp:
            if alert.use_tls:
                smtp.starttls(context=ssl.create_default_context())
            self._smtp_login(alert, smtp)
            smtp.send_message(message)

    def _smtp_login(self, alert: AlertConfig, smtp: smtplib.SMTP) -> None:
        if alert.smtp_username and alert.smtp_password:
            smtp.login(alert.smtp_username, alert.smtp_password)


class MonitorService:
    def __init__(self, config: AppConfig, storage: Storage) -> None:
        self._config = config
        self._storage = storage
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self._alerts = AlertDispatcher(config.alerts)

        for monitor in config.monitors:
            self._storage.ensure_monitor(monitor.name, monitor.target)

    def start(self) -> None:
        for monitor in self._config.monitors:
            thread = threading.Thread(
                target=self._run_monitor,
                args=(monitor,),
                name=f"monitor:{monitor.name}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        self._stop_event.set()
        for thread in self._threads:
            thread.join(timeout=2)

    def _run_monitor(self, monitor: MonitorConfig) -> None:
        while not self._stop_event.is_set():
            result = self._check(monitor)
            previous_status = self._storage.get_summary()
            previous_map = {item["name"]: item["status"] for item in previous_status}
            before = previous_map.get(monitor.name, "unknown")
            self._storage.record_check(result)

            if before != "down" and not result.ok:
                self._alerts.send("down", result)
            elif before == "down" and result.ok:
                self._alerts.send("recovered", result)

            if self._stop_event.wait(monitor.interval_seconds):
                break

    def _check(self, monitor: MonitorConfig) -> CheckResult:
        if monitor.type == "tcp":
            return self._check_tcp(monitor)
        return self._check_http(monitor)

    def _check_http(self, monitor: MonitorConfig) -> CheckResult:
        started = time.perf_counter()
        checked_at = datetime.now(UTC).replace(microsecond=0).isoformat()
        data = monitor.body.encode("utf-8") if monitor.body is not None else None
        req = request.Request(
            url=monitor.url or "",
            data=data,
            method=monitor.method,
            headers=monitor.headers,
        )

        ssl_context = None
        if not monitor.verify_ssl:
            ssl_context = ssl._create_unverified_context()

        try:
            with request.urlopen(req, timeout=monitor.timeout_seconds, context=ssl_context) as response:
                latency = round((time.perf_counter() - started) * 1000, 2)
                status_code = getattr(response, "status", None)
                ok = status_code in monitor.expected_status
                error_message = None if ok else f"Unexpected status: {status_code}"
                return CheckResult(
                    monitor_name=monitor.name,
                    url=monitor.target,
                    checked_at=checked_at,
                    ok=ok,
                    status_code=status_code,
                    latency_ms=latency,
                    error=error_message,
                )
        except error.HTTPError as exc:
            latency = round((time.perf_counter() - started) * 1000, 2)
            return CheckResult(
                monitor_name=monitor.name,
                url=monitor.target,
                checked_at=checked_at,
                ok=False,
                status_code=exc.code,
                latency_ms=latency,
                error=f"HTTPError: {exc.code}",
            )
        except Exception as exc:
            latency = round((time.perf_counter() - started) * 1000, 2)
            return CheckResult(
                monitor_name=monitor.name,
                url=monitor.target,
                checked_at=checked_at,
                ok=False,
                status_code=None,
                latency_ms=latency,
                error=str(exc),
            )

    def _check_tcp(self, monitor: MonitorConfig) -> CheckResult:
        started = time.perf_counter()
        checked_at = datetime.now(UTC).replace(microsecond=0).isoformat()
        try:
            with socket.create_connection((monitor.host or "", int(monitor.port or 0)), timeout=monitor.timeout_seconds):
                latency = round((time.perf_counter() - started) * 1000, 2)
                return CheckResult(
                    monitor_name=monitor.name,
                    url=monitor.target,
                    checked_at=checked_at,
                    ok=True,
                    status_code=None,
                    latency_ms=latency,
                    error=None,
                )
        except Exception as exc:
            latency = round((time.perf_counter() - started) * 1000, 2)
            return CheckResult(
                monitor_name=monitor.name,
                url=monitor.target,
                checked_at=checked_at,
                ok=False,
                status_code=None,
                latency_ms=latency,
                error=str(exc),
            )
