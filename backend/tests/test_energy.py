from datetime import UTC, datetime, timedelta

import pytest

from app.models.schemas import DailyEnergy, EstimateRequest, SystemConfig
from app.services.energy_calculator import (
    EstimateUnavailable,
    daily_energy,
    estimate_production,
    irradiance_wm2,
)
from tests.conftest import make_m, series

LAT, LON, TZ = 41.735, 27.225, "Europe/Istanbul"
CFG = SystemConfig()


def run(ms, cfg=CFG):
    return daily_energy(ms, cfg, latitude_deg=LAT, longitude_deg=LON, timezone=TZ)


def test_power_is_v_times_i():
    m = make_m(datetime(2026, 6, 21, 9, tzinfo=UTC), 0, voltage_v=10.0, current_a=0.15)
    assert m.power_w == pytest.approx(1.5)


def test_constant_power_one_hour_gives_exact_wh():
    # 0.02 V * 0.075 A = 1.5 mW ... use explicit 2 W
    ms = series(datetime(2026, 6, 21, 9, tzinfo=UTC), 3600, voltage_v=20.0, current_a=0.1)
    (day,) = run(ms)
    assert day.reference_energy_wh == pytest.approx(2.0, rel=1e-6)


def test_irregular_intervals_use_real_timestamps():
    t0 = datetime(2026, 6, 21, 9, tzinfo=UTC)
    offsets = [0, 3, 10, 12, 20]  # irregular, all < max gap
    ms = [make_m(t0 + timedelta(seconds=s), i, voltage_v=10.0, current_a=0.36) for i, s in enumerate(offsets)]
    (day,) = run(ms)
    assert day.reference_energy_wh == pytest.approx(3.6 * 20 / 3600)


def test_gap_is_not_counted_as_production():
    t0 = datetime(2026, 6, 21, 9, tzinfo=UTC)
    a = series(t0, 600, voltage_v=20.0, current_a=0.1)
    b = series(t0 + timedelta(hours=2), 600, voltage_v=20.0, current_a=0.1)
    for i, m in enumerate(b):
        m.sequence = 1000 + i
    (day,) = run(a + b)
    assert day.reference_energy_wh == pytest.approx(2.0 * 1200 / 3600, abs=1e-6)
    assert day.gap_count == 1


def test_missing_values_break_the_interval():
    t0 = datetime(2026, 6, 21, 9, tzinfo=UTC)
    ms = series(t0, 20, voltage_v=20.0, current_a=0.1)
    ms[2] = make_m(ms[2].timestamp_utc, 2, current_a=None)
    (day,) = run(ms)
    # intervals touching sample 2 (10 s total) are excluded
    assert day.reference_energy_wh == pytest.approx(2.0 * 10 / 3600, abs=1e-6)


def test_irradiance_from_isc_and_temperature_correction():
    assert irradiance_wm2(0.075, 25.0, CFG) == pytest.approx(500.0)
    hot = irradiance_wm2(0.075, 45.0, CFG)
    assert hot == pytest.approx(500.0 / (1 + 0.0005 * 20))
    assert irradiance_wm2(None, 25.0, CFG) is None
    assert irradiance_wm2(0.075, 25.0, CFG.model_copy(update={"reference_mode": "resistive_load"})) is None


def test_insolation_one_hour_500wm2():
    ms = series(datetime(2026, 6, 21, 9, tzinfo=UTC), 3600, current_a=0.075)
    (day,) = run(ms)
    assert day.insolation_kwh_m2 == pytest.approx(0.5, rel=1e-6)
    assert day.insolation_kind == "estimated"  # uncalibrated default
    cal = CFG.model_copy(update={"ref_calibrated": True})
    assert run(ms, cal)[0].insolation_kind == "calculated"


def test_full_day_complete_and_night_gap_tolerated():
    local_midnight_utc = datetime(2026, 6, 20, 21, tzinfo=UTC)  # 00:00 Istanbul
    full = series(local_midnight_utc, 86395, step=5)
    (day,) = run(full)
    assert day.date == "2026-06-21" and day.complete and day.daylight_coverage > 0.99
    # remove 01:00-04:00 local (night) -> still complete
    night_cut = [m for m in full if not (3600 <= (m.timestamp_utc - local_midnight_utc).seconds < 4 * 3600)]
    assert run(night_cut)[0].complete
    # remove 11:00-13:00 local (daylight) -> incomplete
    day_cut = [m for m in full
               if not (11 * 3600 <= (m.timestamp_utc - local_midnight_utc).seconds < 13 * 3600)]
    d = run(day_cut)[0]
    assert not d.complete and d.daylight_coverage < 0.9


def _day(h, t=25.0, complete=True):
    return DailyEnergy(date="2026-06-21", reference_energy_wh=0, insolation_kwh_m2=h,
                       insolation_kind="estimated",
                       daylight_coverage=1.0 if complete else 0.5, sample_count=1, gap_count=0,
                       irradiance_weighted_panel_temp_c=t, complete=complete, data_source="hardware")


def test_theoretical_estimate_matches_hand_calc():
    req = EstimateRequest(panel_power_w=450, panel_count=8, performance_ratio=0.8,
                          method="theoretical_psh", peak_sun_hours=4.25)
    r = estimate_production(req, CFG, [], "hardware")
    assert r.installed_kwp == pytest.approx(3.6)
    assert r.daily_energy_kwh == pytest.approx(12.24)
    assert r.inputs["peak_sun_hours"].kind == "assumed"
    assert r.calibration_status == "not_applicable"


def test_measured_estimate_with_temperature_factor():
    req = EstimateRequest(panel_power_w=450, panel_count=8, performance_ratio=0.8)
    r = estimate_production(req, CFG, [_day(5.0, 25.0)], "hardware")
    assert r.method == "measured_reference"
    assert r.daily_energy_kwh == pytest.approx(3.6 * 5.0 * 0.8)
    r_hot = estimate_production(req, CFG, [_day(5.0, 45.0)], "hardware")
    assert r_hot.daily_energy_kwh == pytest.approx(3.6 * 5.0 * 0.8 * (1 - 0.0035 * 20), abs=1e-3)
    assert r.calibration_status == "uncalibrated"
    assert any("kalibre" in w for w in r.warnings)
    assert {v.kind for v in r.inputs.values()} >= {"assumed", "calculated", "estimated"}


def test_incomplete_days_are_ignored_and_auto_falls_back():
    req = EstimateRequest(panel_power_w=450, panel_count=8, peak_sun_hours=3.0)
    r = estimate_production(req, CFG, [_day(5.0, complete=False)], "hardware")
    assert r.method == "theoretical_psh"
    with pytest.raises(EstimateUnavailable):
        estimate_production(EstimateRequest(panel_power_w=450, panel_count=8), CFG, [], "hardware")


def test_simulation_source_is_flagged():
    req = EstimateRequest(panel_power_w=450, panel_count=8)
    r = estimate_production(req, CFG, [_day(4.0)], "simulation")
    assert r.warnings[0].startswith("SİMÜLASYON")
