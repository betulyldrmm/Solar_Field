"""Fill the database with N days of SIMULATED history (fast, no waiting).

    cd backend && python scripts/seed_simulation.py --days 3
Rows are tagged data_source=simulation.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.services.ingest import IngestService  # noqa: E402
from app.services.power_schedule import is_scheduled_on  # noqa: E402
from app.simulation.simulator import Simulator  # noqa: E402
from app.storage.csv_logger import CsvLogger  # noqa: E402
from app.storage.database import Database  # noqa: E402


def seed(settings: Settings, days: float, interval_s: int, end: datetime | None = None) -> int:
    db = Database(settings.db_path)
    ingest = IngestService(db, CsvLogger(settings.csv_dir, fsync_every=10_000, min_free_mb=0), "simulation")
    sim = Simulator(latitude_deg=settings.latitude_deg, longitude_deg=settings.longitude_deg,
                    series_cells=settings.battery_series_cells, seed=settings.simulator_seed)
    cfg = db.load_config()
    end = end or datetime.now(UTC)
    t = end - timedelta(days=days)
    stored = 0
    while t < end:
        if not is_scheduled_on(t, cfg, settings.latitude_deg, settings.longitude_deg, settings.timezone):
            sim.power_off(t)  # night shutdown: no rows at night
            t += timedelta(seconds=interval_s)
            continue
        sim.power_on(t)
        msg = sim.sample(t)
        if msg is not None and ingest.ingest(msg, received_at=t).stored:
            stored += 1
        t += timedelta(seconds=interval_s)
    db.close()
    return stored


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=3)
    ap.add_argument("--interval", type=int, default=300, help="seconds (default 5 min)")
    args = ap.parse_args()
    s = Settings.from_env()
    print(f"stored {seed(s, args.days, args.interval)} simulated rows into {s.db_path}")
