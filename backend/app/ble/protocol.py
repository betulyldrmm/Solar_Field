"""BLE protocol between the UNO Q (GATT server) and the PC (Bleak client).

Phase-3a scope: ONE characteristic with live measurements, sent as BLE
notifications. History sync is designed in docs/ble.md but not implemented yet.

Why binary instead of JSON for live data
----------------------------------------
Without MTU negotiation a BLE notification carries at most 20 bytes
(ATT_MTU 23 - 3). Windows/BlueZ usually negotiate more, but we must not rely on
it. A JSON measurement is ~150 bytes and would need chunking, ordering and
re-assembly. The fixed 20-byte packet below always fits in one notification,
so no chunking is needed for live data.

Live packet v1 (little-endian, exactly 20 bytes)
------------------------------------------------
offset size type  field                 unit / note
0      1    u8    version               = 1
1      1    u8    flags                 see FLAG_* below
2      2    u16   sequence              Measurement.sequence mod 65536
4      4    u32   timestamp             Unix seconds, UTC
8      4    i32   voltage               microvolts  (reference panel V)
12     4    i32   current               microamps   (reference panel I)
16     2    i16   panel_temperature     0.01 °C
18     2    u16   battery_voltage       millivolts

A value whose VALID flag is 0 is "not available" and is decoded as None —
never as 0 (project rule: invalid measurements are not real data).
power_w is not sent; the receiver computes V*I and labels it "calculated".
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

# Custom 128-bit UUIDs (random, project-specific). Keep in sync with docs/ble.md.
SERVICE_UUID = "5f1d0001-3a6b-4c2e-9d6e-5f0a1c7b2e10"
LIVE_CHAR_UUID = "5f1d0002-3a6b-4c2e-9d6e-5f0a1c7b2e10"
TELEMETRY_JSON_CHAR_UUID = "5f1d0003-3a6b-4c2e-9d6e-5f0a1c7b2e10"
DEVICE_NAME = "SolarFieldStation"

PROTOCOL_VERSION = 1
_FMT = "<BBHIiihH"
LIVE_PACKET_SIZE = struct.calcsize(_FMT)  # 20
assert LIVE_PACKET_SIZE == 20

FLAG_VOLTAGE = 1 << 0
FLAG_CURRENT = 1 << 1
FLAG_PANEL_TEMP = 1 << 2
FLAG_BATTERY = 1 << 3
FLAG_FAN_VALID = 1 << 4
FLAG_FAN_ON = 1 << 5
FLAG_SIMULATION = 1 << 6  # data_source == "simulation"

_I32 = (-(2**31), 2**31 - 1)
_I16 = (-(2**15), 2**15 - 1)
_U16 = (0, 2**16 - 1)


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class LivePacket:
    sequence: int
    timestamp_utc: datetime
    voltage_v: float | None
    current_a: float | None
    panel_temperature_c: float | None
    battery_voltage_v: float | None
    fan_state: bool | None
    data_source: str  # "simulation" | "hardware"

    @property
    def power_w(self) -> float | None:
        """Calculated (not measured) V*I."""
        if self.voltage_v is None or self.current_a is None:
            return None
        return self.voltage_v * self.current_a

    def as_row(self) -> dict[str, Any]:
        return {
            "timestamp_utc": self.timestamp_utc.isoformat(),
            "sequence": self.sequence,
            "voltage_v": self.voltage_v,
            "current_a": self.current_a,
            "power_w_calculated": self.power_w,
            "panel_temperature_c": self.panel_temperature_c,
            "battery_voltage_v": self.battery_voltage_v,
            "fan_state": self.fan_state,
            "data_source": self.data_source,
        }


def _scaled(value: float | None, scale: float, bounds: tuple[int, int]) -> tuple[int, bool]:
    """Scale to an int. Out-of-range values are sent as invalid, not clipped."""
    if value is None:
        return 0, False
    raw = round(value * scale)
    if not bounds[0] <= raw <= bounds[1]:
        return 0, False
    return raw, True


def encode_live(m: Any) -> bytes:
    """Encode anything with Measurement-like attributes (app.models.schemas.Measurement)."""
    flags = 0
    v, ok = _scaled(m.voltage_v, 1e6, _I32)
    flags |= FLAG_VOLTAGE if ok else 0
    i, ok = _scaled(m.current_a, 1e6, _I32)
    flags |= FLAG_CURRENT if ok else 0
    t, ok = _scaled(m.panel_temperature_c, 100, _I16)
    flags |= FLAG_PANEL_TEMP if ok else 0
    b, ok = _scaled(getattr(m, "battery_voltage_v", None), 1000, _U16)
    flags |= FLAG_BATTERY if ok else 0
    fan = getattr(m, "fan_state", None)
    if fan is not None:
        flags |= FLAG_FAN_VALID | (FLAG_FAN_ON if fan else 0)
    if m.data_source == "simulation":
        flags |= FLAG_SIMULATION
    ts = m.timestamp_utc
    if ts.tzinfo is None:
        raise ProtocolError("timestamp_utc must be timezone-aware")
    return struct.pack(
        _FMT, PROTOCOL_VERSION, flags, m.sequence % 65536, int(ts.timestamp()), v, i, t, b
    )


def decode_live(data: bytes | bytearray) -> LivePacket:
    if len(data) != LIVE_PACKET_SIZE:
        raise ProtocolError(f"expected {LIVE_PACKET_SIZE} bytes, got {len(data)}")
    ver, flags, seq, ts, v, i, t, b = struct.unpack(_FMT, bytes(data))
    if ver != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported protocol version {ver}")
    return LivePacket(
        sequence=seq,
        timestamp_utc=datetime.fromtimestamp(ts, UTC),
        voltage_v=v / 1e6 if flags & FLAG_VOLTAGE else None,
        current_a=i / 1e6 if flags & FLAG_CURRENT else None,
        panel_temperature_c=t / 100 if flags & FLAG_PANEL_TEMP else None,
        battery_voltage_v=b / 1000 if flags & FLAG_BATTERY else None,
        fan_state=bool(flags & FLAG_FAN_ON) if flags & FLAG_FAN_VALID else None,
        data_source="simulation" if flags & FLAG_SIMULATION else "hardware",
    )


def encode_telemetry_json(m: Any, max_bytes: int = 512) -> bytes:
    """Compact UTF-8 JSON snapshot including extended heat & multi-sensor channels."""
    payload: dict[str, Any] = {
        "seq": m.sequence,
        "ts": int(m.timestamp_utc.timestamp()),
        "src": m.data_source,
        "st": getattr(m, "sensor_status", "OK"),
    }
    optional_fields = (
        ("v", "voltage_v"),
        ("i", "current_a"),
        ("p", "power_w"),
        ("tp", "panel_temperature_c"),
        ("ta", "ambient_temperature_c"),
        ("tc", "case_temperature_c"),
        ("th", "heatsink_temperature_c"),
        ("tb", "battery_temperature_c"),
        ("tmcu", "mcu_temperature_c"),
        ("tmpu", "mpu_temperature_c"),
        ("tsurf", "surface_temperature_c"),
        ("hum", "humidity_pct"),
        ("pres", "pressure_hpa"),
        ("wind", "wind_speed_ms"),
        ("irr", "irradiance_measured_wm2"),
        ("bv", "battery_voltage_v"),
        ("bi", "battery_current_a"),
        ("fan", "fan_state"),
    )
    for short, attr in optional_fields:
        val = getattr(m, attr, None)
        if val is not None:
            payload[short] = round(val, 4) if isinstance(val, float) else val
    extra = getattr(m, "extra_sensors", None) or {}
    if extra:
        payload["ex"] = {k: round(v, 3) for k, v in extra.items()}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(raw) <= max_bytes:
        return raw
    payload.pop("ex", None)
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")[:max_bytes]
