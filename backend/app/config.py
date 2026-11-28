"""Runtime settings, read from environment variables (prefix SFS_).

Only process-level settings live here (paths, network, mode). User-editable
system parameters (panel power, fan thresholds, ...) live in the database
(see app.models.schemas.SystemConfig) so they can be changed from the API/LCD.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

APP_VERSION = "0.1.0"
SCHEMA_VERSION = 1  # STM32 -> Linux message schema version


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass
class Settings:
    data_dir: Path = Path("data")
    # "simulation" or "hardware". Never mixed: every stored row carries its source.
    data_mode: str = "simulation"
    run_simulator: bool = False
    simulator_seed: int = 42
    # Network: bind to localhost by default. Exposing on LAN is an explicit choice.
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: list[str] = field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )
    # Config changes require this token. Empty -> PUT /api/config is disabled.
    api_token: str = ""
    # Site location (used for sun position: night detection, simulator).
    latitude_deg: float = 41.735  # Kırklareli (assumption, set per site)
    longitude_deg: float = 27.225
    timezone: str = "Europe/Istanbul"
    # Battery pack topology: 1 for 1S (3.0-4.2 V), 3 for 3S (9.0-12.6 V).
    battery_series_cells: int = 3
    # Storage safety
    min_free_disk_mb: int = 200
    csv_fsync_every: int = 1  # fsync every row: at 5 min/row nothing may be lost on power cut

    @property
    def db_path(self) -> Path:
        return self.data_dir / "solar_field_station.db"

    @property
    def csv_dir(self) -> Path:
        return self.data_dir / "csv"

    @classmethod
    def from_env(cls) -> Settings:
        s = cls()
        s.data_dir = Path(os.environ.get("SFS_DATA_DIR", str(s.data_dir)))
        s.data_mode = os.environ.get("SFS_DATA_MODE", s.data_mode).strip().lower()
        if s.data_mode not in {"simulation", "hardware"}:
            raise ValueError("SFS_DATA_MODE must be 'simulation' or 'hardware'")
        s.run_simulator = _env_bool("SFS_RUN_SIMULATOR", s.data_mode == "simulation")
        s.simulator_seed = int(os.environ.get("SFS_SIMULATOR_SEED", s.simulator_seed))
        s.host = os.environ.get("SFS_HOST", s.host)
        s.port = int(os.environ.get("SFS_PORT", s.port))
        s.cors_origins = _env_list("SFS_CORS_ORIGINS", s.cors_origins)
        s.api_token = os.environ.get("SFS_API_TOKEN", s.api_token)
        s.latitude_deg = float(os.environ.get("SFS_LATITUDE", s.latitude_deg))
        s.longitude_deg = float(os.environ.get("SFS_LONGITUDE", s.longitude_deg))
        s.timezone = os.environ.get("SFS_TIMEZONE", s.timezone)
        s.battery_series_cells = int(
            os.environ.get("SFS_BATTERY_SERIES_CELLS", s.battery_series_cells)
        )
        if s.battery_series_cells < 1 or s.battery_series_cells > 4:
            raise ValueError("SFS_BATTERY_SERIES_CELLS must be 1..4")
        s.min_free_disk_mb = int(os.environ.get("SFS_MIN_FREE_DISK_MB", s.min_free_disk_mb))
        return s
