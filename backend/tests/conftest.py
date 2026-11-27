from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.config import Settings
from app.models.schemas import Measurement


def make_m(t: datetime, seq: int, *, current_a=0.075, voltage_v=0.02, panel_t=25.0, **kw) -> Measurement:
    power = None if (current_a is None or voltage_v is None) else voltage_v * current_a
    data = dict(timestamp_utc=t, boot_id="test", sequence=seq, voltage_v=voltage_v, current_a=current_a,
                power_w=power, panel_temperature_c=panel_t, data_source="simulation")
    data.update(kw)
    return Measurement(**data)


def series(start: datetime, seconds: int, step: int = 5, **kw) -> list[Measurement]:
    return [make_m(start + timedelta(seconds=s), i, **kw) for i, s in enumerate(range(0, seconds + 1, step))]


@pytest.fixture
def settings(tmp_path) -> Settings:
    s = Settings()
    s.data_dir = tmp_path / "data"
    s.data_mode = "simulation"
    s.run_simulator = False
    s.api_token = "test-token"
    s.min_free_disk_mb = 0
    return s


@pytest.fixture
def utc():
    return UTC
