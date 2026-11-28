"""Tests for multi-sensor thermal channels, dynamic extra_sensors, BLE JSON,
source query isolation, and Bluetooth PAN captive-portal redirects."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.ble.protocol import encode_telemetry_json
from app.main import create_app
from app.services.ingest import IngestService
from app.services.validator import validate_message
from app.storage.database import MIGRATIONS, Database

NOW = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)


def test_thermal_and_dynamic_extra_sensors_validated_and_stored(tmp_path):
    raw = {
        "schema_version": 1,
        "boot_id": "unoq01",
        "sequence": 12,
        "voltage_v": 0.032,
        "current_a": 0.118,
        "panel_temperature_c": 42.6,
        "ambient_temperature_c": 24.1,
        "case_temperature_c": 33.8,
        "heatsink_temperature_c": 47.9,
        "battery_temperature_c": 29.4,
        "mcu_temperature_c": 85.0,  # valid MCU die temp (not a DS18B20!)
        "mpu_temperature_c": 38.5,
        "surface_temperature_c": 51.2,
        "humidity_pct": 44.5,
        "pressure_hpa": 1014.8,
        "wind_speed_ms": 2.3,
        "irradiance_measured_wm2": 790.0,
        "extra_sensors": {
            "ds18b20_4_c": 39.25,
            "ds18b20_bad_c": -127.0,
            "ds18b20_por_c": 85.0,
            "heat_exchanger_out_c": 61.4,
        },
        "ntc_a0_c": 36.7,  # top-level unit-suffixed sensor folded into extra_sensors
    }
    res = validate_message(raw, received_at=NOW, data_source="hardware")
    m = res.measurement
    assert m.heatsink_temperature_c == pytest.approx(47.9)
    assert m.battery_temperature_c == pytest.approx(29.4)
    assert m.mcu_temperature_c == pytest.approx(85.0)
    assert m.mpu_temperature_c == pytest.approx(38.5)
    assert m.surface_temperature_c == pytest.approx(51.2)
    assert m.humidity_pct == pytest.approx(44.5)
    assert m.pressure_hpa == pytest.approx(1014.8)
    assert m.wind_speed_ms == pytest.approx(2.3)
    assert m.irradiance_measured_wm2 == pytest.approx(790.0)
    assert m.extra_sensors["ds18b20_4_c"] == pytest.approx(39.25)
    assert m.extra_sensors["heat_exchanger_out_c"] == pytest.approx(61.4)
    assert m.extra_sensors["ntc_a0_c"] == pytest.approx(36.7)
    assert "ds18b20_bad_c" not in m.extra_sensors
    assert "ds18b20_por_c" not in m.extra_sensors
    assert m.sensor_status == "DEGRADED"
    assert len(res.issues) == 2

    db = Database(tmp_path / "sfs.db")
    assert db.insert_measurement(m) is not None
    got = db.latest_measurement(source="hardware")
    assert got == m

    ble_json = json.loads(encode_telemetry_json(m).decode("utf-8"))
    assert ble_json["th"] == pytest.approx(47.9)
    assert ble_json["tmcu"] == pytest.approx(85.0)
    assert ble_json["ex"]["heat_exchanger_out_c"] == pytest.approx(61.4)
    db.close()


def test_v1_to_v2_sqlite_migration_preserves_existing_rows(tmp_path):
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_file))
    conn.executescript("BEGIN;" + MIGRATIONS[0] + "PRAGMA user_version = 1;COMMIT;")
    conn.execute(
        "INSERT INTO measurements (timestamp_utc, boot_id, sequence, voltage_v, current_a, "
        "power_w, panel_temperature_c, sensor_status, data_source) "
        "VALUES ('2026-11-27T10:00:00.000+00:00', 'old1', 1, 0.02, 0.08, 0.0016, 28.5, 'OK', 'hardware')"
    )
    conn.commit()
    conn.close()

    db = Database(db_file)
    assert db.schema_version() == len(MIGRATIONS)
    m = db.latest_measurement(source="hardware")
    assert m is not None and m.boot_id == "old1"
    assert m.panel_temperature_c == pytest.approx(28.5)
    assert m.heatsink_temperature_c is None
    assert m.extra_sensors == {}
    db.close()


def test_export_csv_expands_extra_and_captive_portal_redirects(settings):
    settings.captive_portal_url = "http://10.44.0.1/"
    db = Database(settings.db_path)
    IngestService(db, None, "hardware").ingest({
        "schema_version": 1,
        "boot_id": "hw9",
        "sequence": 1,
        "voltage_v": 0.03,
        "current_a": 0.11,
        "heatsink_temperature_c": 44.2,
        "mcu_temperature_c": 37.8,
        "extra_sensors": {"probe_north_c": 31.4},
    }, received_at=NOW)
    db.close()

    with TestClient(create_app(settings)) as c:
        # Default mode is simulation -> empty unless ?source=hardware is explicitly requested
        assert c.get("/api/measurements/latest").status_code == 404
        hw = c.get("/api/measurements/latest?source=hardware").json()
        assert hw["heatsink_temperature_c"] == pytest.approx(44.2)
        assert hw["extra_sensors"]["probe_north_c"] == pytest.approx(31.4)

        csv_out = c.get("/api/measurements/export.csv?source=hardware").text
        assert "heatsink_temperature_c" in csv_out
        assert "extra.probe_north_c" in csv_out
        assert "31.4" in csv_out

        json_out = c.get("/api/measurements/export.json?source=hardware").json()
        assert json_out["count"] == 1
        assert json_out["measurements"][0]["mcu_temperature_c"] == pytest.approx(37.8)

        cap = c.get("/generate_204", follow_redirects=False)
        assert cap.status_code == 302 and cap.headers["location"] == "http://10.44.0.1/"
        rfc = c.get("/captive-portal-api")
        assert rfc.status_code == 200 and rfc.json()["captive"] is True

        live_hw = c.get("/api/live/telemetry?source=hardware&limit=10").json()
        assert live_hw["source"] == "hardware" and live_hw["count"] >= 1
        assert live_hw["latest"]["data_source"] == "hardware"

        live_sim = c.get("/api/live/telemetry?source=simulation&limit=10").json()
        assert live_sim["source"] == "simulation" and live_sim["count"] >= 1
        assert live_sim["latest"]["irradiance_wm2"] is not None
