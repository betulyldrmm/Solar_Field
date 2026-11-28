"""BLE history sync — pull every stored measurement the PC does not have yet.

Flow (PC = client, UNO Q = server):

    PC  --write HISTORY_CTRL-->  request(nonce, since_id, max_records, chunk_size)
    UNO Q builds a BATCH (records with DB id > since_id), splits it into CHUNKS
    UNO Q --notify HISTORY_DATA--> chunk 0 .. chunk n-1
    PC reassembles, checks CRC32; on success stores the records and asks again
    with since_id = last id received; an empty batch means "up to date".
    Missing chunks: the PC asks for ONLY those again (RESEND, up to 8 indices
    per write); the device keeps the last batch for that. A bad CRC or a dead
    link repeats the whole request a few times, then gives up until next time.

Why chunks: without MTU negotiation a notification carries only 20 bytes; with
it (Windows/BlueZ usually agree on 185–517) much more. The PC tells the device
its usable chunk size, so the same code works in both cases.

All integers little-endian.

request (11 bytes)
    u8  op = 1 (GET_BATCH)   u16 nonce   u32 since_id   u16 max_records   u16 chunk_size
resend (3 + 2n bytes, n <= 8, fits a 20-byte write)
    u8  op = 2 (RESEND)      u16 nonce   u16 index × n

chunk (6-byte header + payload, header+payload <= chunk_size)
    u16 nonce   u16 index   u16 count   payload…

batch (after reassembly)
    u8 version = 1   u16 record_count   u32 device_max_id   records…   u32 crc32(all before)

record (40 bytes)
    u32 id   u32 unix_ts   8s boot_id (ASCII, NUL padded)   u32 sequence
    i32 voltage µV   i32 current µA
    i16 panel_T 0.01°C   i16 ambient_T 0.01°C   i16 case_T 0.01°C
    u16 battery mV   i16 battery mA   u16 flags (see R_* below)

Like the live packet: a value whose flag bit is 0 is None, never 0.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.models.schemas import Measurement

HISTORY_CTRL_UUID = "5f1d0003-3a6b-4c2e-9d6e-5f0a1c7b2e10"   # write
HISTORY_DATA_UUID = "5f1d0004-3a6b-4c2e-9d6e-5f0a1c7b2e10"   # notify

OP_GET_BATCH = 1
OP_RESEND = 2
MAX_RESEND_INDICES = 8
BATCH_VERSION = 1
MIN_CHUNK = 20            # ATT_MTU 23 - 3: always available
MAX_CHUNK = 244           # comfortably below the BLE 5 maximum (512)
MAX_RECORDS_PER_BATCH = 100

_REQ = struct.Struct("<BHIHH")
_CHUNK_HDR = struct.Struct("<HHH")
_BATCH_HDR = struct.Struct("<BHI")
_REC = struct.Struct("<II8sIiihhhHhH")
RECORD_SIZE = _REC.size
assert RECORD_SIZE == 40

R_VOLTAGE, R_CURRENT, R_PANEL_T, R_AMBIENT_T, R_CASE_T, R_BATT_V, R_BATT_I = (1 << i for i in range(7))
R_FAN_VALID, R_FAN_ON, R_SIMULATION, R_SENSORS_OK = (1 << i for i in range(7, 11))


class HistoryError(ValueError):
    pass


# --------------------------------------------------------------------- request
@dataclass(frozen=True)
class HistoryRequest:
    nonce: int
    since_id: int
    max_records: int
    chunk_size: int

    def encode(self) -> bytes:
        return _REQ.pack(OP_GET_BATCH, self.nonce & 0xFFFF, self.since_id, self.max_records, self.chunk_size)

    @classmethod
    def decode(cls, data: bytes | bytearray) -> HistoryRequest:
        if len(data) != _REQ.size:
            raise HistoryError(f"request must be {_REQ.size} bytes, got {len(data)}")
        op, nonce, since, max_rec, chunk = _REQ.unpack(bytes(data))
        if op != OP_GET_BATCH:
            raise HistoryError(f"unknown op {op}")
        return cls(nonce, since, max(1, min(max_rec, MAX_RECORDS_PER_BATCH)),
                   max(MIN_CHUNK, min(chunk, MAX_CHUNK)))


# --------------------------------------------------------------------- records
def _scaled(v: float | None, scale: float, lo: int, hi: int) -> tuple[int, bool]:
    if v is None:
        return 0, False
    raw = round(v * scale)
    return (raw, True) if lo <= raw <= hi else (0, False)


def encode_record(record_id: int, m: Measurement) -> bytes:
    flags = 0
    vals = []
    for value, scale, lo, hi, bit in (
        (m.voltage_v, 1e6, -(2**31), 2**31 - 1, R_VOLTAGE),
        (m.current_a, 1e6, -(2**31), 2**31 - 1, R_CURRENT),
        (m.panel_temperature_c, 100, -(2**15), 2**15 - 1, R_PANEL_T),
        (m.ambient_temperature_c, 100, -(2**15), 2**15 - 1, R_AMBIENT_T),
        (m.case_temperature_c, 100, -(2**15), 2**15 - 1, R_CASE_T),
        (m.battery_voltage_v, 1000, 0, 2**16 - 1, R_BATT_V),
        (m.battery_current_a, 1000, -(2**15), 2**15 - 1, R_BATT_I),
    ):
        raw, ok = _scaled(value, scale, lo, hi)
        vals.append(raw)
        flags |= bit if ok else 0
    if m.fan_state is not None:
        flags |= R_FAN_VALID | (R_FAN_ON if m.fan_state else 0)
    if m.data_source == "simulation":
        flags |= R_SIMULATION
    if m.sensor_status == "OK":
        flags |= R_SENSORS_OK
    boot = m.boot_id.encode("ascii", "replace")[:8]
    return _REC.pack(record_id, int(m.timestamp_utc.timestamp()), boot, m.sequence, *vals, flags)


def decode_record(data: bytes) -> tuple[int, Measurement]:
    rid, ts, boot, seq, v, i, tp, ta, tc, bv, bi, flags = _REC.unpack(data)

    def opt(raw: int, scale: float, bit: int) -> float | None:
        return raw / scale if flags & bit else None

    voltage, current = opt(v, 1e6, R_VOLTAGE), opt(i, 1e6, R_CURRENT)
    m = Measurement(
        timestamp_utc=datetime.fromtimestamp(ts, UTC),
        boot_id=boot.rstrip(b"\0").decode("ascii", "replace") or "unknown",
        sequence=seq,
        voltage_v=voltage,
        current_a=current,
        power_w=None if voltage is None or current is None else voltage * current,
        panel_temperature_c=opt(tp, 100, R_PANEL_T),
        ambient_temperature_c=opt(ta, 100, R_AMBIENT_T),
        case_temperature_c=opt(tc, 100, R_CASE_T),
        battery_voltage_v=opt(bv, 1000, R_BATT_V),
        battery_current_a=opt(bi, 1000, R_BATT_I),
        fan_state=bool(flags & R_FAN_ON) if flags & R_FAN_VALID else None,
        sensor_status="OK" if flags & R_SENSORS_OK else "DEGRADED",
        quality_flags=["via_ble_sync"],
        data_source="simulation" if flags & R_SIMULATION else "hardware",
    )
    return rid, m


# ----------------------------------------------------------------------- batch
def build_batch(records: Iterable[tuple[int, Measurement]], device_max_id: int) -> bytes:
    recs = [encode_record(rid, m) for rid, m in records]
    body = _BATCH_HDR.pack(BATCH_VERSION, len(recs), device_max_id) + b"".join(recs)
    return body + struct.pack("<I", zlib.crc32(body))


@dataclass(frozen=True)
class Batch:
    records: list[tuple[int, Measurement]]
    device_max_id: int


def parse_batch(data: bytes) -> Batch:
    if len(data) < _BATCH_HDR.size + 4:
        raise HistoryError("batch too short")
    body, crc = data[:-4], struct.unpack("<I", data[-4:])[0]
    if zlib.crc32(body) != crc:
        raise HistoryError("CRC mismatch")
    ver, count, max_id = _BATCH_HDR.unpack(body[: _BATCH_HDR.size])
    if ver != BATCH_VERSION:
        raise HistoryError(f"unsupported batch version {ver}")
    recs = body[_BATCH_HDR.size:]
    if len(recs) != count * RECORD_SIZE:
        raise HistoryError("record count does not match length")
    return Batch([decode_record(recs[k * RECORD_SIZE:(k + 1) * RECORD_SIZE]) for k in range(count)], max_id)


# ---------------------------------------------------------------------- chunks
def split_chunks(batch: bytes, nonce: int, chunk_size: int) -> list[bytes]:
    payload = chunk_size - _CHUNK_HDR.size
    parts = [batch[k:k + payload] for k in range(0, len(batch), payload)] or [b""]
    if len(parts) > 0xFFFF:
        raise HistoryError("batch too large")
    return [_CHUNK_HDR.pack(nonce & 0xFFFF, idx, len(parts)) + p for idx, p in enumerate(parts)]


class Reassembler:
    """Collects chunks for one request (nonce). Chunks of other nonces are ignored."""

    def __init__(self, nonce: int):
        self.nonce = nonce & 0xFFFF
        self.count: int | None = None
        self.parts: dict[int, bytes] = {}

    def add(self, chunk: bytes | bytearray) -> bool:
        """Returns True once all chunks are present."""
        if len(chunk) < _CHUNK_HDR.size:
            return False
        nonce, idx, count = _CHUNK_HDR.unpack(bytes(chunk[: _CHUNK_HDR.size]))
        if nonce != self.nonce or count == 0 or idx >= count:
            return False
        if self.count is None:
            self.count = count
        elif count != self.count:
            return False
        self.parts[idx] = bytes(chunk[_CHUNK_HDR.size:])
        return self.complete

    @property
    def complete(self) -> bool:
        return self.count is not None and len(self.parts) == self.count

    @property
    def missing(self) -> list[int]:
        return [] if self.count is None else [k for k in range(self.count) if k not in self.parts]

    def data(self) -> bytes:
        if not self.complete:
            raise HistoryError(f"missing chunks {self.missing}")
        return b"".join(self.parts[k] for k in range(self.count or 0))


def encode_resend(nonce: int, indices: list[int]) -> bytes:
    if not 1 <= len(indices) <= MAX_RESEND_INDICES:
        raise HistoryError("resend needs 1..8 indices")
    return struct.pack(f"<BH{len(indices)}H", OP_RESEND, nonce & 0xFFFF, *indices)


# --------------------------------------------------------------- device side
class HistoryServer:
    """Device-side handler (pure, no BLE): written bytes -> notification payloads.
    Keeps the last batch so single lost chunks can be resent."""

    def __init__(self, db, data_mode: str):
        self.db, self.data_mode = db, data_mode
        self._last_nonce: int | None = None
        self._last_chunks: list[bytes] = []

    def handle(self, raw: bytes | bytearray) -> list[bytes]:
        raw = bytes(raw)
        if raw[:1] == bytes([OP_RESEND]):
            if len(raw) < 5 or (len(raw) - 3) % 2:
                raise HistoryError("bad resend request")
            nonce, *idx = struct.unpack(f"<H{(len(raw) - 3) // 2}H", raw[1:])
            if nonce != self._last_nonce:
                return []  # batch no longer cached; client will re-request it
            return [self._last_chunks[i] for i in idx if i < len(self._last_chunks)]
        req = HistoryRequest.decode(raw)
        records = self.db.measurements_after_id(req.since_id, req.max_records, source=self.data_mode)
        batch = build_batch(records, self.db.max_measurement_id(source=self.data_mode))
        self._last_nonce, self._last_chunks = req.nonce, split_chunks(batch, req.nonce, req.chunk_size)
        return self._last_chunks


def answer_request(db, data_mode: str, raw_request: bytes | bytearray) -> list[bytes]:
    """Stateless convenience wrapper (single request, no resend)."""
    return HistoryServer(db, data_mode).handle(raw_request)
