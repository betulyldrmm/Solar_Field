"""Date-range aggregation & parabolic smoothing for the simplified field UI.

Supports reading 1 day (hourly parabolic curve), 7 days, 30 days (daily), or
12 months (monthly seasonal curve), plus any custom [start, end] date range.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.models.schemas import Measurement, SystemConfig
from app.services.energy_calculator import irradiance_wm2

BucketMode = Literal["auto", "hour", "day", "month"]

MONTH_TR = (
    "",
    "Oca",
    "Şub",
    "Mar",
    "Nis",
    "May",
    "Haz",
    "Tem",
    "Ağu",
    "Eyl",
    "Eki",
    "Kas",
    "Ara",
)


def _smooth_series(vals: list[float | None], passes: int = 1) -> list[float | None]:
    """Gentle [0.2, 0.6, 0.2] 3-point kernel that preserves None gaps and peak shape."""
    out = list(vals)
    for _ in range(passes):
        nxt: list[float | None] = list(out)
        for i, cur in enumerate(out):
            if cur is None:
                continue
            prev_v = out[i - 1] if i > 0 else None
            next_v = out[i + 1] if i + 1 < len(out) else None
            if prev_v is not None and next_v is not None:
                nxt[i] = round(0.2 * prev_v + 0.6 * cur + 0.2 * next_v, 2)
        out = nxt
    return out


def _instant_watts(m: Measurement, cfg: SystemConfig) -> float | None:
    """Compute electrical power (W) for display from direct sensor or irradiance model."""
    if m.irradiance_measured_wm2 is not None:
        g = m.irradiance_measured_wm2
    else:
        g = irradiance_wm2(m.current_a, m.panel_temperature_c, cfg)
    if g is not None:
        t_panel = m.panel_temperature_c if m.panel_temperature_c is not None else 25.0
        gamma = cfg.temp_coefficient_pct_per_c / 100.0
        f_t = max(0.5, 1.0 + gamma * (t_panel - 25.0))
        watts = (cfg.panel_power_w * cfg.panel_count) * (g / 1000.0) * f_t * cfg.performance_ratio
        return max(0.0, watts)
    if m.power_w is not None:
        return max(0.0, m.power_w)
    return None


def build_range_overview(
    rows: list[Measurement],
    cfg: SystemConfig,
    *,
    start_utc: datetime,
    end_utc: datetime,
    timezone: str,
    bucket: BucketMode = "auto",
    source: str,
) -> dict[str, Any]:
    tz = ZoneInfo(timezone)
    span_days = max(0.1, (end_utc - start_utc).total_seconds() / 86400.0)
    if bucket == "auto":
        if span_days <= 2.2:
            resolved_bucket: Literal["hour", "day", "month"] = "hour"
        elif span_days <= 95.0:
            resolved_bucket = "day"
        else:
            resolved_bucket = "month"
    else:
        resolved_bucket = bucket

    # Integrate total energy (kWh) across consecutive valid samples
    total_energy_kwh = 0.0
    max_gap_s = max(3.5 * cfg.measurement_interval_s, 5400.0)
    prev_m: Measurement | None = None
    prev_w: float | None = None

    groups: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"watts": [], "peak_w": 0.0, "t_panel": [], "t_amb": [], "energy_kwh": 0.0, "label": ""}
    )

    all_watts: list[float] = []
    all_t_panel: list[float] = []
    all_t_amb: list[float] = []
    peak_watts = 0.0
    peak_panel_c: float | None = None

    # Pre-populate 24 hours for single-day view so dawn (0 W) -> noon parabola -> dusk (0 W) is complete
    if resolved_bucket == "hour" and span_days <= 1.5:
        local_day = start_utc.astimezone(tz).date()
        for h in range(5, 21):
            key = f"{local_day.isoformat()}T{h:02d}"
            groups[key]["label"] = f"{h:02d}:00"

    for m in rows:
        lt = m.timestamp_utc.astimezone(tz)
        w = _instant_watts(m, cfg)

        if prev_m is not None and w is not None and prev_w is not None:
            dt_s = (m.timestamp_utc - prev_m.timestamp_utc).total_seconds()
            if 0 < dt_s <= max_gap_s:
                e_step_kwh = 0.5 * (prev_w + w) * (dt_s / 3600.0) / 1000.0
                total_energy_kwh += e_step_kwh
            else:
                e_step_kwh = 0.0
        else:
            e_step_kwh = 0.0

        prev_m, prev_w = m, w

        if resolved_bucket == "hour":
            key = f"{lt.date().isoformat()}T{lt.hour:02d}"
            label = (
                f"{lt.hour:02d}:00"
                if span_days <= 1.5
                else f"{lt.day:02d} {MONTH_TR[lt.month]} {lt.hour:02d}:00"
            )
        elif resolved_bucket == "day":
            key = lt.date().isoformat()
            label = f"{lt.day:02d} {MONTH_TR[lt.month]}"
        else:
            key = f"{lt.year:04d}-{lt.month:02d}"
            label = f"{MONTH_TR[lt.month]} {lt.year}"

        g = groups[key]
        g["label"] = label
        g["energy_kwh"] += e_step_kwh
        if w is not None:
            g["watts"].append(w)
            if w > g["peak_w"]:
                g["peak_w"] = w
            if w > 1.0:
                all_watts.append(w)
            if w > peak_watts:
                peak_watts = w
        if m.panel_temperature_c is not None:
            g["t_panel"].append(m.panel_temperature_c)
            all_t_panel.append(m.panel_temperature_c)
            if peak_panel_c is None or m.panel_temperature_c > peak_panel_c:
                peak_panel_c = m.panel_temperature_c
        if m.ambient_temperature_c is not None:
            g["t_amb"].append(m.ambient_temperature_c)
            all_t_amb.append(m.ambient_temperature_c)

    sorted_keys = sorted(groups.keys())
    raw_power_pts: list[float | None] = []
    raw_tpanel_pts: list[float | None] = []
    raw_tamb_pts: list[float | None] = []

    for k in sorted_keys:
        g = groups[k]
        if g["watts"]:
            raw_power_pts.append(round(sum(g["watts"]) / len(g["watts"]), 1))
        elif resolved_bucket == "hour" and rows:
            raw_power_pts.append(0.0)
        else:
            raw_power_pts.append(None)

        raw_tpanel_pts.append(
            round(sum(g["t_panel"]) / len(g["t_panel"]), 1) if g["t_panel"] else None
        )
        raw_tamb_pts.append(
            round(sum(g["t_amb"]) / len(g["t_amb"]), 1) if g["t_amb"] else None
        )

    smooth_power = _smooth_series(raw_power_pts, passes=1 if resolved_bucket != "hour" else 2)
    smooth_tpanel = _smooth_series(raw_tpanel_pts, passes=1)
    smooth_tamb = _smooth_series(raw_tamb_pts, passes=1)

    points: list[dict[str, Any]] = []
    for idx, k in enumerate(sorted_keys):
        g = groups[k]
        peak_pt = (
            round(g["peak_w"], 1)
            if g["watts"]
            else (0.0 if resolved_bucket == "hour" and rows else None)
        )
        points.append({
            "key": k,
            "label": g["label"],
            "power_w": smooth_power[idx],
            "peak_power_w": peak_pt,
            "energy_kwh": round(g["energy_kwh"], 2),
            "panel_temperature_c": smooth_tpanel[idx],
            "ambient_temperature_c": smooth_tamb[idx],
        })

    return {
        "source": source,
        "bucket": resolved_bucket,
        "start_utc": start_utc.astimezone(UTC).isoformat(),
        "end_utc": end_utc.astimezone(UTC).isoformat(),
        "sample_count": len(rows),
        "installed_watts": round(cfg.panel_power_w * cfg.panel_count, 0),
        "summary": {
            "total_energy_kwh": round(total_energy_kwh, 2),
            "avg_daylight_power_w": round(sum(all_watts) / len(all_watts), 1) if all_watts else None,
            "peak_power_w": round(peak_watts, 1) if rows else None,
            "avg_panel_temperature_c": round(sum(all_t_panel) / len(all_t_panel), 1) if all_t_panel else None,
            "peak_panel_temperature_c": round(peak_panel_c, 1) if peak_panel_c is not None else None,
            "avg_ambient_temperature_c": round(sum(all_t_amb) / len(all_t_amb), 1) if all_t_amb else None,
        },
        "points": points,
    }
