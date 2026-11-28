import asyncio
import random
from datetime import UTC, datetime, timedelta

import pytest

from app.ble.client_sync import ChunkTransport, HistorySync, SyncState
from app.ble.history import (
    RECORD_SIZE,
    HistoryError,
    HistoryRequest,
    HistoryServer,
    Reassembler,
    answer_request,
    build_batch,
    decode_record,
    encode_record,
    parse_batch,
    split_chunks,
)
from app.storage.database import Database
from tests.conftest import make_m

T0 = datetime(2026, 11, 28, 9, 0, tzinfo=UTC)


def full_m(t, seq, **kw):
    base = dict(ambient_temperature_c=15.4, case_temperature_c=22.8, battery_voltage_v=11.8,
                battery_current_a=-0.21, fan_state=False)
    base.update(kw)
    return make_m(t, seq, **base)


def device_db(tmp_path, n=130, source="simulation") -> Database:
    db = Database(tmp_path / "device.db")
    for k in range(n):
        db.insert_measurement(full_m(T0 + timedelta(minutes=5 * k), k + 1, data_source=source))
    return db


def test_record_roundtrip_keeps_all_fields():
    m = full_m(T0, 77, boot_id="a1b2c3d4", sensor_status="DEGRADED", fan_state=True)
    raw = encode_record(42, m)
    assert len(raw) == RECORD_SIZE == 40
    rid, back = decode_record(raw)
    assert rid == 42 and back.boot_id == "a1b2c3d4" and back.sequence == 77
    assert back.timestamp_utc == T0
    assert back.current_a == pytest.approx(m.current_a, abs=1e-6)
    assert back.ambient_temperature_c == pytest.approx(15.4)
    assert back.case_temperature_c == pytest.approx(22.8)
    assert back.battery_voltage_v == pytest.approx(11.8) and back.battery_current_a == pytest.approx(-0.21)
    assert back.fan_state is True and back.sensor_status == "DEGRADED"
    assert back.data_source == "simulation"


def test_missing_values_stay_none():
    m = full_m(T0, 1, current_a=None, panel_t=None, battery_current_a=None, fan_state=None)
    _, back = decode_record(encode_record(1, m))
    assert back.current_a is None and back.panel_temperature_c is None
    assert back.battery_current_a is None and back.fan_state is None and back.power_w is None


@pytest.mark.parametrize("chunk_size", [20, 23, 185, 244])
def test_chunks_reassemble_in_any_order(chunk_size):
    batch = build_batch([(k, full_m(T0, k)) for k in range(1, 31)], device_max_id=30)
    chunks = split_chunks(batch, nonce=7, chunk_size=chunk_size)
    assert all(len(c) <= chunk_size for c in chunks)
    random.Random(1).shuffle(chunks)
    r = Reassembler(7)
    for c in chunks:
        r.add(c)
    assert r.complete
    parsed = parse_batch(r.data())
    assert [rid for rid, _ in parsed.records] == list(range(1, 31)) and parsed.device_max_id == 30


def test_other_nonce_ignored_and_missing_reported():
    chunks = split_chunks(build_batch([(1, full_m(T0, 1))], 1), nonce=5, chunk_size=20)
    r = Reassembler(6)
    assert not any(r.add(c) for c in chunks)
    r = Reassembler(5)
    for c in chunks[1:]:
        r.add(c)
    assert not r.complete and r.missing == [0]
    with pytest.raises(HistoryError):
        r.data()


def test_corrupted_batch_rejected():
    batch = bytearray(build_batch([(1, full_m(T0, 1))], 1))
    batch[10] ^= 0xFF
    with pytest.raises(HistoryError, match="CRC"):
        parse_batch(bytes(batch))


def test_request_roundtrip_and_clamping():
    req = HistoryRequest.decode(HistoryRequest(9, 123, 5000, 9999).encode())
    assert (req.nonce, req.since_id) == (9, 123)
    assert req.max_records == 100 and req.chunk_size == 244
    with pytest.raises(HistoryError):
        HistoryRequest.decode(b"\x02" + bytes(10))


