"""Measurement sources: simulator (now) and Arduino UNO Q Bridge (hardware).

The hardware path is intentionally a thin adapter with NO dependency on the
Arduino Python package, so the backend stays testable on a PC.

DOĞRULAMA GEREKİYOR: The UNO Q Bridge/RPC API (App Lab, `arduino.app_utils`
on Linux, `Arduino_RouterBridge` on the STM32) must be checked against the
board's current official docs/firmware before Phase 3. The planned wiring:

    STM32:  Bridge.notify("measurement", json_string)     every N seconds
    Linux:  Bridge.provide("measurement", adapter.on_measurement)
    Linux -> STM32 config:  Bridge.call("set_config", adapter.config_for_mcu())

Only `on_measurement` / `config_for_mcu` below are the stable interface.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from app.models.schemas import SystemConfig
from app.services.ingest import IngestService
from app.simulation.simulator import Simulator

log = logging.getLogger(__name__)


class HardwareBridgeAdapter:
    """Glue between the UNO Q Bridge callback and the ingest pipeline."""

    def __init__(self, ingest: IngestService):
        self.ingest = ingest
        self.last_message_utc: datetime | None = None

    def on_measurement(self, payload: str) -> bool:
        """Called from the Bridge with the STM32's JSON string. Returns True if stored."""
        self.last_message_utc = datetime.now(UTC)
        outcome = self.ingest.ingest(payload, received_at=self.last_message_utc)
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
