"""Ingest pipeline: raw message -> validate -> SQLite (+ sensor errors) -> CSV.

Shared by the simulator and the hardware Bridge adapter, so both paths behave
identically. The Linux side assigns timestamps (its clock is kept by NTP or,
offline, by the DS3231 RTC — see docs/architecture.md)."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.validator import MessageRejected, SensorIssue, validate_message
from app.storage.csv_logger import CsvLogger, DiskSpaceLow
from app.storage.database import Database

log = logging.getLogger(__name__)


@dataclass
class IngestOutcome:
    stored: bool
    duplicate: bool = False
    rejected_reason: str | None = None
    measurement_id: int | None = None
    issues: list[SensorIssue] = field(default_factory=list)


class IngestService:
    def __init__(self, db: Database, csv_logger: CsvLogger | None, data_source: str):
        self.db = db
        self.csv = csv_logger
        self.data_source = data_source
        self._lock = threading.Lock()
        self._last_seq: dict[str, int] = {}
        self._last_disk_warning: datetime | None = None
        self.last_ok_utc: datetime | None = None
        self.duplicates = 0
        self.rejected = 0

    def ingest(self, raw: dict[str, Any] | str | bytes, received_at: datetime | None = None) -> IngestOutcome:
        received_at = received_at or datetime.now(UTC)
        with self._lock:
            try:
                msg = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
                result = validate_message(msg, received_at=received_at, data_source=self.data_source)
            except (MessageRejected, json.JSONDecodeError) as exc:
                self.rejected += 1
                self.db.add_event("warning", "message_rejected", str(exc), received_at)
                return IngestOutcome(stored=False, rejected_reason=str(exc))

            m = result.measurement
            row_id = self.db.insert_measurement(m)
            if row_id is None:
                self.duplicates += 1
                return IngestOutcome(stored=False, duplicate=True)

            prev = self._last_seq.get(m.boot_id)
            if prev is None:
                if self._last_seq:
                    self.db.add_event("info", "device_boot", f"new boot_id {m.boot_id}", received_at)
            elif m.sequence > prev + 1:
                self.db.add_event(
                    "info", "sequence_gap",
                    f"{m.sequence - prev - 1} message(s) missing before seq {m.sequence}", received_at,
                )
            self._last_seq[m.boot_id] = max(m.sequence, prev or 0)

            for issue in result.issues:
                self.db.add_sensor_error(received_at, issue.sensor, issue.error, row_id, m.sequence)

            if "clock_unsynced" in m.quality_flags:
                self.db.add_event("warning", "clock_unsynced", "system clock before 2025", received_at)

            if self.csv is not None:
                try:
                    self.csv.append(m)
                except DiskSpaceLow as exc:
                    if (self._last_disk_warning is None
                            or received_at - self._last_disk_warning > timedelta(hours=1)):
                        self.db.add_event("warning", "disk_low", str(exc), received_at)
                        self._last_disk_warning = received_at
                except OSError as exc:
                    log.exception("CSV write failed")
                    self.db.add_event("error", "csv_write_failed", str(exc), received_at)

            self.last_ok_utc = received_at
            return IngestOutcome(stored=True, measurement_id=row_id, issues=result.issues)
