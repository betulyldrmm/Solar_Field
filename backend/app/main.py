"""FastAPI application factory.

Run (simulation, localhost only):
    cd backend && uvicorn app.main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import APP_VERSION, Settings
from app.services.bridge_service import HardwareBridgeAdapter, SimulationRunner
from app.services.ingest import IngestService
from app.services.power_schedule import is_scheduled_on
from app.simulation.simulator import Simulator
from app.storage.csv_logger import CsvLogger
from app.storage.database import Database

log = logging.getLogger("solar_field_station")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.db_path)
        csv_logger = CsvLogger(settings.csv_dir, settings.csv_fsync_every, settings.min_free_disk_mb)
        ingest = IngestService(db, csv_logger, settings.data_mode)
        cfg = db.load_config()
        app.state.settings = settings
        app.state.db = db
        app.state.ingest = ingest
        app.state.bridge = HardwareBridgeAdapter(ingest)
        db.add_event("info", "service_start", f"v{APP_VERSION}, mode={settings.data_mode}")

        runner: SimulationRunner | None = None
        if settings.data_mode == "simulation" and settings.run_simulator:
            sim = Simulator(latitude_deg=settings.latitude_deg, longitude_deg=settings.longitude_deg,
                            series_cells=settings.battery_series_cells, fan_on_c=cfg.fan_on_c,
                            fan_off_c=cfg.fan_off_c, seed=settings.simulator_seed)
            def _is_on(when):  # simulated station follows the night-shutdown schedule
                return is_scheduled_on(when, db.load_config(), settings.latitude_deg,
                                       settings.longitude_deg, settings.timezone)

            runner = SimulationRunner(sim, ingest, cfg.measurement_interval_s, is_on=_is_on)
            runner.start()

            def _on_change(new_cfg):  # keep simulator in sync with edited config
                sim.fan_on_c, sim.fan_off_c = new_cfg.fan_on_c, new_cfg.fan_off_c
                runner.interval_s = new_cfg.measurement_interval_s

            app.state.on_config_change = _on_change
        try:
            yield
        finally:
            if runner is not None:
                await runner.stop()
            db.add_event("info", "service_stop", "clean shutdown")
            db.close()

    app = FastAPI(title="Solar Field Station API", version=APP_VERSION, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Content-Type", "X-API-Token"],
    )
    app.include_router(router)
    return app


app = create_app()
