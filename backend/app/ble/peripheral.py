"""BLE GATT server (peripheral) — runs on the UNO Q Linux side.

It advertises as "SolarFieldStation", sends the newest stored measurement
as a 20-byte notification (see protocol.py) and answers history requests
with every stored record the PC does not have yet (see history.py). Measuring and storing do NOT
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

from app.ble.history import HISTORY_CTRL_UUID, HISTORY_DATA_UUID, HistoryError, HistoryServer
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
    """Self-contained simulator -> ingest -> temp SQLite. Always data_source=simulation.

    Pre-fills `history_days` of 5-minute history (night shutdown respected) so
    the PC has something to sync."""

    def __init__(self, settings: Settings, history_days: float = 2.0):
        from datetime import UTC, datetime, timedelta

        from app.services.ingest import IngestService
        from app.services.power_schedule import is_scheduled_on
        from app.simulation.simulator import Simulator

        tmp = Path(tempfile.mkdtemp(prefix="sfs_ble_demo_"))
        super().__init__(Database(tmp / "demo.db"), "simulation")
        self.settings = settings
        self.sim = Simulator(latitude_deg=settings.latitude_deg, longitude_deg=settings.longitude_deg,
                             series_cells=settings.battery_series_cells, seed=settings.simulator_seed)
        self.ingest = IngestService(self.db, None, "simulation")
        self._is_on = lambda t: is_scheduled_on(t, self.db.load_config(), settings.latitude_deg,
                                                settings.longitude_deg, settings.timezone)
        now = datetime.now(UTC)
        t = now - timedelta(days=history_days)
        while t < now:
            self._step(t)
            t += timedelta(minutes=5)
        log.info("demo DB: %d records", self.db.count_measurements())

    def _step(self, when) -> None:
        if not self._is_on(when):
            self.sim.power_off(when)
            return
        self.sim.power_on(when)
        msg = self.sim.sample(when)
        if msg is not None:
            self.ingest.ingest(msg, received_at=when)

    def latest(self):
        from datetime import UTC, datetime

        last = super().latest()
        now = datetime.now(UTC)
        if last is None or (now - last.timestamp_utc).total_seconds() >= 300:
            self._step(now)  # demo keeps the real 5-minute cadence
        return super().latest()


async def serve(source: LatestSource, interval_s: float) -> None:
    from bless import BlessServer, GATTAttributePermissions, GATTCharacteristicProperties

    server = BlessServer(name=DEVICE_NAME)
    last_packet = bytearray(LIVE_PACKET_SIZE)

    def on_read(characteristic, **_kwargs):  # a plain read returns the newest packet too
        return last_packet

    loop = asyncio.get_running_loop()
    history_lock = asyncio.Lock()
    history = HistoryServer(source.db, source.data_mode)  # keeps last batch for RESEND

    async def send_history(raw: bytes) -> None:
        async with history_lock:  # one request at a time
            try:
                chunks = await asyncio.to_thread(history.handle, raw)
            except HistoryError as e:
                log.warning("bad history request: %s", e)
                return
            for chunk in chunks:
                server.get_characteristic(HISTORY_DATA_UUID).value = bytearray(chunk)
                server.update_value(SERVICE_UUID, HISTORY_DATA_UUID)
                await asyncio.sleep(0.015)  # don't overrun the BlueZ notification queue
            log.info("history: sent %d chunk(s)", len(chunks))

    def on_write(characteristic, value, **_kwargs):
        if str(characteristic.uuid).lower() == HISTORY_CTRL_UUID:
            asyncio.run_coroutine_threadsafe(send_history(bytes(value)), loop)

    server.read_request_func = on_read
    server.write_request_func = on_write
    await server.add_new_service(SERVICE_UUID)
    await server.add_new_characteristic(
        SERVICE_UUID,
        LIVE_CHAR_UUID,
        GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
        bytearray(last_packet),
        GATTAttributePermissions.readable,
    )
    await server.add_new_characteristic(
        SERVICE_UUID, HISTORY_CTRL_UUID,
        GATTCharacteristicProperties.write,
        None, GATTAttributePermissions.writeable,
    )
    await server.add_new_characteristic(
        SERVICE_UUID, HISTORY_DATA_UUID,
        GATTCharacteristicProperties.notify,
        None, GATTAttributePermissions.readable,
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
    p.add_argument("--interval", type=float, default=30.0, help="seconds between DB checks")
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