def test_device_answers_only_newer_and_only_active_source(tmp_path):
    db = device_db(tmp_path, n=10)
    db.insert_measurement(full_m(T0, 999, boot_id="hw", data_source="hardware"))
    r = Reassembler(3)
    for c in answer_request(db, "simulation", HistoryRequest(3, 4, 100, 244).encode()):
        r.add(c)
    b = parse_batch(r.data())
    assert [rid for rid, _ in b.records] == [5, 6, 7, 8, 9, 10]
    assert all(m.data_source == "simulation" for _, m in b.records)


# ----------------------------------------------------------- end-to-end sync
class FakeTransport(ChunkTransport):
    """In-process 'BLE link' (device HistoryServer + lossy radio)."""

    quiet_s = 0.02

    def __init__(self, db, mode="simulation", drop_rate=0.0, seed=0):
        super().__init__()
        self.server = HistoryServer(db, mode)
        self.drop_rate, self.rng = drop_rate, random.Random(seed)
        self.writes = 0

    async def _write(self, data):
        self.writes += 1
        for c in self.server.handle(data):
            if self.rng.random() >= self.drop_rate:
                asyncio.get_running_loop().call_soon(self.feed, c)


def run(coro):
    return asyncio.run(coro)


def test_full_sync_then_incremental_then_idempotent(tmp_path):
    dev = device_db(tmp_path, n=130)
    pc = Database(tmp_path / "pc.db")
    state = SyncState(tmp_path / "state.json")
    sync = HistorySync(pc, state, "AA:BB", chunk_size=20, batch_records=50)

    res = run(sync.run(FakeTransport(dev)))
    assert res.up_to_date and res.stored == 130 and res.batches == 3
    assert pc.count_measurements() == 130 and state.get("AA:BB") == dev.max_measurement_id()

    for k in range(130, 135):  # station keeps measuring
        dev.insert_measurement(full_m(T0 + timedelta(minutes=5 * k), k + 1))
    res = run(HistorySync(pc, SyncState(tmp_path / "state.json"), "AA:BB").run(FakeTransport(dev)))
    assert res.stored == 5 and pc.count_measurements() == 135

    res = run(sync.run(FakeTransport(dev)))
    assert res.stored == 0 and res.up_to_date


@pytest.mark.parametrize("chunk_size", [20, 185])
def test_sync_survives_lossy_link_by_resending_only_missing_chunks(tmp_path, chunk_size):
    dev = device_db(tmp_path, n=130)
    pc = Database(tmp_path / "pc.db")
    sync = HistorySync(pc, SyncState(tmp_path / "s.json"), "X", chunk_size=chunk_size, batch_records=50)
    link = FakeTransport(dev, drop_rate=0.10, seed=3)  # 10 % of notifications lost
    res = run(sync.run(link))
    assert res.up_to_date and pc.count_measurements() == 130
    assert res.retries == 0  # recovered via RESEND, never had to repeat a whole batch


def test_resend_returns_only_requested_chunks(tmp_path):
    from app.ble.history import encode_resend
    srv = HistoryServer(device_db(tmp_path, n=20), "simulation")
    chunks = srv.handle(HistoryRequest(11, 0, 20, 20).encode())
    assert srv.handle(encode_resend(11, [0, 3])) == [chunks[0], chunks[3]]
    assert srv.handle(encode_resend(12, [0])) == []  # unknown batch -> nothing


def test_device_db_reset_triggers_full_resync(tmp_path):
    pc = Database(tmp_path / "pc.db")
    state = SyncState(tmp_path / "s.json")
    state.set("X", 500)  # PC thinks it already has up to id 500
    dev = device_db(tmp_path, n=8)  # fresh device DB: ids 1..8
    res = run(HistorySync(pc, state, "X").run(FakeTransport(dev)))
    assert res.stored == 8 and state.get("X") == 8


def test_gives_up_on_dead_link(tmp_path):
    dev = device_db(tmp_path, n=5)
    sync = HistorySync(Database(tmp_path / "pc.db"), SyncState(tmp_path / "s.json"), "X", max_retries=2)
    with pytest.raises(HistoryError, match="giving up"):
        run(sync.run(FakeTransport(dev, drop_rate=1.0)))
