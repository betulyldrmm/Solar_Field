"""PC-side BLE client (central) — runs on the Windows computer.

Finds "SolarFieldStation", subscribes to live notifications, prints them and
appends them to a daily CSV under pc_receiver/received/. If the connection
drops (walked away from the field device) it keeps retrying.

Usage (from the repo root, inside a venv):
    pip install -r pc_receiver/requirements.txt
    python pc_receiver/ble_receiver.py --scan      # list nearby BLE devices
    python pc_receiver/ble_receiver.py             # connect and listen
    python pc_receiver/ble_receiver.py --fake      # no Bluetooth: decode locally made packets

Next steps (not here yet): history sync, forwarding to the PC FastAPI for React.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))  # share protocol.py with the device side

from app.ble.protocol import (  # noqa: E402
    DEVICE_NAME,
    LIVE_CHAR_UUID,
    SERVICE_UUID,
    LivePacket,
    ProtocolError,
    decode_live,
    encode_live,
)

log = logging.getLogger("sfs.pc")
OUT_DIR = ROOT / "received"


class Recorder:
    """Prints packets, drops duplicates, notices gaps, appends to CSV."""

    def __init__(self, out_dir: Path = OUT_DIR):
        self.out_dir = out_dir
        self.last_seq: int | None = None

    def handle(self, pkt: LivePacket) -> None:
        if self.last_seq is not None:
            gap = (pkt.sequence - self.last_seq) % 65536
            if gap == 0:
                return  # same packet again (e.g. after reconnect read)
            if gap > 1:
                log.warning("%d ölçüm kaçırıldı (geçmiş senkronizasyonu sonraki adımda)", gap - 1)
        self.last_seq = pkt.sequence

        def fmt(x, unit, nd=3):
            return "—" if x is None else f"{x:.{nd}f} {unit}"

        print(f"[{pkt.timestamp_utc:%H:%M:%S}Z #{pkt.sequence}] "
              f"T_panel={fmt(pkt.panel_temperature_c, '°C', 2)}  V={fmt(pkt.voltage_v, 'V', 4)}  "
              f"I={fmt(pkt.current_a, 'A', 4)}  P={fmt(pkt.power_w, 'W', 4)}  "
              f"Bat={fmt(pkt.battery_voltage_v, 'V')}  [{pkt.data_source}]", flush=True)
        self._append(pkt)

    def _append(self, pkt: LivePacket) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"live_{pkt.timestamp_utc:%Y-%m-%d}_{pkt.data_source}.csv"
        row = pkt.as_row()
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)


def on_bytes(rec: Recorder, data: bytes | bytearray) -> None:
    try:
        rec.handle(decode_live(data))
    except ProtocolError as e:
        log.error("bozuk paket (%d bayt): %s", len(data), e)


async def scan(timeout: float) -> None:
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for dev, adv in sorted(found.values(), key=lambda x: -x[1].rssi):
        mark = "  <-- istasyon" if SERVICE_UUID in adv.service_uuids or dev.name == DEVICE_NAME else ""
        print(f"{dev.address}  RSSI={adv.rssi:4d}  {dev.name or '(isimsiz)'}{mark}")


async def listen(rec: Recorder, address: str | None, scan_timeout: float) -> None:
    from bleak import BleakClient, BleakScanner

    def match(dev, adv) -> bool:
        if address:
            return dev.address.upper() == address.upper()
        return SERVICE_UUID in adv.service_uuids or (dev.name or adv.local_name) == DEVICE_NAME

    while True:
        log.info("istasyon aranıyor...")
        dev = await BleakScanner.find_device_by_filter(match, timeout=scan_timeout)
        if dev is None:
            log.info("bulunamadı, tekrar denenecek")
            await asyncio.sleep(3)
            continue
        disconnected = asyncio.Event()
        try:
            async with BleakClient(dev, disconnected_callback=lambda _c, ev=disconnected: ev.set()) as client:
                log.info("bağlandı: %s (MTU %s)", dev.address, client.mtu_size)
                on_bytes(rec, await client.read_gatt_char(LIVE_CHAR_UUID))  # newest value right away
                await client.start_notify(LIVE_CHAR_UUID, lambda _ch, data: on_bytes(rec, data))
                await disconnected.wait()
                log.warning("bağlantı koptu")
        except Exception as e:  # out of range, adapter off, GATT error...
            log.warning("bağlantı hatası: %s", e)
        await asyncio.sleep(2)


async def fake(rec: Recorder, interval: float) -> None:
    """End-to-end test of encode -> bytes -> decode -> CSV without any Bluetooth."""
    from types import SimpleNamespace

    seq = 0
    while True:
        m = SimpleNamespace(sequence=seq, timestamp_utc=datetime.now(UTC), voltage_v=0.0213,
                            current_a=0.0712 + 0.001 * (seq % 5), panel_temperature_c=30 + seq % 7,
                            battery_voltage_v=3.91, fan_state=False, data_source="simulation")
        on_bytes(rec, encode_live(m))
        seq += 1
        await asyncio.sleep(interval)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Solar Field Station — PC BLE alıcısı")
    p.add_argument("--scan", action="store_true", help="yakındaki BLE cihazlarını listele")
    p.add_argument("--address", help="belirli bir MAC/adres ile bağlan")
    p.add_argument("--fake", action="store_true", help="Bluetooth olmadan sahte paketlerle test")
    p.add_argument("--timeout", type=float, default=10.0)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    rec = Recorder()
    with contextlib.suppress(KeyboardInterrupt):
        if args.scan:
            asyncio.run(scan(args.timeout))
        elif args.fake:
            asyncio.run(fake(rec, 1.0))
        else:
            asyncio.run(listen(rec, args.address, args.timeout))
    return 0


if __name__ == "__main__":
    sys.exit(main())
