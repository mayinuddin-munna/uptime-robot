from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Iterator


@dataclass(slots=True)
class CheckResult:
    monitor_name: str
    url: str
    checked_at: str
    ok: bool
    status_code: int | None
    latency_ms: float | None
    error: str | None


class Storage:
    def __init__(self, data_dir: str) -> None:
        self._path = Path(data_dir)
        self._path.mkdir(parents=True, exist_ok=True)
        self._db_path = self._path / "uptime_robot.db"
        self._lock = Lock()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._db_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS monitors (
                    name TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    last_checked_at TEXT,
                    last_status_code INTEGER,
                    last_latency_ms REAL,
                    status TEXT NOT NULL DEFAULT 'unknown',
                    last_error TEXT,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    uptime_24h REAL NOT NULL DEFAULT 100.0
                );

                CREATE TABLE IF NOT EXISTS checks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    monitor_name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    checked_at TEXT NOT NULL,
                    ok INTEGER NOT NULL,
                    status_code INTEGER,
                    latency_ms REAL,
                    error TEXT
                );

                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    monitor_name TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    resolved_at TEXT,
                    status TEXT NOT NULL,
                    summary TEXT NOT NULL
                );
                """
            )
            connection.commit()

    def ensure_monitor(self, name: str, url: str) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO monitors (name, url)
                VALUES (?, ?)
                ON CONFLICT(name) DO UPDATE SET url = excluded.url
                """,
                (name, url),
            )
            connection.commit()

    def record_check(self, result: CheckResult) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO checks (monitor_name, url, checked_at, ok, status_code, latency_ms, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.monitor_name,
                    result.url,
                    result.checked_at,
                    int(result.ok),
                    result.status_code,
                    result.latency_ms,
                    result.error,
                ),
            )

            last_status = "up" if result.ok else "down"
            previous = connection.execute(
                "SELECT status, consecutive_failures FROM monitors WHERE name = ?",
                (result.monitor_name,),
            ).fetchone()
            previous_status = previous["status"] if previous else "unknown"
            previous_failures = previous["consecutive_failures"] if previous else 0
            consecutive_failures = 0 if result.ok else previous_failures + 1
            uptime = self._calculate_uptime(connection, result.monitor_name)

            connection.execute(
                """
                UPDATE monitors
                SET last_checked_at = ?,
                    last_status_code = ?,
                    last_latency_ms = ?,
                    status = ?,
                    last_error = ?,
                    consecutive_failures = ?,
                    uptime_24h = ?
                WHERE name = ?
                """,
                (
                    result.checked_at,
                    result.status_code,
                    result.latency_ms,
                    last_status,
                    result.error,
                    consecutive_failures,
                    uptime,
                    result.monitor_name,
                ),
            )

            self._update_incidents(connection, result, previous_status)
            connection.commit()

    def _calculate_uptime(self, connection: sqlite3.Connection, monitor_name: str) -> float:
        since = datetime.now(UTC).replace(microsecond=0)
        window_start = since.timestamp() - 86400
        rows = connection.execute(
            """
            SELECT ok FROM checks
            WHERE monitor_name = ? AND strftime('%s', checked_at) >= ?
            """,
            (monitor_name, int(window_start)),
        ).fetchall()
        if not rows:
            return 100.0
        success = sum(1 for row in rows if row[0] == 1)
        return round((success / len(rows)) * 100, 2)

    def _update_incidents(
        self,
        connection: sqlite3.Connection,
        result: CheckResult,
        previous_status: str,
    ) -> None:
        if previous_status != "down" and not result.ok:
            summary = result.error or f"Expected {result.status_code} to be healthy"
            connection.execute(
                """
                INSERT INTO incidents (monitor_name, started_at, status, summary)
                VALUES (?, ?, 'open', ?)
                """,
                (result.monitor_name, result.checked_at, summary),
            )
        elif previous_status == "down" and result.ok:
            connection.execute(
                """
                UPDATE incidents
                SET resolved_at = ?, status = 'resolved'
                WHERE monitor_name = ? AND status = 'open'
                """,
                (result.checked_at, result.monitor_name),
            )

    def get_summary(self) -> list[dict]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT name, url, status, last_checked_at, last_status_code,
                       last_latency_ms, last_error, consecutive_failures, uptime_24h
                FROM monitors
                ORDER BY name ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_recent_checks(self, limit: int = 100) -> list[dict]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT monitor_name, url, checked_at, ok, status_code, latency_ms, error
                FROM checks
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_checks_for_monitor(self, monitor_name: str, limit: int = 50) -> list[dict]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT monitor_name, url, checked_at, ok, status_code, latency_ms, error
                FROM checks
                WHERE monitor_name = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (monitor_name, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_incidents(self, limit: int = 50) -> list[dict]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, monitor_name, started_at, resolved_at, status, summary
                FROM incidents
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]
