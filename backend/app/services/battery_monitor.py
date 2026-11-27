"""Voltage-based Li-ion state-of-charge estimate.

This is only a rough estimate: terminal voltage under load/charge differs from
open-circuit voltage, and old laptop cells deviate from the curve. Labelled as
"estimated" everywhere. Coulomb counting via the second INA226 is future work.
"""

from __future__ import annotations

from app.models.schemas import BatteryStatus

# Typical resting OCV curve for one NMC/LCO 18650 cell: (volts, percent)
OCV_CURVE: list[tuple[float, float]] = [
    (3.00, 0), (3.30, 5), (3.45, 10), (3.60, 20), (3.70, 35),
    (3.77, 50), (3.85, 60), (3.93, 70), (4.00, 80), (4.08, 90), (4.20, 100),
]
CELL_MIN_V = 3.0
CELL_MAX_V = 4.2


def soc_from_voltage(pack_voltage_v: float | None, series_cells: int) -> float | None:
    if pack_voltage_v is None or series_cells < 1:
        return None
    cell_v = pack_voltage_v / series_cells
    if cell_v < CELL_MIN_V - 0.5 or cell_v > CELL_MAX_V + 0.3:
        return None  # implausible -> wrong series count or sensor fault
    if cell_v <= OCV_CURVE[0][0]:
        return 0.0
    if cell_v >= OCV_CURVE[-1][0]:
        return 100.0
    for (v0, p0), (v1, p1) in zip(OCV_CURVE, OCV_CURVE[1:], strict=False):
        if v0 <= cell_v <= v1:
            return round(p0 + (p1 - p0) * (cell_v - v0) / (v1 - v0), 1)
    return None


def battery_status(voltage_v: float | None, current_a: float | None, series_cells: int) -> BatteryStatus:
    return BatteryStatus(
        voltage_v=voltage_v,
        current_a=current_a,
        soc_pct_estimated=soc_from_voltage(voltage_v, series_cells),
        series_cells=series_cells,
        note="Voltage-based estimate; inaccurate under load. Not a measured SoC.",
    )
