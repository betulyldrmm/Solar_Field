from datetime import UTC, datetime

import pytest

from app.ble.protocol import LIVE_PACKET_SIZE, ProtocolError, decode_live, encode_live
from tests.conftest import make_m

T0 = datetime(2026, 11, 28, 11, 0, 0, tzinfo=UTC)


def test_packet_fits_default_ble_mtu():
    assert LIVE_PACKET_SIZE <= 20
    assert len(encode_live(make_m(T0, 1))) == LIVE_PACKET_SIZE


def test_roundtrip_keeps_values():
    m = make_m(T0, 70000, voltage_v=0.0213, current_a=0.0712, panel_t=35.2,
               battery_voltage_v=3.912, fan_state=True)
    p = decode_live(encode_live(m))
    assert p.sequence == 70000 % 65536
    assert p.timestamp_utc == T0
    assert p.voltage_v == pytest.approx(0.0213, abs=1e-6)
    assert p.current_a == pytest.approx(0.0712, abs=1e-6)
    assert p.panel_temperature_c == pytest.approx(35.2, abs=0.01)
    assert p.battery_voltage_v == pytest.approx(3.912, abs=0.001)
    assert p.fan_state is True
    assert p.data_source == "simulation"
    assert p.power_w == pytest.approx(0.0213 * 0.0712, rel=1e-4)


def test_missing_values_stay_none_not_zero():
    m = make_m(T0, 3, current_a=None, panel_t=None)
    p = decode_live(encode_live(m))
    assert p.current_a is None and p.panel_temperature_c is None
    assert p.battery_voltage_v is None and p.fan_state is None
    assert p.power_w is None
    assert p.voltage_v is not None


def test_out_of_range_is_invalid_not_clipped():
    p = decode_live(encode_live(make_m(T0, 4, panel_t=400.0)))  # > i16 at 0.01 °C
    assert p.panel_temperature_c is None


def test_hardware_source_flag():
    assert decode_live(encode_live(make_m(T0, 5, data_source="hardware"))).data_source == "hardware"


@pytest.mark.parametrize("data", [b"", bytes(19), bytes(21), b"\x02" + bytes(19)])
def test_bad_packets_rejected(data):
    with pytest.raises(ProtocolError):
        decode_live(data)
