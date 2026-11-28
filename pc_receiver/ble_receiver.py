"""PC-side BLE client (central) — runs on the Windows computer.

On connect it
  1. pulls every stored record it does not have yet (history sync, see
     backend/app/ble/history.py) into a local SQLite database,
  2. then listens to live notifications; each new live packet triggers a small
     incremental sync, so the local database follows the station.
If the connection drops (walked away from the station) it keeps retrying.

The local database has the backend's schema, so the normal FastAPI backend +
React UI on the PC can show it:
    cd backend
    $env:SFS_DATA_DIR="data_ble"; $env:SFS_RUN_SIMULATOR="false"
    python -m uvicorn app.main:app --port 8000
(set $env:SFS_DATA_MODE="hardware" when the station runs on real sensors)

Usage (from the repo root, inside the backend venv):
    pip install -r pc_receiver/requirements.txt
    python pc_receiver/ble_receiver.py --scan      # list nearby BLE devices
    python pc_receiver/ble_receiver.py             # connect, sync, listen
    python pc_receiver/ble_receiver.py --fake      # no Bluetooth: in-process demo station
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import logging
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "backend"))  # share protocol code with the device side

from app.ble.client_sync import ChunkTransport, HistorySync, SyncResult, SyncState  # noqa: E402
from app.ble.history import (  # noqa: E402
    HISTORY_CTRL_UUID,
    HISTORY_DATA_UUID,
    MAX_CHUNK,
    MIN_CHUNK,
    HistoryError,
    HistoryServer,
)
from app.ble.protocol import (  # noqa: E402
    DEVICE_NAME,
    LIVE_CHAR_UUID,
    SERVICE_UUID,
    LivePacket,
    ProtocolError,
    decode_live,
    encode_live,
)
from app.storage.database import Database  # noqa: E402

log = logging.getLogger("sfs.pc")
CSV_DIR = ROOT / "received"
DEFAULT_DATA_DIR = ROOT.parent / "backend" / "data_ble"


# ------------------------------------------------------------------ live view
class LiveView:
    """Prints live packets, drops duplicates, appends them to a daily CSV."""

    def __init__(self, out_dir: Path = CSV_DIR):
        self.out_dir = out_dir
        self.last_seq: int | None = None

    def handle(self, pkt: LivePacket) -> bool:
        """Returns True for a new packet."""
        if self.last_seq is not None and (pkt.sequence - self.last_seq) % 65536 == 0:
            return False
        self.last_seq = pkt.sequence

        def fmt(x, unit, nd=3):
            return "—" if x is None else f"{x:.{nd}f} {unit}"

        print(f"[canlı {pkt.timestamp_utc:%H:%M:%S}Z #{pkt.sequence}] "
              f"T_panel={fmt(pkt.panel_temperature_c, '°C', 2)}  I={fmt(pkt.current_a, 'A', 4)}  "
              f"Bat={fmt(pkt.battery_voltage_v, 'V')}  [{pkt.data_source}]", flush=True)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.out_dir / f"live_{pkt.timestamp_utc:%Y-%m-%d}_{pkt.data_source}.csv"
        row = pkt.as_row()
        new = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(row))
            if new:
                w.writeheader()
            w.writerow(row)
        return True


def decode_or_log(data: bytes | bytearray) -> LivePacket | None:
    try:
        return decode_live(data)
    except ProtocolError as e:
        log.error("bozuk canlı paket (%d bayt): %s", len(data), e)
        return None


def report(res: SyncResult) -> None:
    if res.stored or res.batches:
        print(f"✔ Geçmiş: {res.received} kayıt alındı, {res.stored} yeni kayıt veritabanına eklendi"
              f" ({res.batches} paket grubu, {res.retries} tekrar).", flush=True)
    else:
        print("✔ Geçmiş güncel.", flush=True)


class Syncer:
    """Runs one HistorySync at a time; extra triggers while running are merged."""

    def __init__(self, sync: HistorySync, transport):
        self.sync, self.transport = sync, transport
        self._lock = asyncio.Lock()

    async def run(self) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            try:
                report(await self.sync.run(self.transport))
            except HistoryError as e:
                log.error("geçmiş senkronizasyonu başarısız: %s", e)

    def trigger(self) -> None:
        asyncio.get_running_loop().create_task(self.run())


# ----------------------------------------------------------------- real BLE
class BleakHistoryTransport(ChunkTransport):
    def __init__(self, client):
        super().__init__()
        self.client = client

    def on_chunk(self, _char, data: bytearray) -> None:
        self.feed(data)

    async def _write(self, data: bytes) -> None:
        await self.client.write_gatt_char(HISTORY_CTRL_UUID, data, response=True)


async def scan(timeout: float) -> None:
    from bleak import BleakScanner

    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    for dev, adv in sorted(found.values(), key=lambda x: -x[1].rssi):
        mark = "  <-- istasyon" if SERVICE_UUID in adv.service_uuids or dev.name == DEVICE_NAME else ""
        print(f"{dev.address}  RSSI={adv.rssi:4d}  {dev.name or '(isimsiz)'}{mark}")


async def listen(db: Database, state: SyncState, address: str | None, scan_timeout: float) -> None:
    from bleak import BleakClient, BleakScanner

    live = LiveView()

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
                chunk = max(MIN_CHUNK, min(MAX_CHUNK, (client.mtu_size or 23) - 3))
                log.info("bağlandı: %s (MTU %s → parça %d bayt)", dev.address, client.mtu_size, chunk)
                transport = BleakHistoryTransport(client)
                await client.start_notify(HISTORY_DATA_UUID, transport.on_chunk)
                syncer = Syncer(HistorySync(db, state, dev.address, chunk_size=chunk), transport)

                pkt = decode_or_log(await client.read_gatt_char(LIVE_CHAR_UUID))
                if pkt:
                    live.handle(pkt)
                await syncer.run()  # 1) everything stored while we were away

                def on_live(_ch, data, s=syncer):  # 2) then follow the station
                    p = decode_or_log(data)
                    if p and live.handle(p):
                        s.trigger()

                await client.start_notify(LIVE_CHAR_UUID, on_live)
                await disconnected.wait()
                log.warning("bağlantı koptu")
        except Exception as e:  # noqa: BLE001 — out of range, adapter off, GATT error...
            log.warning("bağlantı hatası: %s", e)
        await asyncio.sleep(2)


# ---------------------------------------------------------------- fake link
class FakeLink(ChunkTransport):
    """In-process station + lossy 'radio' (5 % of chunks lost), no Bluetooth needed."""

    quiet_s = 0.1

    def __init__(self, source, drop_rate: float = 0.05):
        super().__init__()
        self.server = HistoryServer(source.db, source.data_mode)
        self.drop_rate, self.rng = drop_rate, random.Random()

    async def _write(self, data: bytes) -> None:
        loop = asyncio.get_running_loop()
        for k, c in enumerate(self.server.handle(data)):
            if self.rng.random() >= self.drop_rate:
                loop.call_later(0.002 * k, self.feed, c)


async def fake(db: Database, state: SyncState) -> None:
    from app.ble.peripheral import DemoSource
    from app.config import Settings

    print("Sahte istasyon hazırlanıyor (2 günlük simülasyon geçmişi)...", flush=True)
    station = DemoSource(Settings.from_env(), history_days=2)
    syncer = Syncer(HistorySync(db, state, "FAKE-STATION", chunk_size=20), FakeLink(station))
    await syncer.run()
    live = LiveView()
    print("Canlı veri bekleniyor (sahte istasyon da 5 dakikada bir ölçer, Ctrl+C ile çık)...", flush=True)
    while True:
        m = await asyncio.to_thread(station.latest)
        if m is not None:
            pkt = decode_or_log(encode_live(m))
            if pkt and live.handle(pkt):
                await syncer.run()
        await asyncio.sleep(10)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Solar Field Station — PC BLE alıcısı")
    p.add_argument("--scan", action="store_true", help="yakındaki BLE cihazlarını listele")
    p.add_argument("--address", help="belirli bir MAC/adres ile bağlan")
    p.add_argument("--fake", action="store_true", help="Bluetooth olmadan sahte istasyonla test")
    default_dir = Path(os.environ.get("SFS_BLE_DATA_DIR", DEFAULT_DATA_DIR))
    p.add_argument("--data-dir", type=Path, default=default_dir,
                   help=f"alınan verinin SQLite klasörü (varsayılan: {DEFAULT_DATA_DIR})")
    p.add_argument("--timeout", type=float, default=10.0)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    if args.scan:
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(scan(args.timeout))
        return 0
    db = Database(args.data_dir / "solar_field_station.db")
    state = SyncState(args.data_dir / "ble_sync_state.json")
    print(f"Veritabanı: {db.path}  (şu an {db.count_measurements()} kayıt)", flush=True)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(fake(db, state) if args.fake else listen(db, state, args.address, args.timeout))
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
