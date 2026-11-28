"""FastAPI application factory.

Serves `/api/*`, optional Bluetooth PAN captive-portal redirects so phones and
laptops auto-open the dashboard on connect, and the compiled React `frontend/dist`
bundle when present.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import APP_VERSION, Settings
from app.models.schemas import SystemConfig
from app.services.bridge_service import HardwareBridgeAdapter, RouterBridgeRunner, SimulationRunner
from app.services.ingest import IngestService
from app.services.live_telemetry import LiveTelemetryHub
from app.services.power_schedule import is_scheduled_on
from app.simulation.simulator import Simulator
from app.storage.csv_logger import CsvLogger
from app.storage.database import Database

log = logging.getLogger("solar_field_station")

CAPTIVE_PROBE_PATHS = (
    "/generate_204",
    "/gen_204",
    "/hotspot-detect.html",
    "/library/test/success.html",
    "/ncsi.txt",
    "/connecttest.txt",
    "/redirect",
    "/canonical.html",
    "/success.txt",
)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.db_path)
        csv_logger = CsvLogger(settings.csv_dir, settings.csv_fsync_every, settings.min_free_disk_mb)
        ingest = IngestService(db, csv_logger, settings.data_mode)
        cfg = db.load_config()
        is_hw = settings.data_mode == "hardware"
        live_hub = LiveTelemetryHub(
            data_dir=settings.data_dir,
            latitude_deg=settings.latitude_deg,
            longitude_deg=settings.longitude_deg,
            series_cells=settings.battery_series_cells,
        )
        live_hub.update_config(cfg)
        live_hub.start()
        bridge_adapter = HardwareBridgeAdapter(ingest, enrich_host_thermal=is_hw, live_hub=live_hub)
        app.state.settings = settings
        app.state.db = db
        app.state.ingest = ingest
        app.state.bridge = bridge_adapter
        app.state.live_hub = live_hub
        db.add_event("info", "service_start", f"v{APP_VERSION}, mode={settings.data_mode}")

        runner: SimulationRunner | None = None
        bridge_runner: RouterBridgeRunner | None = None
        change_hooks: list = [live_hub.update_config]

        if settings.data_mode == "simulation" and settings.run_simulator:
            sim = Simulator(latitude_deg=settings.latitude_deg, longitude_deg=settings.longitude_deg,
                            series_cells=settings.battery_series_cells, fan_on_c=cfg.fan_on_c,
                            fan_off_c=cfg.fan_off_c, seed=settings.simulator_seed)

            def _is_on(when):  # simulated station follows the night-shutdown schedule
                return is_scheduled_on(when, db.load_config(), settings.latitude_deg,
                                       settings.longitude_deg, settings.timezone)

            runner = SimulationRunner(sim, ingest, cfg.measurement_interval_s, is_on=_is_on)
            runner.start()

            def _sim_change(new_cfg: SystemConfig) -> None:
                sim.fan_on_c, sim.fan_off_c = new_cfg.fan_on_c, new_cfg.fan_off_c
                runner.interval_s = new_cfg.measurement_interval_s

            change_hooks.append(_sim_change)

        if is_hw and settings.enable_bridge and Path(settings.router_socket).exists():
            bridge_runner = RouterBridgeRunner(bridge_adapter, settings.router_socket, initial_config=cfg)
            bridge_runner.start()
            change_hooks.append(bridge_runner.push_config)

        if change_hooks:
            def _on_change(new_cfg: SystemConfig) -> None:
                for fn in change_hooks:
                    fn(new_cfg)

            app.state.on_config_change = _on_change

        try:
            yield
        finally:
            await live_hub.stop()
            if runner is not None:
                await runner.stop()
            if bridge_runner is not None:
                bridge_runner.stop()
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

    if settings.captive_portal_url:
        portal_url = settings.captive_portal_url

        @app.get("/captive-portal-api", include_in_schema=False)
        def captive_api() -> JSONResponse:
            return JSONResponse(
                {"captive": True, "user-portal-url": portal_url},
                media_type="application/captive+json",
            )

        def _captive_redirect() -> RedirectResponse:
            return RedirectResponse(url=portal_url, status_code=302)

        for probe in CAPTIVE_PROBE_PATHS:
            app.add_api_route(probe, _captive_redirect, methods=["GET", "HEAD"], include_in_schema=False)
    else:
        # Silent validation mode: return exact 204 / Success responses so Android & iOS
        # validate Bluetooth PAN (`10.44.0.1/24`) instead of routing `10.44.0.1` over 5G CGNAT.
        @app.api_route("/generate_204", methods=["GET", "HEAD"], include_in_schema=False)
        @app.api_route("/gen_204", methods=["GET", "HEAD"], include_in_schema=False)
        def android_204() -> Response:
            return Response(status_code=204)

        @app.api_route("/hotspot-detect.html", methods=["GET", "HEAD"], include_in_schema=False)
        @app.api_route("/library/test/success.html", methods=["GET", "HEAD"], include_in_schema=False)
        def apple_success() -> HTMLResponse:
            return HTMLResponse("<HTML><HEAD><TITLE>Success</TITLE></HEAD><BODY>Success</BODY></HTML>")

        @app.api_route("/connecttest.txt", methods=["GET", "HEAD"], include_in_schema=False)
        def ms_connecttest() -> PlainTextResponse:
            return PlainTextResponse("Microsoft Connect Test")

        @app.api_route("/ncsi.txt", methods=["GET", "HEAD"], include_in_schema=False)
        def ms_ncsi() -> PlainTextResponse:
            return PlainTextResponse("Microsoft NCSI")

    dist = settings.frontend_dist
    index_html = dist / "index.html"
    if index_html.is_file():
        assets_dir = dist / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
        def serve_root() -> FileResponse:
            return FileResponse(index_html)

        @app.get("/{full_path:path}", include_in_schema=False)
        def serve_spa(full_path: str, request: Request):
            candidate = (dist / full_path).resolve()
            if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            if settings.captive_portal_url and request.headers.get("host", "").lower() not in {
                settings.bt_pan_ip,
                f"{settings.bt_pan_ip}:{settings.port}",
                "localhost",
                f"localhost:{settings.port}",
                "127.0.0.1",
                f"127.0.0.1:{settings.port}",
                "uno-q.local",
                "solar.field",
            }:
                return RedirectResponse(url=settings.captive_portal_url, status_code=302)
            return FileResponse(index_html)

    return app


app = create_app()
