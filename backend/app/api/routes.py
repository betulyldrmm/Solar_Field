"""HTTP API. All numeric fields carry their unit in the name."""

from __future__ import annotations

import hmac
import json
import subprocess
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse

from app.ble.protocol import DEVICE_NAME, LIVE_CHAR_UUID, SERVICE_UUID, TELEMETRY_JSON_CHAR_UUID
from app.config import APP_VERSION, Settings
from app.models.schemas import (
    DailyEnergy,
    DataSource,
    DeviceStatus,
    EnergySummary,
    EstimateRequest,
    EstimateResponse,
    Event,
    EventsResponse,
    Health,
    Measurement,
    MeasurementPage,
    SensorError,
    SystemConfig,
    SystemConfigOut,
)
from app.services.battery_monitor import battery_status
from app.services.energy_calculator import EstimateUnavailable, daily_energy, estimate_production
from app.services.power_schedule import is_scheduled_on, next_transitions
from app.storage.csv_logger import to_csv_text
from app.storage.database import Database

router = APIRouter(prefix="/api")
MAX_EXPORT_ROWS = 200_000


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


DbDep = Annotated[Database, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _active_source(settings: Settings, source: DataSource | None) -> DataSource:
    return source if source is not None else settings.data_mode  # type: ignore[return-value]


def require_token(
    settings: SettingsDep, x_api_token: Annotated[str | None, Header()] = None
) -> None:
    if not settings.api_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Configuration changes are disabled (SFS_API_TOKEN not set)")
    if x_api_token is None or not hmac.compare_digest(x_api_token, settings.api_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing X-API-Token")


def _check_range(start: datetime | None, end: datetime | None) -> None:
    for name, v in (("start", start), ("end", end)):
        if v is not None and v.tzinfo is None:
            raise HTTPException(422,
                                f"{name} must include a timezone (e.g. 2026-11-26T00:00:00Z)")
    if start and end and start >= end:
        raise HTTPException(422, "start must be before end")


def _daily(db: Database, settings: Settings, days: int, src: str) -> list[DailyEnergy]:
    cfg = db.load_config()
    start = datetime.now(UTC) - timedelta(days=days + 1)
    rows = db.query_measurements(start=start, source=src)
    result = daily_energy(rows, cfg, latitude_deg=settings.latitude_deg,
                          longitude_deg=settings.longitude_deg, timezone=settings.timezone)
    return result[-days:]


# ---------------------------------------------------------------- health
@router.get("/health", response_model=Health)
def health(db: DbDep) -> Health:
    ok = db.ping()
    return Health(status="ok" if ok else "degraded", version=APP_VERSION, db_ok=ok,
                  time_utc=datetime.now(UTC))


@router.get("/live/telemetry")
def live_telemetry(
    request: Request,
    settings: SettingsDep,
    source: DataSource | None = None,
    limit: Annotated[int, Query(ge=1, le=180)] = 120,
) -> dict[str, Any]:
    """High-frequency 1.5 s live ring buffer for real-time oscilloscopes and gauges."""
    src = _active_source(settings, source)
    hub = getattr(request.app.state, "live_hub", None)
    if hub is None:
        return {"source": src, "count": 0, "latest": None, "series": []}
    return hub.snapshot(src, limit=limit)


@router.get("/bluetooth/info")
def bluetooth_info(settings: SettingsDep) -> dict[str, Any]:
    """Bluetooth PAN + BLE GATT connection metadata for the field dashboard."""
    peers: list[str] = []
    adapter_mac: str | None = None
    try:
        out = subprocess.run(
            ["bluetoothctl", "devices", "Connected"],
            capture_output=True, text=True, timeout=1.5, check=False,
        )
        for line in out.stdout.splitlines():
            if line.startswith("Device "):
                peers.append(line.removeprefix("Device ").strip())
        show = subprocess.run(
            ["bluetoothctl", "show"],
            capture_output=True, text=True, timeout=1.5, check=False,
        )
        for line in show.stdout.splitlines():
            if line.startswith("Controller "):
                adapter_mac = line.split()[1]
                break
    except (OSError, subprocess.SubprocessError):
        pass
    return {
        "device_name": DEVICE_NAME,
        "adapter_mac": adapter_mac,
        "pan_ip": settings.bt_pan_ip,
        "pan_url": f"http://{settings.bt_pan_ip}/",
        "connected_peers": peers,
        "ble_service_uuid": SERVICE_UUID,
        "ble_live_char_uuid": LIVE_CHAR_UUID,
        "ble_json_char_uuid": TELEMETRY_JSON_CHAR_UUID,
    }


@router.get("/device/status", response_model=DeviceStatus)
def device_status(
    request: Request, db: DbDep, settings: SettingsDep, source: DataSource | None = None
) -> DeviceStatus:
    src = _active_source(settings, source)
    cfg = db.load_config()
    last = db.latest_measurement(source=src)
    stale_after = 3 * cfg.measurement_interval_s + 10
    if last is None:
        state, age = "offline", None
    else:
        age = (datetime.now(UTC) - last.timestamp_utc).total_seconds()
        state = "online" if age <= stale_after else ("stale" if age <= 10 * stale_after else "offline")
    now = datetime.now(UTC)
    args = (cfg, settings.latitude_deg, settings.longitude_deg, settings.timezone)
    scheduled_on = is_scheduled_on(now, *args)
    next_shutdown, next_wake = next_transitions(now, *args)
    if not scheduled_on and state != "online":
        state = "sleeping"  # planned night shutdown, not a fault
    adapter = getattr(request.app.state, "bridge", None)
    router_up = bool(getattr(adapter, "router_connected", False))
    if src == "simulation":
        bridge = "simulator"
    elif state == "online":
        bridge = "connected"
    elif router_up:
        bridge = "router_waiting"
    else:
        bridge = "no_data"
    return DeviceStatus(
        device_status=state,
        data_mode=src,
        last_measurement_utc=last.timestamp_utc if last else None,
        age_s=round(age, 1) if age is not None else None,
        sensor_status=last.sensor_status if last else None,
        sensor_states=last.sensor_states if last else {},
        fan_state=last.fan_state if last else None,
        battery=battery_status(last.battery_voltage_v if last else None,
                               last.battery_current_a if last else None,
                               settings.battery_series_cells),
        scheduled_on=scheduled_on,
        next_shutdown_utc=next_shutdown,
        next_wake_utc=next_wake,
        bridge=bridge,
        measurements_total=db.count_measurements(source=src),
    )


# ---------------------------------------------------------- measurements
@router.get("/measurements/latest", response_model=Measurement,
            responses={404: {"description": "No measurement yet"}})
def latest(db: DbDep, settings: SettingsDep, source: DataSource | None = None) -> Measurement:
    src = _active_source(settings, source)
    m = db.latest_measurement(source=src)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No measurement recorded yet")
    return m


@router.get("/measurements/history", response_model=MeasurementPage)
def history(
    db: DbDep,
    settings: SettingsDep,
    start: datetime | None = None,
    end: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=5000)] = 500,
    offset: Annotated[int, Query(ge=0)] = 0,
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc",
    source: DataSource | None = None,
) -> MeasurementPage:
    _check_range(start, end)
    src = _active_source(settings, source)
    items = db.query_measurements(start, end, src, limit, offset, descending=order == "desc")
    total = db.count_measurements(start, end, src)
    return MeasurementPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/measurements/export.csv", response_class=PlainTextResponse)
