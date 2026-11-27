"""Append-only CSV log, one file per UTC day (measurements_YYYY-MM-DD.csv).

CSV is the human-readable backup next to SQLite. Daily files limit the damage
of a corrupted file to one day, and make copying data off the device easy.
"""

from __future__ import annotations

import csv
import io
import os
import shutil
from collections.abc import Iterable
from pathlib import Path

from app.models.schemas import Measurement

CSV_FIELDS = [
    "timestamp_utc", "sequence", "voltage_v", "current_a", "power_w",
    "panel_temperature_c", "ambient_temperature_c", "case_temperature_c",
    "battery_voltage_v", "battery_current_a", "fan_state", "sensor_status",
    "data_source", "boot_id", "quality_flags",
]


class DiskSpaceLow(OSError):
    pass


def measurement_to_row(m: Measurement) -> dict[str, str]:
    def fmt(v: float | None) -> str:
        return "" if v is None else f"{v:.6g}"

    return {
        "timestamp_utc": m.timestamp_utc.isoformat(timespec="milliseconds"),
        "sequence": str(m.sequence),
        "voltage_v": fmt(m.voltage_v),
        "current_a": fmt(m.current_a),
        "power_w": fmt(m.power_w),
        "panel_temperature_c": fmt(m.panel_temperature_c),
        "ambient_temperature_c": fmt(m.ambient_temperature_c),
        "case_temperature_c": fmt(m.case_temperature_c),
        "battery_voltage_v": fmt(m.battery_voltage_v),
        "battery_current_a": fmt(m.battery_current_a),
        "fan_state": "" if m.fan_state is None else ("1" if m.fan_state else "0"),
        "sensor_status": m.sensor_status,
        "data_source": m.data_source,
        "boot_id": m.boot_id,
        "quality_flags": "|".join(m.quality_flags),
    }


def to_csv_text(measurements: Iterable[Measurement]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, lineterminator="\n")
    w.writeheader()
    for m in measurements:
        w.writerow(measurement_to_row(m))
    return buf.getvalue()


class CsvLogger:
    def __init__(self, directory: Path, fsync_every: int = 12, min_free_mb: int = 200):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.fsync_every = max(1, fsync_every)
        self.min_free_mb = min_free_mb
        self._pending = 0

    def path_for(self, m: Measurement) -> Path:
        return self.directory / f"measurements_{m.timestamp_utc.date().isoformat()}.csv"

    def free_mb(self) -> float:
        return shutil.disk_usage(self.directory).free / (1024 * 1024)

    def append(self, m: Measurement) -> Path:
        if self.free_mb() < self.min_free_mb:
            raise DiskSpaceLow(f"free disk below {self.min_free_mb} MB, CSV write skipped")
        path = self.path_for(m)
        new_file = not path.exists() or path.stat().st_size == 0
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=CSV_FIELDS, lineterminator="\n")
            if new_file:
                w.writeheader()
            w.writerow(measurement_to_row(m))
            self._pending += 1
            if new_file or self._pending >= self.fsync_every:
                f.flush()
                os.fsync(f.fileno())
                self._pending = 0
        return path
