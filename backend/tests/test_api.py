from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from scripts.seed_simulation import seed


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["db_ok"] is True


def test_empty_state(client):
    assert client.get("/api/measurements/latest").status_code == 404
    st = client.get("/api/device/status").json()
    assert st["device_status"] == "offline" and st["data_mode"] == "simulation"
    assert client.get("/api/energy/daily").json() == []
    assert client.get("/api/measurements/history").json()["total"] == 0


def test_config_get_put_with_token(client):
    cfg = client.get("/api/config").json()
    assert cfg["installed_kwp"] == 3.6
    cfg.update(panel_power_w=500, panel_count=10)
    body = {k: v for k, v in cfg.items() if k not in ("installed_kwp", "data_mode")}
    assert client.put("/api/config", json=body).status_code == 401
    assert client.put("/api/config", json=body, headers={"X-API-Token": "wrong"}).status_code == 401
    r = client.put("/api/config", json=body, headers={"X-API-Token": "test-token"})
    assert r.status_code == 200 and r.json()["installed_kwp"] == 5.0
    assert client.get("/api/config").json()["panel_count"] == 10
    kinds = [e["kind"] for e in client.get("/api/events").json()["events"]]
    assert "config_changed" in kinds


def test_config_validation_errors(client):
    bad = {"panel_power_w": -5}
    assert client.put("/api/config", json=bad, headers={"X-API-Token": "test-token"}).status_code == 422
    hyst = {"fan_on_c": 40, "fan_off_c": 39.5}
    assert client.put("/api/config", json=hyst, headers={"X-API-Token": "test-token"}).status_code == 422


def test_put_disabled_without_token(settings):
    settings.api_token = ""
    with TestClient(create_app(settings)) as c:
        assert c.put("/api/config", json={}, headers={"X-API-Token": ""}).status_code == 403


def test_estimate_theoretical_and_errors(client):
    r = client.post("/api/simulation/estimate", json={
        "panel_power_w": 450, "panel_count": 8, "method": "theoretical_psh", "peak_sun_hours": 4.25})
    assert r.status_code == 200 and r.json()["daily_energy_kwh"] == pytest.approx(12.24)
    r = client.post("/api/simulation/estimate", json={"panel_power_w": 450, "panel_count": 8})
    assert r.status_code == 422  # no data, no PSH
    r = client.post("/api/simulation/estimate", json={"panel_power_w": 0, "panel_count": 8})
    assert r.status_code == 422


def test_history_rejects_naive_datetime(client):
    assert client.get("/api/measurements/history?start=2026-11-26T00:00:00").status_code == 422


def test_seeded_data_flows_through_api(settings):
    seed(settings, days=2.2, interval_s=60, end=datetime.now(UTC))
    with TestClient(create_app(settings)) as c:
        latest = c.get("/api/measurements/latest").json()
        assert latest["data_source"] == "simulation"
        page = c.get("/api/measurements/history?limit=10&order=desc").json()
        assert page["total"] > 3000 and len(page["items"]) == 10
        daily = c.get("/api/energy/daily?days=3").json()
        assert len(daily) >= 2 and all(d["data_source"] == "simulation" for d in daily)
        csv_text = c.get("/api/measurements/export.csv").text
        assert csv_text.startswith("timestamp_utc,sequence")
        status = c.get("/api/device/status").json()
        assert status["battery"]["soc_pct_estimated"] is not None
        assert c.get("/api/events").json()["sensor_errors"] is not None