def export_csv(
    db: DbDep,
    settings: SettingsDep,
    start: datetime | None = None,
    end: datetime | None = None,
    source: DataSource | None = None,
) -> PlainTextResponse:
    _check_range(start, end)
    src = _active_source(settings, source)
    if db.count_measurements(start, end, src) > MAX_EXPORT_ROWS:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"More than {MAX_EXPORT_ROWS} rows; narrow the date range")
    rows = db.query_measurements(start, end, src)
    return PlainTextResponse(
        to_csv_text(rows), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="measurements_{src}.csv"'},
    )


@router.get("/measurements/export.json")
def export_json(
    db: DbDep,
    settings: SettingsDep,
    start: datetime | None = None,
    end: datetime | None = None,
    source: DataSource | None = None,
) -> JSONResponse:
    _check_range(start, end)
    src = _active_source(settings, source)
    if db.count_measurements(start, end, src) > MAX_EXPORT_ROWS:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"More than {MAX_EXPORT_ROWS} rows; narrow the date range")
    rows = db.query_measurements(start, end, src)
    payload = {
        "exported_utc": datetime.now(UTC).isoformat(),
        "data_source": src,
        "count": len(rows),
        "measurements": [json.loads(r.model_dump_json()) for r in rows],
    }
    return JSONResponse(
        content=payload,
        headers={"Content-Disposition": f'attachment; filename="measurements_{src}.json"'},
    )


