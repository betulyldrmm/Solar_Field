"""HTTP API. All numeric fields carry their unit in the name."""

from __future__ import annotations

import hmac
from datetime import UTC, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.config import APP_VERSION, Settings
from app.models.schemas import (
    DailyEnergy,
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


def _daily(db: Database, settings: Settings, days: int) -> list[DailyEnergy]:
    cfg = db.load_config()
    start = datetime.now(UTC) - timedelta(days=days + 1)
    rows = db.query_measurements(start=start, source=settings.data_mode)
    result = daily_energy(rows, cfg, latitude_deg=settings.latitude_deg,
                          longitude_deg=settings.longitude_deg, timezone=settings.timezone)
    return result[-days:]


# ---------------------------------------------------------------- health
@router.get("/health", response_model=Health)
def health(db: DbDep) -> Health:
    ok = db.ping()
    return Health(status="ok" if ok else "degraded", version=APP_VERSION, db_ok=ok,
                  time_utc=datetime.now(UTC))


@router.get("/device/status", response_model=DeviceStatus)
def device_status(request: Request, db: DbDep, settings: SettingsDep) -> DeviceStatus:
    cfg = db.load_config()
    last = db.latest_measurement(source=settings.data_mode)
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
    bridge = "simulator" if settings.data_mode == "simulation" else (
        "connected" if state == "online" else "no_data")
    return DeviceStatus(
        device_status=state,
        data_mode=settings.data_mode,  # type: ignore[arg-type]
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
        measurements_total=db.count_measurements(source=settings.data_mode),
    )


# ---------------------------------------------------------- measurements
@router.get("/measurements/latest", response_model=Measurement,
            responses={404: {"description": "No measurement yet"}})
def latest(db: DbDep, settings: SettingsDep) -> Measurement:
    m = db.latest_measurement(source=settings.data_mode)
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
) -> MeasurementPage:
    _check_range(start, end)
    items = db.query_measurements(start, end, settings.data_mode, limit, offset,
                                  descending=order == "desc")
    total = db.count_measurements(start, end, settings.data_mode)
    return MeasurementPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/measurements/export.csv", response_class=PlainTextResponse)
def export_csv(db: DbDep, settings: SettingsDep,
               start: datetime | None = None, end: datetime | None = None) -> PlainTextResponse:
    _check_range(start, end)
    if db.count_measurements(start, end, settings.data_mode) > MAX_EXPORT_ROWS:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            f"More than {MAX_EXPORT_ROWS} rows; narrow the date range")
    rows = db.query_measurements(start, end, settings.data_mode)
    return PlainTextResponse(
        to_csv_text(rows), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="measurements_{settings.data_mode}.csv"'},
    )


# ---------------------------------------------------------------- energy
@router.get("/energy/daily", response_model=list[DailyEnergy])
def energy_daily(db: DbDep, settings: SettingsDep,
                 days: Annotated[int, Query(ge=1, le=366)] = 7) -> list[DailyEnergy]:
    return _daily(db, settings, days)


@router.get("/energy/summary", response_model=EnergySummary)
def energy_summary(db: DbDep, settings: SettingsDep) -> EnergySummary:
    cfg = db.load_config()
    days = _daily(db, settings, 366)
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
        data_source=settings.data_mode,
    )


@router.post("/simulation/estimate", response_model=EstimateResponse)
def estimate(req: EstimateRequest, db: DbDep, settings: SettingsDep) -> EstimateResponse:
    cfg = db.load_config()
    days = _daily(db, settings, req.days + 1)
    try:
        resp = estimate_production(req, cfg, days, settings.data_mode)
    except EstimateUnavailable as exc:
        raise HTTPException(422, str(exc)) from exc
    db.save_estimate(resp.method, resp.installed_kwp, resp.daily_energy_kwh,
                     req.model_dump(), resp.model_dump(), resp.data_source)
    return resp


# ---------------------------------------------------------------- config
def _config_out(cfg: SystemConfig, settings: Settings) -> SystemConfigOut:
    return SystemConfigOut(**cfg.model_dump(), installed_kwp=round(cfg.kwp, 4),
                           data_mode=settings.data_mode)  # type: ignore[arg-type]


@router.get("/config", response_model=SystemConfigOut)
def get_config(db: DbDep, settings: SettingsDep) -> SystemConfigOut:
    return _config_out(db.load_config(), settings)


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
