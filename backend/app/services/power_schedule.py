"""Night shutdown schedule.

The station switches itself OFF `shutdown_after_sunset_min` after sunset and
back ON `wake_before_sunrise_min` before the next sunrise (winter energy
budget: no load during the ~14 h winter night).

This module only *computes* the schedule (pure functions, testable on a PC).
Who actually cuts and restores power is a hardware decision:

DOĞRULAMA GEREKİYOR: planned mechanism is a DS3231 alarm (INT/SQW pin) driving
the power switch for the 5 V rail. Linux programs the alarm with `next_wake`
before an orderly shutdown (`systemctl poweroff`), so SQLite/CSV are closed
cleanly. Not built or tested yet.

Sunrise/sunset use the same NOAA elevation model as the rest of the backend
(threshold -0.833°), found by scanning + bisection, accuracy about ±1 min.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.schemas import SystemConfig
from app.services.solar_position import sun_elevation_deg

HORIZON_DEG = -0.833
_SCAN = timedelta(minutes=10)


@dataclass(frozen=True)
class SunEvents:
    sunrise_utc: datetime | None  # None: polar day/night (never happens at our latitudes)
    sunset_utc: datetime | None


@dataclass(frozen=True)
class PowerWindow:
    """One ON period: wake (before sunrise) -> shutdown (after sunset)."""

    wake_utc: datetime
    shutdown_utc: datetime


def _above(t: datetime, lat: float, lon: float) -> bool:
    return sun_elevation_deg(t, lat, lon) > HORIZON_DEG


def _bisect(a: datetime, b: datetime, lat: float, lon: float) -> datetime:
    state_a = _above(a, lat, lon)
    while (b - a) > timedelta(seconds=20):
        mid = a + (b - a) / 2
        if _above(mid, lat, lon) == state_a:
            a = mid
        else:
            b = mid
    return (a + (b - a) / 2).replace(microsecond=0)


def sun_events(day: date, lat: float, lon: float, timezone: str) -> SunEvents:
    """Sunrise and sunset on the given LOCAL date."""
    tz = ZoneInfo(timezone)
    t = datetime(day.year, day.month, day.day, tzinfo=tz)
    end = t + timedelta(days=1)
    sunrise = sunset = None
    prev = _above(t, lat, lon)
    while t < end:
        nxt = t + _SCAN
        cur = _above(nxt, lat, lon)
        if cur != prev:
            crossing = _bisect(t, nxt, lat, lon).astimezone(ZoneInfo("UTC"))
            if cur and sunrise is None:
                sunrise = crossing
            elif not cur:
                sunset = crossing
        prev, t = cur, nxt
    return SunEvents(sunrise, sunset)


def window_for(day: date, cfg: SystemConfig, lat: float, lon: float, timezone: str) -> PowerWindow | None:
    ev = sun_events(day, lat, lon, timezone)
    if ev.sunrise_utc is None or ev.sunset_utc is None:
        return None
    return PowerWindow(
        wake_utc=ev.sunrise_utc - timedelta(minutes=cfg.wake_before_sunrise_min),
        shutdown_utc=ev.sunset_utc + timedelta(minutes=cfg.shutdown_after_sunset_min),
    )


def is_scheduled_on(when: datetime, cfg: SystemConfig, lat: float, lon: float, timezone: str) -> bool:
    """True if the station should be running at `when`. Always True if disabled."""
    if not cfg.night_shutdown_enabled:
        return True
    day = when.astimezone(ZoneInfo(timezone)).date()
    w = window_for(day, cfg, lat, lon, timezone)
    return w is None or w.wake_utc <= when < w.shutdown_utc


def next_transitions(
    when: datetime, cfg: SystemConfig, lat: float, lon: float, timezone: str
) -> tuple[datetime | None, datetime | None]:
    """(next_shutdown_utc, next_wake_utc) after `when`; (None, None) if disabled."""
    if not cfg.night_shutdown_enabled:
        return None, None
    tz = ZoneInfo(timezone)
    day = when.astimezone(tz).date()
    shutdown = wake = None
    for offset in range(0, 3):
        w = window_for(day + timedelta(days=offset), cfg, lat, lon, timezone)
        if w is None:
            continue
        if wake is None and w.wake_utc > when:
            wake = w.wake_utc
        if shutdown is None and w.shutdown_utc > when:
            shutdown = w.shutdown_utc
        if wake and shutdown:
            break
    return shutdown, wake
