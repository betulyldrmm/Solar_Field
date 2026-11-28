"""Measurement sources: simulator (PC/demo) and Arduino UNO Q Router Bridge (hardware).

On the Arduino UNO Q, `arduino-router.service` exposes a MessagePack-RPC Unix
socket at `/var/run/arduino-router.sock` bridging the Linux MPU (QRB2210) and
the real-time STM32U585 MCU (`Arduino_RouterBridge` C++ library):

    STM32 -> Linux:  Bridge.notify("measurement", json_string)
    Linux -> Router: call("$/register", "measurement")
    Linux -> STM32:  call("set_config", config_for_mcu(cfg))
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models.schemas import SystemConfig
from app.services.ingest import IngestService
from app.simulation.simulator import Simulator

log = logging.getLogger(__name__)

ROUTE_ALREADY_EXISTS_ERR = 0x05
FUNCTION_NOT_FOUND_ERR = 0xFE
GENERIC_ERR = 0xFF


def read_linux_soc_temperatures() -> tuple[float | None, dict[str, float]]:
    """Read Qualcomm QRB2210 thermal zones from sysfs when running on Linux/UNO Q."""
    thermal_root = Path("/sys/class/thermal")
    if not thermal_root.is_dir():
        return None, {}
    zones: dict[str, float] = {}
    cpu_temps: list[float] = []
    for zone in sorted(thermal_root.glob("thermal_zone*")):
        try:
            ztype = (zone / "type").read_text(encoding="utf-8").strip()
            raw_mc = int((zone / "temp").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        temp_c = round(raw_mc / 1000.0, 2)
        if not (-40.0 <= temp_c <= 125.0):
            continue
        clean = ztype.lower().replace("-", "_")
        zones[f"soc_{clean}_c"] = temp_c
        if clean.startswith(("cpu", "hm_center", "soc")):
            cpu_temps.append(temp_c)
    if not zones:
        return None, {}
    primary = round(max(cpu_temps or zones.values()), 2)
    return primary, zones


class HardwareBridgeAdapter:
    """Glue between the UNO Q Bridge callback and the ingest pipeline."""

    def __init__(self, ingest: IngestService, *, enrich_host_thermal: bool = False, live_hub: Any = None):
        self.ingest = ingest
        self.enrich_host_thermal = enrich_host_thermal
        self.live_hub = live_hub
        self.last_message_utc: datetime | None = None
        self.router_connected: bool = False

    def on_live(self, payload: str | bytes | dict[str, Any]) -> None:
        """Fast 2 s non-persisted live telemetry callback from the STM32U585."""
        if self.live_hub is not None:
            self.live_hub.on_mcu_payload(payload)

    def on_measurement(self, payload: str | bytes | dict[str, Any]) -> bool:
        """Called from the Bridge with the STM32's JSON string or dict. Returns True if stored."""
        now = datetime.now(UTC)
        self.last_message_utc = now
        if self.live_hub is not None:
            self.live_hub.on_mcu_payload(payload)
        raw: str | bytes | dict[str, Any] = payload
        if self.enrich_host_thermal:
            try:
                msg = json.loads(payload) if isinstance(payload, (str, bytes)) else dict(payload)
                if isinstance(msg, dict):
                    mpu_t, soc_zones = read_linux_soc_temperatures()
                    if mpu_t is not None and msg.get("mpu_temperature_c") is None:
                        msg["mpu_temperature_c"] = mpu_t
                        sensors = msg.setdefault("sensors", {})
                        if isinstance(sensors, dict):
                            sensors.setdefault("t_mpu", "OK")
                    if soc_zones:
                        extra = msg.setdefault("extra_sensors", {})
                        if isinstance(extra, dict):
                            for k in ("soc_cpuss0_thermal_c", "soc_wlan_thermal_c"):
                                if k in soc_zones and k not in extra:
                                    extra[k] = soc_zones[k]
                    raw = msg
            except (json.JSONDecodeError, TypeError, ValueError):
                raw = payload
        outcome = self.ingest.ingest(raw, received_at=now)
        return outcome.stored

    @staticmethod
    def config_for_mcu(cfg: SystemConfig) -> str:
        """Compact JSON for the STM32 (LCD values + local safety thresholds)."""
        return json.dumps(
            {
                "schema_version": 1,
                "panel_power_w": cfg.panel_power_w,
                "panel_count": cfg.panel_count,
                "installed_kwp": round(cfg.kwp, 3),
                "measurement_interval_s": cfg.measurement_interval_s,
                "fan_on_c": cfg.fan_on_c,
                "fan_off_c": cfg.fan_off_c,
                "night_shutdown_enabled": cfg.night_shutdown_enabled,
            },
            separators=(",", ":"),
        )


