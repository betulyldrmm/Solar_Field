"""Validates raw STM32 messages (schema v1) into Measurement rows.

Rules:
* Structural problems (wrong schema, missing sequence) -> message rejected.
* Field problems (out of range, sensor fault sentinel, sensor reported failed)
  -> that field becomes None and a SensorIssue is recorded. A bad value is
  never stored as if it were a real measurement.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.config import SCHEMA_VERSION
from app.models.schemas import Measurement

# DS18B20 sentinels: -127 = DEVICE_DISCONNECTED_C, 85.0 = power-on reset value
DS18B20_DISCONNECTED_C = -127.0
DS18B20_POWER_ON_RESET_C = 85.0

# field -> (sensor id, min, max)
FIELD_RULES: dict[str, tuple[str, float, float]] = {
    "voltage_v": ("ina_ref", -0.1, 36.0),  # INA226 bus voltage limit 36 V
    "current_a": ("ina_ref", -0.05, 2.0),
    "power_w": ("ina_ref", -0.5, 50.0),
    "battery_voltage_v": ("ina_batt", 0.0, 36.0),
    "battery_current_a": ("ina_batt", -10.0, 10.0),
    "panel_temperature_c": ("t_panel", -40.0, 120.0),
    "ambient_temperature_c": ("t_ambient", -40.0, 70.0),
    "case_temperature_c": ("t_case", -40.0, 100.0),
    "heatsink_temperature_c": ("t_heatsink", -40.0, 125.0),
    "battery_temperature_c": ("t_battery", -40.0, 90.0),
    "mcu_temperature_c": ("t_mcu", -40.0, 125.0),
    "mpu_temperature_c": ("t_mpu", -40.0, 125.0),
    "surface_temperature_c": ("t_surface", -70.0, 380.0),
    "humidity_pct": ("env_humidity", 0.0, 100.0),
    "pressure_hpa": ("env_pressure", 300.0, 1150.0),
    "wind_speed_ms": ("wind", 0.0, 75.0),
    "irradiance_measured_wm2": ("irradiance_sensor", 0.0, 1600.0),
}
# Fields typically backed by DS18B20 1-Wire probes (reject both -127 and 85.0 reset sentinel)
DS18B20_FIELDS = {
    "panel_temperature_c",
    "ambient_temperature_c",
    "case_temperature_c",
    "heatsink_temperature_c",
    "battery_temperature_c",
}
TEMP_FIELDS = DS18B20_FIELDS | {"mcu_temperature_c", "mpu_temperature_c", "surface_temperature_c"}
OK_STATES = {"OK"}
EARLIEST_VALID_TIME = datetime(2025, 1, 1, tzinfo=UTC)
_EXTRA_KEY_RE = re.compile(r"^[a-zA-Z0-9_.-]{1,48}$")
_EXTRA_SUFFIXES = ("_c", "_v", "_a", "_w", "_pct", "_hpa", "_lux", "_wm2", "_ms", "_ohm", "_rpm", "_hz")
_KNOWN_TOP_KEYS = {
    "schema_version",
    "boot_id",
    "sequence",
    "uptime_ms",
    "fan_state",
    "sensor_status",
    "sensors",
    "extra_sensors",
    *FIELD_RULES,
}


class MessageRejected(ValueError):
    """The message cannot be stored at all."""


@dataclass
class SensorIssue:
    sensor: str
    error: str


@dataclass
class ValidationResult:
    measurement: Measurement
    issues: list[SensorIssue] = field(default_factory=list)


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def validate_message(
    raw: dict[str, Any], *, received_at: datetime, data_source: str
) -> ValidationResult:
    if not isinstance(raw, dict):
        raise MessageRejected("message is not a JSON object")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise MessageRejected(
            f"unsupported schema_version {raw.get('schema_version')!r}, expected {SCHEMA_VERSION}"
        )
    seq = raw.get("sequence")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        raise MessageRejected("sequence must be a non-negative integer")
    if received_at.tzinfo is None:
        raise MessageRejected("received_at must be timezone-aware")

    issues: list[SensorIssue] = []
    flags: list[str] = []
    sensor_states_raw = raw.get("sensors") or {}
    sensor_states = {
        str(k): str(v).upper() for k, v in sensor_states_raw.items()
    } if isinstance(sensor_states_raw, dict) else {}

    values: dict[str, float | None] = {}
    for name, (sensor, lo, hi) in FIELD_RULES.items():
        present = name in raw and raw[name] is not None
        val = _as_float(raw.get(name))
        state = sensor_states.get(sensor, "OK")
        if state not in OK_STATES:
            if present or state != "NOT_INSTALLED":
                issues.append(SensorIssue(sensor, f"{name}: sensor state {state}"))
            values[name] = None
            continue
        if present and val is None:
            issues.append(SensorIssue(sensor, f"{name}: not a finite number"))
        elif val is not None and name in TEMP_FIELDS:
            if val == DS18B20_DISCONNECTED_C:
                issues.append(SensorIssue(sensor, f"{name}: DS18B20 disconnected (-127)"))
                val = None
            elif name in DS18B20_FIELDS and val == DS18B20_POWER_ON_RESET_C:
                issues.append(SensorIssue(sensor, f"{name}: DS18B20 power-on value 85.0"))
                val = None
        if val is not None and not (lo <= val <= hi):
            issues.append(SensorIssue(sensor, f"{name}: {val} out of range [{lo}, {hi}]"))
            val = None
        values[name] = val

    extra_candidates: dict[str, Any] = {}
    raw_extra = raw.get("extra_sensors")
    if isinstance(raw_extra, dict):
        extra_candidates.update(raw_extra)
    for k, v_raw in raw.items():
        if k not in _KNOWN_TOP_KEYS and k.endswith(_EXTRA_SUFFIXES):
            extra_candidates.setdefault(k, v_raw)

    extra_sensors: dict[str, float] = {}
    for k_raw, v_raw in list(extra_candidates.items())[:64]:
        k = str(k_raw).strip()
        if not _EXTRA_KEY_RE.match(k):
            issues.append(SensorIssue("extra_sensors", f"invalid sensor key {k!r}"))
            continue
        state = sensor_states.get(k, "OK")
        if state not in OK_STATES:
            if v_raw is not None or state != "NOT_INSTALLED":
                issues.append(SensorIssue(k, f"{k}: sensor state {state}"))
            continue
        if v_raw is None:
            continue
        val = _as_float(v_raw)
        if val is None:
            issues.append(SensorIssue(k, f"{k}: not a finite number"))
            continue
        is_temp = k.endswith("_c") or k.startswith(("temp_", "t_", "ds18b20_", "heat_"))
        if is_temp:
            if val == DS18B20_DISCONNECTED_C:
                issues.append(SensorIssue(k, f"{k}: DS18B20 disconnected (-127)"))
                continue
            if k.startswith("ds18b20_") and val == DS18B20_POWER_ON_RESET_C:
                issues.append(SensorIssue(k, f"{k}: DS18B20 power-on value 85.0"))
                continue
            if not (-200.0 <= val <= 1000.0):
                issues.append(SensorIssue(k, f"{k}: {val} out of thermal range [-200, 1000]"))
                continue
        extra_sensors[k] = val

    v, i, p = values["voltage_v"], values["current_a"], values["power_w"]
    if v is not None and i is not None:
        computed = v * i
        if p is None:
            values["power_w"] = computed
            flags.append("power_computed")
        elif abs(p - computed) > max(0.05, 0.05 * abs(computed)):
            flags.append("power_mismatch")
            values["power_w"] = computed
    elif p is not None:
        # power without V or I cannot be cross-checked -> don't trust it
        values["power_w"] = None
        flags.append("power_unverifiable")

    if received_at < EARLIEST_VALID_TIME:
        flags.append("clock_unsynced")

    fan = raw.get("fan_state")
    fan_state = fan if isinstance(fan, bool) else None

    status = str(raw.get("sensor_status") or "OK").upper()
    if issues and status == "OK":
        status = "DEGRADED"

    m = Measurement(
        timestamp_utc=received_at.astimezone(UTC),
        boot_id=str(raw.get("boot_id") or "unknown")[:32],
        sequence=seq,
        fan_state=fan_state,
        sensor_status=status,
        sensor_states=sensor_states,
        extra_sensors=extra_sensors,
        quality_flags=flags,
        data_source=data_source,  # type: ignore[arg-type]
        **values,
    )
    return ValidationResult(measurement=m, issues=issues)
