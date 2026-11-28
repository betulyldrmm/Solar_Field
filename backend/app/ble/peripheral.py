"""BLE GATT server (peripheral) — runs on the UNO Q Linux side.

It advertises as "SolarFieldStation" and sends the newest stored measurement
as a 20-byte notification (see protocol.py). Measuring and storing do NOT
depend on this process: if BLE crashes or nobody is connected, the backend
keeps logging to SQLite/CSV.

Run (from backend/):
    python -m app.ble.peripheral            # reads the backend's SQLite DB
    python -m app.ble.peripheral --demo     # own simulator, no backend needed

Library: `bless` (BlueZ D-Bus on Linux; WinRT on Windows, so a second laptop
can play the UNO Q's role before the hardware is ready).

DOĞRULAMA GEREKİYOR (UNO Q donanımında henüz denenmedi):
* bluetoothd çalışıyor mu, adaptör "Powered: yes" mi  -> `bluetoothctl show`
* BlueZ sürümü GATT server + LE advertising destekliyor mu (>= 5.50 önerilir)
* Wi-Fi/BT combo modülünde BLE peripheral rolü çalışıyor mu
Bkz. docs/ble.md "Donanım doğrulama listesi".
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import sys
import tempfile
from pathlib import Path

from app.ble.protocol import DEVICE_NAME, LIVE_CHAR_UUID, LIVE_PACKET_SIZE, SERVICE_UUID, encode_live
from app.config import Settings
from app.storage.database import Database

log = logging.getLogger("sfs.ble")


class LatestSource:
    """Reads the newest measurement of the active data mode from SQLite."""

    def __init__(self, db: Database, data_mode: str):
        self.db = db
        self.data_mode = data_mode

    def latest(self):
        return self.db.latest_measurement(source=self.data_mode)


class DemoSource(LatestSource):
    """Self-contained simulator -> ingest -> temp SQLite. Always data_source=simulation."""

    def __init__(self, settings: Settings):
        from app.services.ingest import IngestService
        from app.simulation.simulator import Simulator

        tmp = Path(tempfile.mkdtemp(prefix="sfs_ble_demo_"))
        super().__init__(Database(tmp / "demo.db"), "simulation")
        self.sim = Simulator(latitude_deg=settings.latitude_deg, longitude_deg=settings.longitude_deg,
                             series_cells=settings.battery_series_cells, seed=settings.simulator_seed)
        self.ingest = IngestService(self.db, None, "simulation")

    def latest(self):
        from datetime import UTC, datetime

        msg = self.sim.sample(datetime.now(UTC))
        if msg is not None:
            self.ingest.ingest(msg)
        return super().latest()


async def serve(source: LatestSource, interval_s: float) -> None:
    from bless import BlessServer, GATTAttributePermissions, GATTCharacteristicProperties

    server = BlessServer(name=DEVICE_NAME)
    last_packet = bytearray(LIVE_PACKET_SIZE)

    def on_read(characteristic, **_kwargs):  # a plain read returns the newest packet too
        return last_packet

    server.read_request_func = on_read
    await server.add_new_service(SERVICE_UUID)
    await server.add_new_characteristic(
        SERVICE_UUID,
        LIVE_CHAR_UUID,
        GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
        bytearray(last_packet),
        GATTAttributePermissions.readable,
    )
    await server.start()
    log.info("advertising as %s (service %s)", DEVICE_NAME, SERVICE_UUID)

    last_key = None
    try:
        while True:
            try:
                m = await asyncio.to_thread(source.latest)
            except Exception:
                log.exception("reading latest measurement failed")
                m = None
            if m is not None and (m.boot_id, m.sequence) != last_key:
                last_key = (m.boot_id, m.sequence)
                last_packet[:] = encode_live(m)
                server.get_characteristic(LIVE_CHAR_UUID).value = bytearray(last_packet)
                server.update_value(SERVICE_UUID, LIVE_CHAR_UUID)  # notify subscribers
                log.info("sent seq=%s t=%s T=%s", m.sequence, m.timestamp_utc, m.panel_temperature_c)
            elif m is None:
                log.debug("no measurement yet")
            await asyncio.sleep(interval_s)
    finally:
        await server.stop()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Solar Field Station BLE peripheral")
    p.add_argument("--demo", action="store_true", help="use a built-in simulator instead of the DB")
    p.add_argument("--interval", type=float, default=5.0, help="seconds between checks")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        import bless  # noqa: F401
    except ImportError:
        log.error("'bless' yüklü değil: pip install -r requirements-ble.txt")
        return 2
    settings = Settings.from_env()
    source: LatestSource
    if args.demo:
        source = DemoSource(settings)
    else:
        if not settings.db_path.exists():
            log.error("DB yok: %s — önce backend'i başlat ya da --demo kullan", settings.db_path)
            return 2
        source = LatestSource(Database(settings.db_path), settings.data_mode)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(serve(source, args.interval))
    return 0


if __name__ == "__main__":
    sys.exit(main())