class RouterBridgeRunner:
    """MessagePack-RPC client/server connected to UNO Q's `/var/run/arduino-router.sock`."""

    def __init__(
        self,
        adapter: HardwareBridgeAdapter,
        socket_path: str = "/var/run/arduino-router.sock",
        initial_config: SystemConfig | None = None,
    ):
        self.adapter = adapter
        self.socket_path = socket_path
        self._desired_cfg = initial_config
        self._stop = threading.Event()
        self._send_lock = threading.Lock()
        self._conn: socket.socket | None = None
        self._next_id = 1
        self._pending: dict[int, tuple[threading.Event, list[Any]]] = {}
        self._pending_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sfs-router-bridge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._send_lock:
            if self._conn is not None:
                with contextlib.suppress(OSError):
                    self._conn.shutdown(socket.SHUT_RDWR)
                    self._conn.close()
                self._conn = None
        if self._thread is not None:
            self._thread.join(timeout=3.0)
            self._thread = None
        self.adapter.router_connected = False

    def push_config(self, cfg: SystemConfig) -> None:
        """Send updated config to the STM32 asynchronously (best-effort)."""
        self._desired_cfg = cfg
        threading.Thread(target=self._push_config_sync, args=(cfg,), daemon=True).start()

    def _push_config_sync(self, cfg: SystemConfig) -> None:
        if not self.adapter.router_connected:
            return
        try:
            self.call("set_config", self.adapter.config_for_mcu(cfg), timeout=4.0)
            log.info("pushed config to STM32 (interval=%ss)", cfg.measurement_interval_s)
        except Exception as exc:
            log.debug("STM32 set_config not acknowledged yet: %s", exc)

    def _send(self, msg: list[Any]) -> None:
        import msgpack

        packed = msgpack.packb(msg, use_bin_type=True)
        with self._send_lock:
            if self._conn is None:
                raise ConnectionError("router socket not connected")
            self._conn.sendall(packed)

    def call(self, method: str, *params: Any, timeout: float = 5.0) -> Any:
        with self._pending_lock:
            msgid = self._next_id
            self._next_id = (self._next_id % 0x7FFFFFFF) + 1
            ev = threading.Event()
            box: list[Any] = []
            self._pending[msgid] = (ev, box)
        try:
            self._send([0, msgid, method, list(params)])
            if not ev.wait(timeout=timeout):
                raise TimeoutError(f"RPC {method} timed out")
            err, res = box[0], box[1]
            if err is not None and not (isinstance(err, list) and err and err[0] == ROUTE_ALREADY_EXISTS_ERR):
                raise RuntimeError(f"RPC {method} error: {err}")
            return res
        finally:
            with self._pending_lock:
                self._pending.pop(msgid, None)

    def _handle_rpc(self, msg: Any) -> None:
        if not isinstance(msg, list) or not msg:
            return
        mtype = msg[0]
        if mtype == 1 and len(msg) == 4:  # Response: [1, msgid, error, result]
            _, msgid, err, result = msg
            with self._pending_lock:
                entry = self._pending.get(msgid)
            if entry is not None:
                ev, box = entry
                box[:] = [err, result]
                ev.set()
        elif mtype == 2 and len(msg) == 3:  # Notification: [2, method, params]
            _, method, params = msg
            mname = method.decode("utf-8") if isinstance(method, bytes) else str(method)
            if mname == "measurement" and isinstance(params, (list, tuple)) and params:
                p = params[0].decode("utf-8") if isinstance(params[0], bytes) else params[0]
                self.adapter.on_measurement(p)
            elif mname == "live" and isinstance(params, (list, tuple)) and params:
                p = params[0].decode("utf-8") if isinstance(params[0], bytes) else params[0]
                self.adapter.on_live(p)
        elif mtype == 0 and len(msg) == 4:  # Request from MCU: [0, msgid, method, params]
            _, msgid, method, params = msg
            mname = method.decode("utf-8") if isinstance(method, bytes) else str(method)
            if mname == "measurement" and isinstance(params, (list, tuple)) and params:
                p = params[0].decode("utf-8") if isinstance(params[0], bytes) else params[0]
                ok = self.adapter.on_measurement(p)
                with contextlib.suppress(Exception):
                    self._send([1, msgid, None, ok])
            elif mname == "get_config":
                cfg = self._desired_cfg or SystemConfig()
                with contextlib.suppress(Exception):
                    self._send([1, msgid, None, self.adapter.config_for_mcu(cfg)])
            else:
                with contextlib.suppress(Exception):
                    self._send([1, msgid, [FUNCTION_NOT_FOUND_ERR, f"unknown {mname}"], None])

    def _run(self) -> None:
        try:
            import msgpack
        except ImportError:
            log.warning("msgpack not installed; UNO Q RouterBridgeRunner disabled")
            return

        while not self._stop.is_set():
            if not Path(self.socket_path).exists():
                self._stop.wait(3.0)
                continue
            sock: socket.socket | None = None
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(5.0)
                sock.connect(self.socket_path)
                sock.settimeout(2.0)
                with self._send_lock:
                    self._conn = sock
                self.adapter.router_connected = True
                log.info("connected to arduino-router at %s", self.socket_path)

                def _setup() -> None:
                    try:
                        self.call("$/register", "measurement", timeout=5.0)
                        self.call("$/register", "live", timeout=5.0)
                        self.call("$/register", "get_config", timeout=5.0)
                        if self._desired_cfg is not None:
                            time.sleep(0.3)
                            self._push_config_sync(self._desired_cfg)
                    except Exception as exc:
                        log.warning("router method registration failed: %s", exc)

                threading.Thread(target=_setup, daemon=True).start()
                unpacker = msgpack.Unpacker(raw=False)
                last_reregister = time.monotonic()
                while not self._stop.is_set():
                    try:
                        chunk = sock.recv(4096)
                    except TimeoutError:
                        if time.monotonic() - last_reregister >= 15.0:
                            last_reregister = time.monotonic()
                            threading.Thread(target=_setup, daemon=True).start()
                        continue
                    if not chunk:
                        break
                    unpacker.feed(chunk)
                    for decoded in unpacker:
                        self._handle_rpc(decoded)
            except Exception as exc:
                if not self._stop.is_set():
                    log.warning("router bridge disconnected: %s", exc)
            finally:
                self.adapter.router_connected = False
                with self._send_lock:
                    self._conn = None
                if sock is not None:
                    with contextlib.suppress(OSError):
                        sock.close()
            self._stop.wait(3.0)


class SimulationRunner:
    """Feeds the simulator into the ingest pipeline at the configured interval."""

    def __init__(
        self,
        simulator: Simulator,
        ingest: IngestService,
        interval_s: float,
        is_on: Callable[[datetime], bool] | None = None,
    ):
        self.simulator = simulator
        self.ingest = ingest
        self.interval_s = interval_s
        self.is_on = is_on or (lambda _when: True)  # night-shutdown schedule
        self._task: asyncio.Task[None] | None = None

    def step(self, when: datetime | None = None) -> bool:
        when = when or datetime.now(UTC)
        if not self.is_on(when):
            self.simulator.power_off(when)  # station is off at night -> no data
            return False
        self.simulator.power_on(when)
        msg = self.simulator.sample(when)
        if msg is None:
            return False
        return self.ingest.ingest(msg, received_at=when).stored

    async def _loop(self) -> None:
        while True:
            try:
                await asyncio.to_thread(self.step)
            except Exception:  # keep the logger alive; errors are logged
                log.exception("simulation step failed")
            await asyncio.sleep(self.interval_s)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
