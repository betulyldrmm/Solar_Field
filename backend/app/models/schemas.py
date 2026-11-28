"""Pydantic models. Every numeric field name carries its unit as a suffix
(_v, _a, _w, _wh, _kwh, _c, _s, _deg, _pct, _wm2)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

DataSource = Literal["simulation", "hardware"]
ValueKind = Literal["measured", "assumed", "calculated", "estimated"]


# --------------------------------------------------------------------------
# Stored measurement
# --------------------------------------------------------------------------
class Measurement(BaseModel):
    """One validated measurement row. None means "not available / invalid",
    never a substituted fake value."""

    timestamp_utc: datetime
    boot_id: str
    sequence: int = Field(ge=0)
    voltage_v: float | None = None
    current_a: float | None = None
    power_w: float | None = None
    panel_temperature_c: float | None = None
    ambient_temperature_c: float | None = None
    case_temperature_c: float | None = None
    heatsink_temperature_c: float | None = None
    battery_temperature_c: float | None = None
    mcu_temperature_c: float | None = None
    mpu_temperature_c: float | None = None
    surface_temperature_c: float | None = None
    humidity_pct: float | None = None
    pressure_hpa: float | None = None
    wind_speed_ms: float | None = None
    irradiance_measured_wm2: float | None = None
    battery_voltage_v: float | None = None
    battery_current_a: float | None = None
    fan_state: bool | None = None
    sensor_status: str = "OK"
    sensor_states: dict[str, str] = Field(default_factory=dict)
    extra_sensors: dict[str, float] = Field(default_factory=dict)
    quality_flags: list[str] = Field(default_factory=list)
    data_source: DataSource


class MeasurementPage(BaseModel):
    items: list[Measurement]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------
# User-editable system configuration
# --------------------------------------------------------------------------
class SystemConfig(BaseModel):
    # Planned PV system
    panel_power_w: float = Field(450, gt=0, le=1000, description="STC power of one module, W")
    panel_count: int = Field(8, ge=1, le=10000)
    performance_ratio: float = Field(
        0.80, ge=0.5, le=0.95,
        description="Inverter, cable, soiling, mismatch losses (excl. temperature)",
    )
    temp_coefficient_pct_per_c: float = Field(-0.35, ge=-1.0, le=0.0)
    tilt_deg: float = Field(30, ge=0, le=90)
    azimuth_deg: float = Field(180, ge=0, lt=360, description="180 = south")
    # Reference panel (measurement) — see docs/calibration.md
    reference_mode: Literal["short_circuit", "resistive_load"] = "short_circuit"
    ref_isc_per_wm2_a: float = Field(
        0.150 / 1000, gt=0,
        description="Reference panel short-circuit current per 1 W/m2 irradiance, A/(W/m2)",
    )
    ref_calibrated: bool = Field(
        False, description="False -> factor is the nameplate assumption, not a calibration"
    )
    ref_isc_temp_coeff_per_c: float = Field(0.0005, ge=0, le=0.002)
    # Acquisition / thermal
    measurement_interval_s: int = Field(
        300, ge=1, le=3600,
        description="Seconds between STORED measurements (STM32 averages its fast samples over this window)",
    )
    fan_on_c: float = Field(40.0, ge=25, le=70)
    fan_off_c: float = Field(35.0, ge=20, le=68)
    # Night shutdown (see app/services/power_schedule.py)
    night_shutdown_enabled: bool = True
    shutdown_after_sunset_min: int = Field(30, ge=0, le=180)
    wake_before_sunrise_min: int = Field(30, ge=0, le=180)

    @model_validator(mode="after")
    def _check_fan_hysteresis(self) -> SystemConfig:
        if self.fan_off_c > self.fan_on_c - 2.0:
            raise ValueError("fan_off_c must be at least 2 °C below fan_on_c (hysteresis)")
        return self

    @property
    def kwp(self) -> float:
        return self.panel_power_w * self.panel_count / 1000.0


class SystemConfigOut(SystemConfig):
    installed_kwp: float
    data_mode: DataSource


# --------------------------------------------------------------------------
# Energy
# --------------------------------------------------------------------------
class DailyEnergy(BaseModel):
    date: str  # local date, YYYY-MM-DD
    reference_energy_wh: float = Field(description="Integrated V*I of the reference panel")
    insolation_kwh_m2: float | None = Field(
        description="Plane-of-array insolation from reference Isc; null if no current data"
    )
    insolation_kind: ValueKind
    daylight_coverage: float = Field(ge=0, le=1, description="Fraction of daylight measured")
    sample_count: int
    gap_count: int
    irradiance_weighted_panel_temp_c: float | None
    complete: bool
    data_source: str


class EnergySummary(BaseModel):
    today: DailyEnergy | None
    days_available: int
    complete_days: int
    mean_insolation_kwh_m2: float | None
    calibration_status: Literal["calibrated", "uncalibrated"]
    data_source: str


class LabeledValue(BaseModel):
    value: float | None
    unit: str
    kind: ValueKind
    note: str = ""


class EstimateRequest(BaseModel):
    panel_power_w: float = Field(gt=0, le=1000)
    panel_count: int = Field(ge=1, le=10000)
    performance_ratio: float = Field(0.80, ge=0.5, le=0.95)
    temp_coefficient_pct_per_c: float = Field(-0.35, ge=-1.0, le=0.0)
    tilt_deg: float = Field(30, ge=0, le=90)
    azimuth_deg: float = Field(180, ge=0, lt=360)
    method: Literal["auto", "measured_reference", "theoretical_psh"] = "auto"
    peak_sun_hours: float | None = Field(
        None, gt=0, le=12, description="Required for theoretical_psh"
    )
    days: int = Field(7, ge=1, le=366, description="How many recent days of data to use")


class EstimateResponse(BaseModel):
    method: Literal["measured_reference", "theoretical_psh"]
    installed_kwp: float
    daily_energy_kwh: float = Field(description="Mean estimated daily production")
    per_day: list[dict[str, float | str]] = Field(default_factory=list)
    inputs: dict[str, LabeledValue]
    calibration_status: Literal["calibrated", "uncalibrated", "not_applicable"]
    data_source: str
    warnings: list[str]


# --------------------------------------------------------------------------
# Status / events
# --------------------------------------------------------------------------
class BatteryStatus(BaseModel):
    voltage_v: float | None
    current_a: float | None
    soc_pct_estimated: float | None
    series_cells: int
    note: str


class DeviceStatus(BaseModel):
    device_status: Literal["online", "stale", "offline", "sleeping"]
    data_mode: DataSource
    last_measurement_utc: datetime | None
    age_s: float | None
    sensor_status: str | None
    sensor_states: dict[str, str]
    fan_state: bool | None
    battery: BatteryStatus
    scheduled_on: bool = True
    next_shutdown_utc: datetime | None = None
    next_wake_utc: datetime | None = None
    bridge: str
    measurements_total: int


class Event(BaseModel):
    id: int
    timestamp_utc: datetime
    level: str
    kind: str
    message: str


class SensorError(BaseModel):
    id: int
    timestamp_utc: datetime
    sensor: str
    error: str
    measurement_sequence: int | None


class EventsResponse(BaseModel):
    events: list[Event]
    sensor_errors: list[SensorError]


class Health(BaseModel):
    status: Literal["ok", "degraded"]
    version: str
    db_ok: bool
    time_utc: datetime
