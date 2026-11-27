import csv
from datetime import UTC, datetime, timedelta

from app.models.schemas import SystemConfig
from app.storage.csv_logger import CSV_FIELDS, CsvLogger, DiskSpaceLow
from app.storage.database import MIGRATIONS, Database
from tests.conftest import make_m

T0 = datetime(2026, 11, 26, 10, tzinfo=UTC)


def test_migration_sets_version_and_is_idempotent(tmp_path):
    db = Database(tmp_path / "a.db")
    assert db.schema_version() == len(MIGRATIONS)
    db.close()
    db2 = Database(tmp_path / "a.db")
    assert db2.schema_version() == len(MIGRATIONS)
    assert db2.ping()


def test_insert_query_roundtrip_and_duplicates(tmp_path):
    db = Database(tmp_path / "a.db")
    m = make_m(T0, 1, fan_state=True, sensor_states={"t_case": "OK"}, quality_flags=["x"])
    rid = db.insert_measurement(m)
    assert rid is not None
    assert db.insert_measurement(m) is None  # same boot_id+sequence
    got = db.latest_measurement()
    assert got == m
    assert db.count_measurements() == 1


def test_query_range_and_source_filter(tmp_path):
    db = Database(tmp_path / "a.db")
    for i in range(10):
        db.insert_measurement(make_m(T0 + timedelta(minutes=i), i))
    db.insert_measurement(make_m(T0, 99, data_source="hardware", boot_id="hw"))
    sel = db.query_measurements(T0 + timedelta(minutes=2), T0 + timedelta(minutes=5), "simulation")
    assert [m.sequence for m in sel] == [2, 3, 4]
    assert db.count_measurements(source="hardware") == 1
    page = db.query_measurements(source="simulation", limit=3, offset=0, descending=True)
    assert [m.sequence for m in page] == [9, 8, 7]


def test_config_roundtrip_and_defaults(tmp_path):
    db = Database(tmp_path / "a.db")
    assert db.load_config() == SystemConfig()
    cfg = SystemConfig(panel_power_w=550, panel_count=12)
    db.save_config(cfg)
    assert db.load_config() == cfg


def test_events_and_sensor_errors(tmp_path):
    db = Database(tmp_path / "a.db")
    rid = db.insert_measurement(make_m(T0, 1))
    db.add_event("warning", "k", "msg")
    db.add_sensor_error(T0, "t_panel", "disconnected", rid, 1)
    assert db.recent_events()[0]["kind"] == "k"
    assert db.recent_sensor_errors()[0]["sensor"] == "t_panel"


def test_csv_daily_file_with_header(tmp_path):
    log = CsvLogger(tmp_path / "csv", fsync_every=2, min_free_mb=0)
    p1 = log.append(make_m(T0, 1))
    log.append(make_m(T0 + timedelta(seconds=5), 2, panel_t=None))
    p2 = log.append(make_m(T0 + timedelta(days=1), 3))
    assert p1 != p2 and p1.name == "measurements_2026-11-26.csv"
    rows = list(csv.DictReader(p1.open()))
    assert list(rows[0].keys()) == CSV_FIELDS
    assert len(rows) == 2 and rows[1]["panel_temperature_c"] == ""  # missing stays empty, not 0


def test_csv_refuses_when_disk_low(tmp_path):
    log = CsvLogger(tmp_path / "csv", min_free_mb=10**9)
    try:
        log.append(make_m(T0, 1))
        raise AssertionError("expected DiskSpaceLow")
    except DiskSpaceLow:
        pass
