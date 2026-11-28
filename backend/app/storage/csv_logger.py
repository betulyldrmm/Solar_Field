"""Append-only CSV log, one file per UTC day (measurements_YYYY-MM-DD.csv).

CSV is the human-readable backup next to SQLite. Daily files limit the damage
of a corrupted file to one day, and make copying data off the device easy.
"""

from __future__ import annotations

import csv
import io
import json
import os
import shutil
from collections.abc import Iterable
from pathlib import Path

from app.models.schemas import Measurement

CSV_FIELDS = [
    "timestamp_utc", "sequence", "voltage_v", "current_a", "power_w",
    "panel_temperature_c", "ambient_temperature_c", "case_temperature_c",
    "heatsink_temperature_c", "battery_temperature_c", "mcu_temperature_c",
    "mpu_temperature_c", "surface_temperature_c", "humidity_pct", "pressure_hpa",
    "wind_speed_ms", "irradiance_measured_wm2",
    "battery_voltage_v", "battery_current_a", "fan_state", "sensor_status",
    "data_source", "boot_id", "quality_flags", "extra_sensors",
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
        "heatsink_temperature_c": fmt(m.heatsink_temperature_c),
        "battery_temperature_c": fmt(m.battery_temperature_c),
        "mcu_temperature_c": fmt(m.mcu_temperature_c),
        "mpu_temperature_c": fmt(m.mpu_temperature_c),
        "surface_temperature_c": fmt(m.surface_temperature_c),
        "humidity_pct": fmt(m.humidity_pct),
        "pressure_hpa": fmt(m.pressure_hpa),
        "wind_speed_ms": fmt(m.wind_speed_ms),
        "irradiance_measured_wm2": fmt(m.irradiance_measured_wm2),
        "battery_voltage_v": fmt(m.battery_voltage_v),
        "battery_current_a": fmt(m.battery_current_a),
        "fan_state": "" if m.fan_state is None else ("1" if m.fan_state else "0"),
        "sensor_status": m.sensor_status,
        "data_source": m.data_source,
        "boot_id": m.boot_id,
        "quality_flags": "|".join(m.quality_flags),
        "extra_sensors": json.dumps(m.extra_sensors, separators=(",", ":")) if m.extra_sensors else "",
    }


def to_csv_text(measurements: Iterable[Measurement], *, expand_extra: bool = True) -> str:
    items = list(measurements)
    extra_keys: list[str] = []
    if expand_extra:
        seen: set[str] = set()
        for m in items:
            for k in sorted(m.extra_sensors):
                if k not in CSV_FIELDS and k not in seen:
                    seen.add(k)
                    extra_keys.append(k)
    fields = CSV_FIELDS + [f"extra.{k}" for k in extra_keys]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    w.writeheader()
    for m in items:
        row = measurement_to_row(m)
        for k in extra_keys:
            v = m.extra_sensors.get(k)
            row[f"extra.{k}"] = "" if v is None else f"{v:.6g}"
        w.writerow(row)
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
