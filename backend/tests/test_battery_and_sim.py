from datetime import UTC, datetime, timedelta

import pytest

from app.services.battery_monitor import soc_from_voltage
from app.services.solar_position import sun_elevation_deg
from app.services.validator import validate_message
from app.simulation.simulator import Simulator

LAT, LON = 41.735, 27.225


@pytest.mark.parametrize("v,cells,expected", [(4.2, 1, 100), (3.0, 1, 0), (3.77, 1, 50),
                                              (12.6, 3, 100), (11.31, 3, 50)])
def test_soc_curve(v, cells, expected):
    assert soc_from_voltage(v, cells) == pytest.approx(expected, abs=0.5)


def test_soc_rejects_wrong_topology():
    assert soc_from_voltage(12.0, 1) is None  # 12 V on a 1S config -> implausible
    assert soc_from_voltage(None, 1) is None


def test_sun_elevation_sanity():
    noon_june = datetime(2026, 6, 21, 10, 10, tzinfo=UTC)  # ~13:10 local solar noon
    assert 68 < sun_elevation_deg(noon_june, LAT, LON) < 73  # 90 - 41.7 + 23.44 ≈ 71.7
    assert sun_elevation_deg(datetime(2026, 6, 21, 0, tzinfo=UTC), LAT, LON) < 0


def test_simulator_is_deterministic_and_valid():
    t = datetime(2026, 6, 21, 10, tzinfo=UTC)
    a = Simulator(latitude_deg=LAT, longitude_deg=LON, seed=1, fault_rate=0, drop_rate=0)
    b = Simulator(latitude_deg=LAT, longitude_deg=LON, seed=1, fault_rate=0, drop_rate=0)
    assert a.sample(t) == b.sample(t)
    r = validate_message(a.sample(t + timedelta(seconds=5)), received_at=t, data_source="simulation")
    assert r.issues == []


def test_simulator_physically_plausible():
    sim = Simulator(latitude_deg=LAT, longitude_deg=LON, seed=3, fault_rate=0, drop_rate=0)
    night = sim.sample(datetime(2026, 11, 26, 0, tzinfo=UTC))
    assert night["current_a"] < 0.001
    noon = max(sim.sample(datetime(2026, 6, 21, 10, tzinfo=UTC) + timedelta(seconds=5 * i))["current_a"]
               for i in range(50))
    assert 0.03 < noon <= 0.16  # never above nameplate 150 mA
    assert -20 < night["ambient_temperature_c"] < 30
