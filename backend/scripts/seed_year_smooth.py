#!/usr/bin/env python3
"""Seed 12 months of smooth, realistic solar field data into SQLite (`simulation`).

Generates 365 days ending today at 30-minute resolution during daylight hours
(~8,800 rows) with clean parabolic daily sun arcs and seasonal annual variation,
so Günlük / Haftalık / Aylık / Yıllık calendar views work out of the box.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import SCHEMA_VERSION, Settings  # noqa: E402
from app.services.solar_position import sun_elevation_deg  # noqa: E402
from app.services.validator import validate_message  # noqa: E402
from app.storage.database import MEASUREMENT_COLUMNS, Database  # noqa: E402


def seed_smooth_year(settings: Settings, days: int = 365, step_min: int = 30) -> int:
    db = Database(settings.db_path)
    with db._tx() as conn:
        conn.execute("DELETE FROM measurements WHERE data_source = 'simulation'")

    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(days=days)
    step = timedelta(minutes=step_min)

    lat, lon = settings.latitude_deg, settings.longitude_deg
    seq = 0
    boot_id = "yearsim1"
    rows_to_insert = []

    t = start
    while t <= now:
        elev = sun_elevation_deg(t, lat, lon)
        if elev > -2.0:
            seq += 1
            doy = t.timetuple().tm_yday
            # Gentle multi-day weather envelope (0.72..0.98) — smooth, never spiky!
            weather = 0.86 + 0.11 * math.sin(2 * math.pi * doy / 9.5) * math.cos(2 * math.pi * doy / 23.0)
            if elev > 0:
                s = math.sin(math.radians(elev))
                ghi = 1060.0 * s * math.exp(-0.057 / max(0.05, s)) * weather
            else:
                ghi = 0.0

            local_hour = (t.hour + t.minute / 60.0 + lon / 15.0) % 24.0
            seasonal_mean = 14.5 - 10.5 * math.cos(2 * math.pi * (doy - 20) / 365.0)
            # Smooth daily ambient parabola peaking ~14:30 local time
            t_amb = seasonal_mean - 4.5 * math.cos(2 * math.pi * (local_hour - 2.5) / 24.0)
            t_panel = t_amb + (ghi / 800.0) * 23.0
            t_case = t_amb + 3.0 + (ghi / 1000.0) * 9.0
            fan = t_case >= 40.0

            isc = 0.150 * (ghi / 1000.0) * (1.0 + 0.0005 * (t_panel - 25.0))
            v_ref = isc * 0.30
            soc = 68.0 + 22.0 * math.sin(math.pi * max(0.0, min(1.0, (local_hour - 6.0) / 12.0)))
            batt_v = 10.8 + (soc / 100.0) * 1.65
            batt_i = (20.0 * (ghi / 1000.0) * 0.78 - 2.6) / 11.4

            raw = {
                "schema_version": SCHEMA_VERSION,
                "boot_id": boot_id,
                "sequence": seq,
                "uptime_ms": seq * step_min * 60 * 1000,
                "voltage_v": round(v_ref, 4),
                "current_a": round(isc, 5),
                "power_w": round(v_ref * isc, 6),
                "panel_temperature_c": round(t_panel, 2),
                "ambient_temperature_c": round(t_amb, 2),
                "case_temperature_c": round(t_case, 2),
                "battery_voltage_v": round(batt_v, 3),
                "battery_current_a": round(batt_i, 3),
                "fan_state": fan,
                "sensor_status": "OK",
                "sensors": {
                    "ina_ref": "OK",
                    "ina_batt": "OK",
                    "t_panel": "OK",
                    "t_ambient": "OK",
                    "t_case": "OK",
                },
            }
            m = validate_message(raw, received_at=t, data_source="simulation").measurement
            rows_to_insert.append(m)
        t += step

    placeholders = ",".join("?" * len(MEASUREMENT_COLUMNS))
    sql = f"INSERT OR IGNORE INTO measurements ({','.join(MEASUREMENT_COLUMNS)}) VALUES ({placeholders})"
    tuples = [
        (
            m.timestamp_utc.astimezone(UTC).isoformat(timespec="milliseconds"),
            m.boot_id,
            m.sequence,
            m.voltage_v,
            m.current_a,
            m.power_w,
            m.panel_temperature_c,
            m.ambient_temperature_c,
            m.case_temperature_c,
            m.heatsink_temperature_c,
            m.battery_temperature_c,
            m.mcu_temperature_c,
            m.mpu_temperature_c,
            m.surface_temperature_c,
            m.humidity_pct,
            m.pressure_hpa,
            m.wind_speed_ms,
            m.irradiance_measured_wm2,
            m.battery_voltage_v,
            m.battery_current_a,
            None if m.fan_state is None else int(m.fan_state),
            m.sensor_status,
            json.dumps(m.sensor_states),
            json.dumps(m.extra_sensors),
            json.dumps(m.quality_flags),
            m.data_source,
        )
        for m in rows_to_insert
    ]
    with db._tx() as conn:
        conn.executemany(sql, tuples)
    db.close()
    return len(rows_to_insert)


def main() -> None:
    p = argparse.ArgumentParser(description="Seed 12-month smooth parabolic dataset")
    p.add_argument("--days", type=int, default=365)
    p.add_argument("--step-min", type=int, default=30)
    args = p.parse_args()
    s = Settings.from_env()
    n = seed_smooth_year(s, days=args.days, step_min=args.step_min)
    print(f"Seeded {n} smooth simulation measurements over {args.days} days into {s.db_path}")


if __name__ == "__main__":
    main()
