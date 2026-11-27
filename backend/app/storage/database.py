"""SQLite storage with a minimal migration mechanism (PRAGMA user_version).

Durability choices for a device that may lose power at any moment:
* WAL journal + synchronous=NORMAL: a power cut loses at most the last few
  transactions, never corrupts the database file.
* UNIQUE(boot_id, sequence): duplicate messages are ignored, not stored twice.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models.schemas import Measurement, SystemConfig

MIGRATIONS: list[str] = [
    # v1 — initial schema
    """
    CREATE TABLE measurements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp_utc TEXT NOT NULL,            -- ISO 8601, UTC
        boot_id TEXT NOT NULL,
        sequence INTEGER NOT NULL,
        voltage_v REAL,
        current_a REAL,
        power_w REAL,
        panel_temperature_c REAL,
        ambient_temperature_c REAL,
        case_temperature_c REAL,
        battery_voltage_v REAL,
        battery_current_a REAL,
        fan_state INTEGER,                      -- 0/1/NULL
        sensor_status TEXT NOT NULL,
        sensor_states TEXT NOT NULL DEFAULT '{}',  -- JSON object
        quality_flags TEXT NOT NULL DEFAULT '[]',  -- JSON array
        data_source TEXT NOT NULL CHECK (data_source IN ('simulation', 'hardware')),
        UNIQUE (boot_id, sequence)
    );
    CREATE INDEX idx_measurements_time ON measurements (timestamp_utc);

    CREATE TABLE system_config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL,                    -- JSON value
        updated_utc TEXT NOT NULL
    );

    CREATE TABLE energy_estimates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_utc TEXT NOT NULL,
        method TEXT NOT NULL,
        installed_kwp REAL NOT NULL,
        daily_energy_kwh REAL NOT NULL,
        request_json TEXT NOT NULL,
        response_json TEXT NOT NULL,
        data_source TEXT NOT NULL
    );

    CREATE TABLE device_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp_utc TEXT NOT NULL,
        level TEXT NOT NULL CHECK (level IN ('info', 'warning', 'error')),
        kind TEXT NOT NULL,
        message TEXT NOT NULL
    );
    CREATE INDEX idx_events_time ON device_events (timestamp_utc);

    CREATE TABLE sensor_errors (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp_utc TEXT NOT NULL,
        sensor TEXT NOT NULL,
        error TEXT NOT NULL,
        measurement_id INTEGER REFERENCES measurements (id) ON DELETE SET NULL,
        measurement_sequence INTEGER
    );
    CREATE INDEX idx_sensor_errors_time ON sensor_errors (timestamp_utc);
    """,
]

MEASUREMENT_COLUMNS = [
    "timestamp_utc", "boot_id", "sequence", "voltage_v", "current_a", "power_w",
    "panel_temperature_c", "ambient_temperature_c", "case_temperature_c",
    "battery_voltage_v", "battery_current_a", "fan_state", "sensor_status",
    "sensor_states", "quality_flags", "data_source",
]


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="milliseconds")


def _now() -> str:
    return _iso(datetime.now(UTC))


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self.migrate()

    # ------------------------------------------------------------------ infra
    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def schema_version(self) -> int:
        return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self) -> None:
        with self._lock:
            current = self.schema_version()
            for version, sql in enumerate(MIGRATIONS[current:], start=current + 1):
                self._conn.executescript("BEGIN;" + sql + f"PRAGMA user_version = {version};COMMIT;")

    def ping(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("SELECT 1").fetchone()
                return self._conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        except sqlite3.Error:
            return False

    # ----------------------------------------------------------- measurements
    def insert_measurement(self, m: Measurement) -> int | None:
        """Returns the new row id, or None if (boot_id, sequence) already exists."""
        row = (
            _iso(m.timestamp_utc), m.boot_id, m.sequence, m.voltage_v, m.current_a, m.power_w,
            m.panel_temperature_c, m.ambient_temperature_c, m.case_temperature_c,
            m.battery_voltage_v, m.battery_current_a,
            None if m.fan_state is None else int(m.fan_state),
            m.sensor_status, json.dumps(m.sensor_states), json.dumps(m.quality_flags),
            m.data_source,
        )
        placeholders = ",".join("?" * len(MEASUREMENT_COLUMNS))
        with self._tx() as c:
            cur = c.execute(
                f"INSERT OR IGNORE INTO measurements ({','.join(MEASUREMENT_COLUMNS)}) "
                f"VALUES ({placeholders})",
                row,
            )
            return cur.lastrowid if cur.rowcount == 1 else None

    @staticmethod
    def _row_to_measurement(r: sqlite3.Row) -> Measurement:
        d = dict(r)
        d.pop("id", None)
        d["sensor_states"] = json.loads(d["sensor_states"])
        d["quality_flags"] = json.loads(d["quality_flags"])
        d["fan_state"] = None if d["fan_state"] is None else bool(d["fan_state"])
        return Measurement(**d)

    def _where(
        self, start: datetime | None, end: datetime | None, source: str | None
    ) -> tuple[str, list[Any]]:
        clauses, params = [], []
        if start is not None:
            clauses.append("timestamp_utc >= ?")
            params.append(_iso(start))
        if end is not None:
            clauses.append("timestamp_utc < ?")
            params.append(_iso(end))
        if source is not None:
            clauses.append("data_source = ?")
            params.append(source)
        return ("WHERE " + " AND ".join(clauses)) if clauses else "", params

    def latest_measurement(self, source: str | None = None) -> Measurement | None:
        where, params = self._where(None, None, source)
        with self._lock:
            r = self._conn.execute(
                f"SELECT * FROM measurements {where} ORDER BY timestamp_utc DESC, id DESC LIMIT 1",
                params,
            ).fetchone()
        return self._row_to_measurement(r) if r else None

    def query_measurements(
        self,
        start: datetime | None = None,
        end: datetime | None = None,
        source: str | None = None,
        limit: int | None = None,
        offset: int = 0,
        descending: bool = False,
    ) -> list[Measurement]:
        where, params = self._where(start, end, source)
        order = "DESC" if descending else "ASC"
        sql = f"SELECT * FROM measurements {where} ORDER BY timestamp_utc {order}, id {order}"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params += [limit, offset]
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_measurement(r) for r in rows]

    def count_measurements(
        self, start: datetime | None = None, end: datetime | None = None, source: str | None = None
    ) -> int:
        where, params = self._where(start, end, source)
        with self._lock:
            return int(
                self._conn.execute(f"SELECT COUNT(*) FROM measurements {where}", params).fetchone()[0]
            )

    # ---------------------------------------------------------------- config
    def load_config(self) -> SystemConfig:
        with self._lock:
            rows = self._conn.execute("SELECT key, value FROM system_config").fetchall()
        stored = {r["key"]: json.loads(r["value"]) for r in rows}
        known = {k: v for k, v in stored.items() if k in SystemConfig.model_fields}
        try:
            return SystemConfig(**known)
        except ValueError:
            # Corrupt/inconsistent stored config -> safe defaults, logged by caller
            return SystemConfig()

    def save_config(self, cfg: SystemConfig) -> None:
        now = _now()
        with self._tx() as c:
            for key, value in cfg.model_dump().items():
                c.execute(
                    "INSERT INTO system_config (key, value, updated_utc) VALUES (?, ?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_utc=excluded.updated_utc",
                    (key, json.dumps(value), now),
                )

    # -------------------------------------------------------- events / errors
    def add_event(self, level: str, kind: str, message: str, when: datetime | None = None) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO device_events (timestamp_utc, level, kind, message) VALUES (?, ?, ?, ?)",
                (_iso(when) if when else _now(), level, kind, message[:500]),
            )

    def add_sensor_error(
        self, when: datetime, sensor: str, error: str,
        measurement_id: int | None, sequence: int | None,
    ) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO sensor_errors (timestamp_utc, sensor, error, measurement_id, "
                "measurement_sequence) VALUES (?, ?, ?, ?, ?)",
                (_iso(when), sensor, error[:300], measurement_id, sequence),
            )

    def recent_events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM device_events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def recent_sensor_errors(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, timestamp_utc, sensor, error, measurement_sequence "
                "FROM sensor_errors ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------- estimates
    def save_estimate(
        self, method: str, installed_kwp: float, daily_kwh: float,
        request: dict[str, Any], response: dict[str, Any], data_source: str,
    ) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO energy_estimates (created_utc, method, installed_kwp, daily_energy_kwh, "
                "request_json, response_json, data_source) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (_now(), method, installed_kwp, daily_kwh, json.dumps(request),
                 json.dumps(response, default=str), data_source),
            )
            return int(cur.lastrowid or 0)