# ---------------------------------------------------------------- energy
@router.get("/energy/daily", response_model=list[DailyEnergy])
def energy_daily(
    db: DbDep,
    settings: SettingsDep,
    days: Annotated[int, Query(ge=1, le=366)] = 7,
    source: DataSource | None = None,
) -> list[DailyEnergy]:
    src = _active_source(settings, source)
    return _daily(db, settings, days, src)


@router.get("/energy/summary", response_model=EnergySummary)
def energy_summary(
    db: DbDep, settings: SettingsDep, source: DataSource | None = None
) -> EnergySummary:
    src = _active_source(settings, source)
    cfg = db.load_config()
    days = _daily(db, settings, 366, src)
    today_str = datetime.now(ZoneInfo(settings.timezone)).date().isoformat()
    complete = [d for d in days if d.complete and d.insolation_kwh_m2 is not None]
    return EnergySummary(
        today=next((d for d in days if d.date == today_str), None),
        days_available=len(days),
        complete_days=len(complete),
        mean_insolation_kwh_m2=(
            round(sum(d.insolation_kwh_m2 or 0 for d in complete) / len(complete), 4)
            if complete else None
        ),
        calibration_status="calibrated" if cfg.ref_calibrated else "uncalibrated",
        data_source=src,
    )


@router.post("/simulation/estimate", response_model=EstimateResponse)
def estimate(
    req: EstimateRequest, db: DbDep, settings: SettingsDep, source: DataSource | None = None
) -> EstimateResponse:
    src = _active_source(settings, source)
    cfg = db.load_config()
    days = _daily(db, settings, req.days + 1, src)
    try:
        resp = estimate_production(req, cfg, days, src)
    except EstimateUnavailable as exc:
        raise HTTPException(422, str(exc)) from exc
    db.save_estimate(resp.method, resp.installed_kwp, resp.daily_energy_kwh,
                     req.model_dump(), resp.model_dump(), resp.data_source)
    return resp


# ---------------------------------------------------------------- config
def _config_out(cfg: SystemConfig, settings: Settings, source: DataSource | None = None) -> SystemConfigOut:
    src = _active_source(settings, source)
    return SystemConfigOut(**cfg.model_dump(), installed_kwp=round(cfg.kwp, 4), data_mode=src)


@router.get("/config", response_model=SystemConfigOut)
def get_config(
    db: DbDep, settings: SettingsDep, source: DataSource | None = None
) -> SystemConfigOut:
    return _config_out(db.load_config(), settings, source)


@router.put("/config", response_model=SystemConfigOut, dependencies=[Depends(require_token)])
def put_config(cfg: SystemConfig, request: Request, db: DbDep, settings: SettingsDep) -> SystemConfigOut:
    old = db.load_config()
    db.save_config(cfg)
    changed = [k for k, v in cfg.model_dump().items() if old.model_dump().get(k) != v]
    db.add_event("info", "config_changed", "changed: " + (", ".join(changed) or "nothing"))
    on_change = getattr(request.app.state, "on_config_change", None)
    if on_change is not None:
        on_change(cfg)
    return _config_out(cfg, settings)


# ---------------------------------------------------------------- events
@router.get("/events", response_model=EventsResponse)
def events(db: DbDep, limit: Annotated[int, Query(ge=1, le=1000)] = 100) -> EventsResponse:
    return EventsResponse(
        events=[Event(**e) for e in db.recent_events(limit)],
        sensor_errors=[SensorError(**e) for e in db.recent_sensor_errors(limit)],
    )
