"""Energy engine.

1. Reference panel electrical energy:  E[Wh] = Σ ½(P_k + P_k+1)·Δt / 3600
   Intervals longer than `max_gap_s` are treated as *gaps* (unknown), never
   as production.
2. Plane-of-array irradiance from the reference panel short-circuit current:
       G[W/m²] = I_sc / (k · (1 + α·(T_panel − 25)))
   k = ref_isc_per_wm2_a (A per W/m²). Until calibrated, k is the nameplate
   assumption and every derived value is labelled "estimated".
3. Planned system daily energy (measured_reference method):
       E_day[kWh] = P_stc[kWp] · H_poa[kWh/m²] / (1 kW/m²) · f_T · PR
       f_T = 1 + γ·(T_w − 25),  T_w = irradiance-weighted panel temperature
   The reference panel must be mounted at the planned tilt/azimuth; site
   shading is therefore already contained in H_poa (not applied twice).
4. Theoretical method (no measurement):  E_day = P_stc · PSH · PR
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.schemas import (
    DailyEnergy,
    EstimateRequest,
    EstimateResponse,
    LabeledValue,
    Measurement,
    SystemConfig,
)
from app.services.solar_position import is_daylight

COMPLETE_DAY_COVERAGE = 0.90
DAYLIGHT_STEP_S = 300


class EstimateUnavailable(ValueError):
    pass


def max_gap_for(cfg: SystemConfig) -> float:
    return max(3.0 * cfg.measurement_interval_s, 30.0)


def irradiance_wm2(current_a: float | None, panel_temp_c: float | None, cfg: SystemConfig) -> float | None:
    """Irradiance estimate from reference short-circuit current. None if unknown."""
    if current_a is None or cfg.reference_mode != "short_circuit":
        return None
    temp = 25.0 if panel_temp_c is None else panel_temp_c
    denom = cfg.ref_isc_per_wm2_a * (1.0 + cfg.ref_isc_temp_coeff_per_c * (temp - 25.0))
    if denom <= 0:
        return None
    return max(0.0, current_a / denom)


@dataclass
class _DayAcc:
    energy_wh: float = 0.0
    insolation_wh_m2: float = 0.0
    has_irradiance: bool = False
    covered_daylight_s: float = 0.0
    weighted_temp_sum: float = 0.0
    weight_sum: float = 0.0
    samples: int = 0
    gaps: int = 0
    sources: set[str] = field(default_factory=set)


def _daylight_seconds(day: date, tz: ZoneInfo, lat: float, lon: float) -> float:
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    end = start + timedelta(days=1)
    t, total = start, 0.0
    step = timedelta(seconds=DAYLIGHT_STEP_S)
    while t < end:
        if is_daylight(t + step / 2, lat, lon):
            total += DAYLIGHT_STEP_S
        t += step
    return total


def daily_energy(
    measurements: list[Measurement],
    cfg: SystemConfig,
    *,
    latitude_deg: float,
    longitude_deg: float,
    timezone: str,
) -> list[DailyEnergy]:
    """measurements must be sorted by timestamp (ascending)."""
    tz = ZoneInfo(timezone)
    max_gap = max_gap_for(cfg)
    days: dict[date, _DayAcc] = defaultdict(_DayAcc)

    for m in measurements:
        acc = days[m.timestamp_utc.astimezone(tz).date()]
        acc.samples += 1
        acc.sources.add(m.data_source)

    for a, b in zip(measurements, measurements[1:], strict=False):
        dt = (b.timestamp_utc - a.timestamp_utc).total_seconds()
        if dt <= 0:
            continue
        acc = days[a.timestamp_utc.astimezone(tz).date()]
        if dt > max_gap:
            acc.gaps += 1
            continue
        mid = a.timestamp_utc + timedelta(seconds=dt / 2)
        daylight = is_daylight(mid, latitude_deg, longitude_deg)

        if a.power_w is not None and b.power_w is not None:
            acc.energy_wh += 0.5 * (a.power_w + b.power_w) * dt / 3600.0

        ga = irradiance_wm2(a.current_a, a.panel_temperature_c, cfg)
        gb = irradiance_wm2(b.current_a, b.panel_temperature_c, cfg)
        if ga is not None and gb is not None:
            g = 0.5 * (ga + gb)
            acc.insolation_wh_m2 += g * dt / 3600.0
            acc.has_irradiance = True
            if daylight:
                acc.covered_daylight_s += dt
            temps = [t for t in (a.panel_temperature_c, b.panel_temperature_c) if t is not None]
            if temps and g > 0:
                acc.weighted_temp_sum += g * dt * (sum(temps) / len(temps))
                acc.weight_sum += g * dt
        elif cfg.reference_mode == "resistive_load" and a.power_w is not None and daylight:
            acc.covered_daylight_s += dt

    kind = "calculated" if cfg.ref_calibrated else "estimated"
    result: list[DailyEnergy] = []
    for day in sorted(days):
        acc = days[day]
        total_daylight = _daylight_seconds(day, tz, latitude_deg, longitude_deg)
        coverage = min(1.0, acc.covered_daylight_s / total_daylight) if total_daylight else 0.0
        result.append(
            DailyEnergy(
                date=day.isoformat(),
                reference_energy_wh=round(acc.energy_wh, 6),
                insolation_kwh_m2=round(acc.insolation_wh_m2 / 1000.0, 4) if acc.has_irradiance else None,
                insolation_kind=kind,
                daylight_coverage=round(coverage, 4),
                sample_count=acc.samples,
                gap_count=acc.gaps,
                irradiance_weighted_panel_temp_c=(
                    round(acc.weighted_temp_sum / acc.weight_sum, 2) if acc.weight_sum else None
                ),
                complete=coverage >= COMPLETE_DAY_COVERAGE,
                data_source=",".join(sorted(acc.sources)) or "none",
            )
        )
    return result


def estimate_production(
    req: EstimateRequest, cfg: SystemConfig, days: list[DailyEnergy], data_source: str
) -> EstimateResponse:
    kwp = req.panel_power_w * req.panel_count / 1000.0
    warnings: list[str] = []
    inputs: dict[str, LabeledValue] = {
        "panel_power_w": LabeledValue(value=req.panel_power_w, unit="W", kind="assumed",
                                      note="User input (planned module STC power)"),
        "panel_count": LabeledValue(value=req.panel_count, unit="pcs", kind="assumed"),
        "installed_kwp": LabeledValue(value=round(kwp, 4), unit="kWp", kind="calculated"),
        "performance_ratio": LabeledValue(value=req.performance_ratio, unit="-", kind="assumed",
                                          note="Inverter, cable, soiling, mismatch"),
    }

    usable = [d for d in days if d.complete and d.insolation_kwh_m2 is not None][-req.days:]
    method = req.method
    if method == "auto":
        method = "measured_reference" if usable else "theoretical_psh"

    if method == "measured_reference":
        if cfg.reference_mode != "short_circuit":
            raise EstimateUnavailable(
                "measured_reference needs reference_mode=short_circuit (Isc ∝ irradiance)"
            )
        if not usable:
            raise EstimateUnavailable(
                "No complete measured day (≥90 % daylight coverage) available yet"
            )
        gamma = req.temp_coefficient_pct_per_c / 100.0
        per_day: list[dict[str, float | str]] = []
        for d in usable:
            t_w = d.irradiance_weighted_panel_temp_c
            f_t = 1.0 + gamma * ((t_w if t_w is not None else 25.0) - 25.0)
            e = kwp * (d.insolation_kwh_m2 or 0.0) * f_t * req.performance_ratio
            per_day.append({
                "date": d.date,
                "insolation_kwh_m2": d.insolation_kwh_m2 or 0.0,
                "temperature_factor": round(f_t, 4),
                "energy_kwh": round(e, 3),
            })
        mean_kwh = sum(float(p["energy_kwh"]) for p in per_day) / len(per_day)
        mean_h = sum(float(p["insolation_kwh_m2"]) for p in per_day) / len(per_day)
        inputs["insolation_kwh_m2_mean"] = LabeledValue(
            value=round(mean_h, 4), unit="kWh/m²/day",
            kind="calculated" if cfg.ref_calibrated else "estimated",
            note="From reference panel Isc" + ("" if cfg.ref_calibrated else " (UNCALIBRATED factor)"),
        )
        inputs["temp_coefficient_pct_per_c"] = LabeledValue(
            value=req.temp_coefficient_pct_per_c, unit="%/°C", kind="assumed")
        inputs["reference_panel_temperature"] = LabeledValue(
            value=None, unit="°C", kind="measured",
            note="Irradiance-weighted daily value, see per_day")
        inputs["daily_energy_kwh"] = LabeledValue(
            value=round(mean_kwh, 3), unit="kWh/day", kind="estimated")
        if not cfg.ref_calibrated:
            warnings.append(
                "Referans panel kalibre edilmedi: ışınım etiket değerinden tahmin ediliyor, "
                "sonuç ±%20+ hatalı olabilir (docs/calibration.md)."
            )
        if len(usable) < 30:
            warnings.append(
                f"Yalnızca {len(usable)} tam gün veri var; bu süre yıllık üretimi temsil etmez."
            )
        warnings.append(
            "Referans panel planlanan eğim/yönde monte edilmiş olmalı; model eğim/yön dönüşümü yapmaz."
        )
        calibration = "calibrated" if cfg.ref_calibrated else "uncalibrated"
    else:
        if req.peak_sun_hours is None:
            raise EstimateUnavailable(
                "theoretical_psh needs peak_sun_hours (no complete measured day available)"
            )
        mean_kwh = kwp * req.peak_sun_hours * req.performance_ratio
        per_day = []
        inputs["peak_sun_hours"] = LabeledValue(
            value=req.peak_sun_hours, unit="h", kind="assumed",
            note="User-supplied equivalent full-sun hours; NOT measured at the site")
        inputs["daily_energy_kwh"] = LabeledValue(
            value=round(mean_kwh, 3), unit="kWh/day", kind="estimated")
        warnings.append("Teorik hesap: saha ölçümü kullanılmadı.")
        calibration = "not_applicable"

    if "simulation" in data_source and method == "measured_reference":
        warnings.insert(0, "SİMÜLASYON VERİSİ: sonuç gerçek saha ölçümüne dayanmıyor.")

    return EstimateResponse(
        method=method,
        installed_kwp=round(kwp, 4),
        daily_energy_kwh=round(mean_kwh, 3),
        per_day=per_day,
        inputs=inputs,
        calibration_status=calibration,
        data_source=data_source if method == "measured_reference" else "none",
        warnings=warnings,
    )
