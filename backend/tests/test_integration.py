"""End-to-end (simulation): simulator -> ingest/validation -> SQLite + CSV
-> energy calculation -> FastAPI estimate. The React step is covered in Phase 4."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services.bridge_service import HardwareBridgeAdapter, SimulationRunner
from app.services.ingest import IngestService
from app.simulation.simulator import Simulator
from app.storage.csv_logger import CsvLogger
from app.storage.database import Database


def test_full_chain_simulation(settings):
    db = Database(settings.db_path)
    ingest = IngestService(db, CsvLogger(settings.csv_dir, min_free_mb=0), "simulation")
    sim = Simulator(latitude_deg=settings.latitude_deg, longitude_deg=settings.longitude_deg, seed=7)
    runner = SimulationRunner(sim, ingest, 30)
    # three full local days (Istanbul midnight = 21:00 UTC), 30 s resolution
    t = datetime(2026, 6, 19, 21, tzinfo=UTC)
    end = t + timedelta(days=3)
    while t < end:
        runner.step(t)
        t += timedelta(seconds=30)
    assert db.count_measurements() > 8000
    assert len(list(settings.csv_dir.glob("*.csv"))) >= 3
    db.close()

    # API reads the persisted data; check estimate uses measured days
    from app.api import routes
    from app.models.schemas import EstimateRequest
    from app.services.energy_calculator import daily_energy, estimate_production

    db = Database(settings.db_path)
    days = daily_energy(db.query_measurements(source="simulation"), db.load_config(),
                        latitude_deg=settings.latitude_deg, longitude_deg=settings.longitude_deg,
                        timezone=settings.timezone)
    complete = [d for d in days if d.complete]
    assert len(complete) == 3
    for d in complete:
        # June, Thrace, clouds 0.15-1.0, uncalibrated (true factor 0.93) -> plausible band
        assert 2.0 < d.insolation_kwh_m2 < 8.5
    r = estimate_production(EstimateRequest(panel_power_w=450, panel_count=8), db.load_config(),
                            days, "simulation")
    assert r.method == "measured_reference"
    assert 5 < r.daily_energy_kwh < 25  # 3.6 kWp in June
    assert r.warnings[0].startswith("SİMÜLASYON")
    assert routes is not None
    db.close()


def test_duplicates_and_bad_messages_via_bridge_adapter(settings):
    db = Database(settings.db_path)
    adapter = HardwareBridgeAdapter(IngestService(db, None, "hardware"))
    good = '{"schema_version":1,"boot_id":"hw1","sequence":1,"voltage_v":0.03,"current_a":0.1}'
    assert adapter.on_measurement(good) is True
    assert adapter.on_measurement(good) is False  # duplicate ignored
    assert adapter.on_measurement("{not json") is False
    assert adapter.on_measurement('{"schema_version":9,"sequence":2}') is False
    gap = '{"schema_version":1,"boot_id":"hw1","sequence":5,"voltage_v":0.03,"current_a":0.1}'
    assert adapter.on_measurement(gap) is True
    kinds = [e["kind"] for e in db.recent_events()]
    assert kinds.count("message_rejected") == 2 and "sequence_gap" in kinds
    assert db.count_measurements(source="hardware") == 2


def test_live_simulator_background_task(settings):
    settings.run_simulator = True
    with TestClient(create_app(settings)) as c:
        import time
        for _ in range(40):
            if c.get("/api/measurements/latest").status_code == 200:
                break
            time.sleep(0.05)
        r = c.get("/api/measurements/latest")
        assert r.status_code == 200 and r.json()["data_source"] == "simulation"
        assert c.get("/api/device/status").json()["device_status"] == "online"


@pytest.mark.parametrize("mode", ["hardware"])
def test_hardware_mode_never_shows_simulation_rows(settings, mode):
    db = Database(settings.db_path)
    IngestService(db, None, "simulation").ingest(
        {"schema_version": 1, "boot_id": "s", "sequence": 1, "current_a": 0.1, "voltage_v": 0.03})
    db.close()
    settings.data_mode = mode
    with TestClient(create_app(settings)) as c:
        assert c.get("/api/measurements/latest").status_code == 404
