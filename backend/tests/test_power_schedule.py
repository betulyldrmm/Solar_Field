from datetime import UTC, date, datetime, timedelta

from app.models.schemas import SystemConfig
from app.services.bridge_service import SimulationRunner
from app.services.ingest import IngestService
from app.services.power_schedule import is_scheduled_on, next_transitions, sun_events
from app.simulation.simulator import Simulator
from app.storage.database import Database

LAT, LON, TZ = 41.735, 27.225, "Europe/Istanbul"  # Kırklareli


def test_sunrise_sunset_kirklareli_winter_and_summer():
    # Reference: Istanbul 08:23/17:39 (21 Dec) and 05:32/20:39 (21 Jun) local; Kırklareli is
    # 1.8° further west -> ~7 min later: ~08:30/17:46 and ~05:39/20:46. Tolerance 10 min.
    w = sun_events(date(2026, 12, 21), LAT, LON, TZ)
    s = sun_events(date(2026, 6, 21), LAT, LON, TZ)
    def loc(t):  # Turkey is UTC+3 all year
        return t + timedelta(hours=3)
    assert abs((loc(w.sunrise_utc) - datetime(2026, 12, 21, 8, 30, tzinfo=UTC)).total_seconds()) < 10 * 60
    assert abs((loc(w.sunset_utc) - datetime(2026, 12, 21, 17, 46, tzinfo=UTC)).total_seconds()) < 10 * 60
    assert abs((loc(s.sunrise_utc) - datetime(2026, 6, 21, 5, 39, tzinfo=UTC)).total_seconds()) < 10 * 60
    assert abs((loc(s.sunset_utc) - datetime(2026, 6, 21, 20, 46, tzinfo=UTC)).total_seconds()) < 10 * 60


def test_schedule_on_by_day_off_at_night_with_margins():
    cfg = SystemConfig()
    ev = sun_events(date(2026, 12, 21), LAT, LON, TZ)
    on = lambda t: is_scheduled_on(t, cfg, LAT, LON, TZ)  # noqa: E731
    assert on(ev.sunrise_utc - timedelta(minutes=29))       # 30 min wake margin
    assert not on(ev.sunrise_utc - timedelta(minutes=31))
    assert on(ev.sunset_utc + timedelta(minutes=29))        # 30 min shutdown margin
    assert not on(ev.sunset_utc + timedelta(minutes=31))
    assert not on(datetime(2026, 12, 21, 0, 0, tzinfo=UTC))  # 03:00 local


def test_disabled_means_always_on():
    cfg = SystemConfig(night_shutdown_enabled=False)
    assert is_scheduled_on(datetime(2026, 12, 21, 0, tzinfo=UTC), cfg, LAT, LON, TZ)
    assert next_transitions(datetime(2026, 12, 21, 0, tzinfo=UTC), cfg, LAT, LON, TZ) == (None, None)


def test_next_transitions_at_night_and_day():
    cfg = SystemConfig()
    night = datetime(2026, 12, 21, 0, 0, tzinfo=UTC)
    shutdown, wake = next_transitions(night, cfg, LAT, LON, TZ)
    assert night < wake < shutdown and (shutdown - wake) < timedelta(hours=11)
    noon = datetime(2026, 12, 21, 10, 0, tzinfo=UTC)
    shutdown, wake = next_transitions(noon, cfg, LAT, LON, TZ)
    assert noon < shutdown < wake  # off this evening, on tomorrow morning


def test_simulator_off_at_night_no_rows_no_drain_new_boot(tmp_path):
    db = Database(tmp_path / "x.db")
    ingest = IngestService(db, None, "simulation")
    sim = Simulator(latitude_deg=LAT, longitude_deg=LON, seed=1, fault_rate=0, drop_rate=0)
    cfg = SystemConfig()
    runner = SimulationRunner(sim, ingest, 300, is_on=lambda t: is_scheduled_on(t, cfg, LAT, LON, TZ))
    t = datetime(2026, 12, 21, 12, 0, tzinfo=UTC)  # 15:00 local, before sunset
    boots, night_rows = set(), 0
    soc_at_shutdown = None
    for _ in range(int(24 * 3600 / 300)):
        stored = runner.step(t)
        on = is_scheduled_on(t, cfg, LAT, LON, TZ)
        if not on and soc_at_shutdown is None:
            soc_at_shutdown = sim.soc
        if stored:
            boots.add(sim.boot_id)
            night_rows += 0 if on else 1
        t += timedelta(seconds=300)
    assert night_rows == 0
    assert len(boots) == 2                      # rebooted once in the morning
    assert sim.soc >= soc_at_shutdown - 0.5     # nothing drawn overnight


def test_fan_power_follows_3s_pack_voltage():
    sim = Simulator(latitude_deg=LAT, longitude_deg=LON, seed=2, fault_rate=0, drop_rate=0)
    assert sim.series == 3
    msg = sim.sample(datetime(2026, 7, 1, 10, 0, tzinfo=UTC))
    assert 9.0 <= msg["battery_voltage_v"] <= 12.7
