"""Real-time high-frequency telemetry hub (1.5 s resolution ring buffer).

Provides immediate live oscilloscope series, Qualcomm SoC multi-zone thermal
readings, Linux CPU/RAM/Wi-Fi system health, and STM32U585 live 2 s snapshots
without polluting the 5-minute calibrated SQLite energy database.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import shutil
import threading
from collections import deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.models.schemas import SystemConfig
from app.services.battery_monitor import soc_from_voltage
from app.services.energy_calculator import irradiance_wm2
from app.simulation.simulator import Simulator

log = logging.getLogger("sfs.live")


def _read_proc_cpu_times() -> tuple[int, int] | None:
    try:
        line = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0]
        parts = [int(x) for x in line.split()[1:]]
        idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
        return sum(parts), idle
    except (OSError, ValueError, IndexError):
        return None


def _read_mem_info() -> tuple[float, float]:
    try:
        info: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if ":" in line:
                k, rest = line.split(":", 1)
                val = rest.strip().split()[0]
                info[k] = int(val)
        total_kb = info.get("MemTotal", 0)
        avail_kb = info.get("MemAvailable", info.get("MemFree", 0))
        if total_kb > 0:
            used_kb = max(0, total_kb - avail_kb)
            return round(used_kb * 100.0 / total_kb, 1), round(used_kb / 1024.0, 1)
    except (OSError, ValueError, IndexError):
        pass
    return 0.0, 0.0


def _read_wifi_dbm() -> float | None:
    try:
        lines = Path("/proc/net/wireless").read_text(encoding="utf-8").splitlines()
        for line in lines[2:]:
            if ":" in line:
                _, rest = line.split(":", 1)
                cols = rest.split()
                if len(cols) >= 3:
                    return float(cols[2].rstrip("."))
    except (OSError, ValueError):
        pass
    return None


def read_all_thermal_zones() -> dict[str, float]:
    root = Path("/sys/class/thermal")
    if not root.is_dir():
        return {}
    out: dict[str, float] = {}
    for z in sorted(root.glob("thermal_zone*")):
        try:
            ztype = (z / "type").read_text(encoding="utf-8").strip()
            raw = int((z / "temp").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        c = round(raw / 1000.0, 2)
        if -40.0 <= c <= 130.0:
            out[ztype] = c
    return out


class LiveTelemetryHub:
    """Maintains separate live ring buffers for `hardware` and `simulation` modes."""

    def __init__(
        self,
        *,
        data_dir: Path,
        latitude_deg: float,
        longitude_deg: float,
        series_cells: int = 3,
        maxlen: int = 180,
    ):
        self.data_dir = data_dir
        self.series_cells = series_cells
        self._lock = threading.Lock()
        self._buffers: dict[str, deque[dict[str, Any]]] = {
            "hardware": deque(maxlen=maxlen),
            "simulation": deque(maxlen=maxlen),
        }
        self._last_mcu_live: dict[str, Any] = {}
        self._last_mcu_utc: datetime | None = None
        self._prev_cpu = _read_proc_cpu_times()
        self._cpu_pct = 0.0
        self._tick_seq = 0
        self._sim = Simulator(
            latitude_deg=latitude_deg,
            longitude_deg=longitude_deg,
            series_cells=series_cells,
            seed=99,
            fault_rate=0.0,
            drop_rate=0.0,
        )
        self._task: asyncio.Task[None] | None = None
        self._cfg = SystemConfig()

    def update_config(self, cfg: SystemConfig) -> None:
        self._cfg = cfg
        self._sim.fan_on_c = cfg.fan_on_c
        self._sim.fan_off_c = cfg.fan_off_c

    def on_mcu_payload(self, payload: str | bytes | dict[str, Any]) -> None:
        """Ingest a fast 2 s live or window packet from the STM32U585."""
        try:
            data = json.loads(payload) if isinstance(payload, (str, bytes)) else dict(payload)
            if not isinstance(data, dict):
                return
        except (json.JSONDecodeError, TypeError, ValueError):
            return
        with self._lock:
            self._last_mcu_live = data
            self._last_mcu_utc = datetime.now(UTC)

    def _sample_cpu_pct(self) -> float:
        cur = _read_proc_cpu_times()
        if cur is not None and self._prev_cpu is not None:
            dt_total = cur[0] - self._prev_cpu[0]
            dt_idle = cur[1] - self._prev_cpu[1]
            if dt_total > 0:
                self._cpu_pct = round(max(0.0, min(100.0, (dt_total - dt_idle) * 100.0 / dt_total)), 1)
        if cur is not None:
            self._prev_cpu = cur
        return self._cpu_pct

    def step(self) -> None:
        now = datetime.now(UTC)
        self._tick_seq += 1
        cpu_pct = self._sample_cpu_pct()
        mem_pct, mem_mb = _read_mem_info()
        wifi_dbm = _read_wifi_dbm()
        zones = read_all_thermal_zones()
        try:
            free_mb = round(shutil.disk_usage(self.data_dir).free / (1024 * 1024), 1)
        except OSError:
            free_mb = 0.0

        mpu_c = round(max(zones.values()), 2) if zones else None

        with self._lock:
            mcu = dict(self._last_mcu_live)
            mcu_utc = self._last_mcu_utc

        mcu_age = (now - mcu_utc).total_seconds() if mcu_utc else None
        mcu_online = mcu_age is not None and mcu_age < 30.0
        extra_hw = dict(mcu.get("extra_sensors") or {}) if isinstance(mcu.get("extra_sensors"), dict) else {}
        adc_hw = {
            k: float(v)
            for k, v in extra_hw.items()
            if k.startswith("adc_a") and isinstance(v, (int, float))
        }
        v_hw = mcu.get("voltage_v")
        i_hw = mcu.get("current_a")
        p_hw = mcu.get("power_w")
        if p_hw is None and isinstance(v_hw, (int, float)) and isinstance(i_hw, (int, float)):
            p_hw = round(v_hw * i_hw, 6)
        tp_hw = mcu.get("panel_temperature_c")
        irr_hw = mcu.get("irradiance_measured_wm2")
        if irr_hw is None and isinstance(i_hw, (int, float)):
            t_arg = float(tp_hw) if isinstance(tp_hw, (int, float)) else None
            irr_hw = irradiance_wm2(float(i_hw), t_arg, self._cfg)
            if irr_hw is not None:
                irr_hw = round(irr_hw, 1)
        bv_hw = mcu.get("battery_voltage_v")
        soc_hw = (
            soc_from_voltage(float(bv_hw), self.series_cells)
            if isinstance(bv_hw, (int, float))
            else None
        )

        hw_point: dict[str, Any] = {
            "timestamp_utc": now.isoformat(timespec="milliseconds"),
            "seq": int(mcu.get("sequence") or self._tick_seq),
            "uptime_s": round(float(mcu.get("uptime_ms", 0)) / 1000.0, 1) if mcu else 0.0,
            "mcu_online": mcu_online,
            "mcu_age_s": round(mcu_age, 1) if mcu_age is not None else None,
            "cpu_pct": cpu_pct,
            "mem_pct": mem_pct,
            "mem_used_mb": mem_mb,
            "disk_free_mb": free_mb,
            "wifi_dbm": wifi_dbm,
            "mpu_temperature_c": mpu_c,
            "mcu_temperature_c": mcu.get("mcu_temperature_c"),
            "panel_temperature_c": tp_hw,
            "ambient_temperature_c": mcu.get("ambient_temperature_c"),
            "case_temperature_c": mcu.get("case_temperature_c"),
            "heatsink_temperature_c": mcu.get("heatsink_temperature_c"),
            "battery_temperature_c": mcu.get("battery_temperature_c"),
            "surface_temperature_c": mcu.get("surface_temperature_c"),
            "humidity_pct": mcu.get("humidity_pct"),
            "pressure_hpa": mcu.get("pressure_hpa"),
            "voltage_v": v_hw,
            "current_a": i_hw,
            "power_w": p_hw,
            "irradiance_wm2": irr_hw,
            "battery_voltage_v": bv_hw,
            "battery_current_a": mcu.get("battery_current_a"),
            "battery_soc_pct": round(soc_hw, 1) if soc_hw is not None else None,
            "fan_state": mcu.get("fan_state"),
            "adc_mv": adc_hw,
            "thermal_zones": zones,
            "extra_sensors": extra_hw,
            "data_source": "hardware",
        }

        # Synthetic live stream for simulation tab (always active, smooth daytime demo curve if night)
        sim_when = now
        sim_msg = self._sim.sample(sim_when) or {}
        # If sun is currently down in local winter evening, synthesize a lively demo solar wave
        sim_i = float(sim_msg.get("current_a") or 0.0)
        phase = self._tick_seq * 0.12
        if sim_i < 0.005:
            demo_g = 620.0 + 180.0 * math.sin(phase * 0.35) + 45.0 * math.cos(phase * 1.3)
            sim_i = round(0.140 * (demo_g / 1000.0), 5)
            sim_v = round(sim_i * 0.3, 4)
            sim_tp = round(38.5 + 4.2 * math.sin(phase * 0.25), 2)
            sim_ta = round(21.4 + 0.8 * math.cos(phase * 0.15), 2)
            sim_tc = round(29.8 + 2.1 * math.sin(phase * 0.2), 2)
            sim_th = round(36.2 + 3.0 * math.sin(phase * 0.22), 2)
        else:
            sim_v = float(sim_msg.get("voltage_v") or 0.0)
            sim_tp = float(sim_msg.get("panel_temperature_c") or 25.0)
            sim_ta = float(sim_msg.get("ambient_temperature_c") or 20.0)
            sim_tc = float(sim_msg.get("case_temperature_c") or 26.0)
            sim_th = float(sim_msg.get("heatsink_temperature_c") or 30.0)
            demo_g = irradiance_wm2(sim_i, sim_tp, self._cfg) or 0.0

        sim_bv = float(sim_msg.get("battery_voltage_v") or 11.82) + 0.02 * math.sin(phase * 0.4)
        sim_soc = soc_from_voltage(sim_bv, self.series_cells)
        sim_zones = {
            "cpuss0-thermal": round(34.2 + 1.8 * math.sin(phase * 0.5), 2),
            "gpu-thermal": round(33.1 + 1.4 * math.cos(phase * 0.4), 2),
            "wlan-thermal": round(35.0 + 1.1 * math.sin(phase * 0.3), 2),
            "hm-center-thermal": round(34.8 + 1.5 * math.sin(phase * 0.45), 2),
        }
        sim_point: dict[str, Any] = {
            "timestamp_utc": now.isoformat(timespec="milliseconds"),
            "seq": self._tick_seq,
            "uptime_s": round(self._tick_seq * 1.5, 1),
            "mcu_online": True,
            "mcu_age_s": 0.4,
            "cpu_pct": round(12.0 + 6.0 * abs(math.sin(phase * 0.6)), 1),
            "mem_pct": mem_pct or 18.5,
            "mem_used_mb": mem_mb or 640.0,
            "disk_free_mb": free_mb or 16800.0,
            "wifi_dbm": wifi_dbm if wifi_dbm is not None else -54.0,
            "mpu_temperature_c": sim_zones["hm-center-thermal"],
            "mcu_temperature_c": round(sim_tc + 5.1, 2),
            "panel_temperature_c": sim_tp,
            "ambient_temperature_c": sim_ta,
            "case_temperature_c": sim_tc,
            "heatsink_temperature_c": sim_th,
            "battery_temperature_c": round(sim_ta + 3.4, 2),
            "surface_temperature_c": round(sim_tp + 1.6, 2),
            "humidity_pct": sim_msg.get("humidity_pct", 48.2),
            "pressure_hpa": sim_msg.get("pressure_hpa", 1014.5),
            "voltage_v": sim_v,
            "current_a": sim_i,
            "power_w": round(sim_v * sim_i, 6),
            "irradiance_wm2": round(demo_g, 1),
            "battery_voltage_v": round(sim_bv, 3),
            "battery_current_a": sim_msg.get("battery_current_a", 0.42),
            "battery_soc_pct": round(sim_soc, 1) if sim_soc is not None else 74.0,
            "fan_state": bool(sim_th >= self._cfg.fan_on_c),
            "adc_mv": {
                "adc_a0_mv": round(1650 + 180 * math.sin(phase), 1),
                "adc_a1_mv": round(920 + 95 * math.cos(phase * 0.8), 1),
                "adc_a2_mv": round(2410 + 60 * math.sin(phase * 0.5), 1),
                "adc_a3_mv": round(410 + 35 * math.cos(phase * 1.2), 1),
            },
            "thermal_zones": sim_zones,
            "extra_sensors": {
                "panel_backsheet_2_c": round(sim_tp - 1.2, 2),
                "regulator_buck_5v_c": round(sim_th - 1.7, 2),
                "illuminance_lux": round(demo_g * 112.0, 0),
            },
            "data_source": "simulation",
        }

        with self._lock:
            self._buffers["hardware"].append(hw_point)
            self._buffers["simulation"].append(sim_point)

    def snapshot(self, source: str, limit: int = 120) -> dict[str, Any]:
        with self._lock:
            buf = list(self._buffers.get(source, ()))
        if not buf:
            self.step()
            with self._lock:
                buf = list(self._buffers.get(source, ()))
        sliced = buf[-limit:]
        return {
            "source": source,
            "count": len(sliced),
            "latest": sliced[-1] if sliced else None,
            "series": sliced,
        }

    async def _loop(self, interval_s: float = 1.5) -> None:
        # Pre-seed 20 points on startup so charts immediately look rich
        for _ in range(12):
            await asyncio.to_thread(self.step)
        while True:
            try:
                await asyncio.to_thread(self.step)
            except Exception:
                log.exception("live telemetry tick failed")
            await asyncio.sleep(interval_s)

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
