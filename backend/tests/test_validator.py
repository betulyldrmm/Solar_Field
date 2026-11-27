from datetime import UTC, datetime

import pytest

from app.services.validator import MessageRejected, validate_message

NOW = datetime(2026, 11, 26, 10, 0, tzinfo=UTC)


def msg(**over):
    base = {
        "schema_version": 1, "boot_id": "b1", "sequence": 7,
        "voltage_v": 10.8, "current_a": 0.12, "power_w": 1.296,
        "panel_temperature_c": 35.2, "ambient_temperature_c": 27.4, "case_temperature_c": 31.0,
        "fan_state": False, "sensor_status": "OK",
    }
    base.update(over)
    return base


def test_valid_message_passes_unchanged():
    r = validate_message(msg(), received_at=NOW, data_source="hardware")
    assert r.issues == []
    m = r.measurement
    assert m.voltage_v == 10.8 and m.current_a == 0.12
    assert m.power_w == pytest.approx(1.296)
    assert m.sensor_status == "OK" and m.data_source == "hardware"
    assert m.timestamp_utc == NOW


@pytest.mark.parametrize("bad", [{"schema_version": 2}, {"sequence": -1}, {"sequence": "x"},
                                 {"sequence": True}])
def test_structural_errors_reject(bad):
    with pytest.raises(MessageRejected):
        validate_message(msg(**bad), received_at=NOW, data_source="hardware")


def test_ds18b20_disconnected_sentinel_is_not_stored():
    r = validate_message(msg(panel_temperature_c=-127.0), received_at=NOW, data_source="hardware")
    assert r.measurement.panel_temperature_c is None
    assert r.measurement.sensor_status == "DEGRADED"
    assert any("disconnected" in i.error for i in r.issues)


def test_ds18b20_power_on_value_rejected():
    r = validate_message(msg(case_temperature_c=85.0), received_at=NOW, data_source="hardware")
    assert r.measurement.case_temperature_c is None


def test_sensor_state_failed_nulls_field_even_if_value_present():
    r = validate_message(msg(sensors={"t_ambient": "timeout"}), received_at=NOW, data_source="hardware")
    assert r.measurement.ambient_temperature_c is None
    assert r.issues[0].sensor == "t_ambient"


def test_not_installed_sensor_is_silent():
    r = validate_message(msg(sensors={"ina_batt": "NOT_INSTALLED"}), received_at=NOW, data_source="hardware")
    assert r.issues == [] and r.measurement.battery_voltage_v is None


def test_out_of_range_and_nan():
    r = validate_message(msg(voltage_v=50.0, ambient_temperature_c=float("nan")),
                         received_at=NOW, data_source="hardware")
    assert r.measurement.voltage_v is None
    assert r.measurement.ambient_temperature_c is None
    assert r.measurement.power_w is None  # V invalid -> power can't be verified
    assert "power_unverifiable" in r.measurement.quality_flags


def test_power_recomputed_when_missing_or_mismatched():
    r = validate_message(msg(power_w=None), received_at=NOW, data_source="hardware")
    assert r.measurement.power_w == pytest.approx(1.296)
    assert "power_computed" in r.measurement.quality_flags
    r = validate_message(msg(power_w=5.0), received_at=NOW, data_source="hardware")
    assert r.measurement.power_w == pytest.approx(1.296)
    assert "power_mismatch" in r.measurement.quality_flags


def test_clock_unsynced_flag():
    r = validate_message(msg(), received_at=datetime(2000, 1, 1, tzinfo=UTC), data_source="hardware")
    assert "clock_unsynced" in r.measurement.quality_flags
