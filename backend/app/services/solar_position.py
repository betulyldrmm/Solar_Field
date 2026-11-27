"""Sun elevation (NOAA simplified algorithm, accuracy ~±0.5° — enough to decide
day/night and to shape the simulator; not used to fabricate irradiance values
in hardware mode)."""

from __future__ import annotations

import math
from datetime import UTC, datetime


def sun_elevation_deg(when: datetime, latitude_deg: float, longitude_deg: float) -> float:
    if when.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    t = when.astimezone(UTC)
    day_of_year = t.timetuple().tm_yday
    hour = t.hour + t.minute / 60 + t.second / 3600
    gamma = 2 * math.pi / 365 * (day_of_year - 1 + (hour - 12) / 24)

    eq_time_min = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.040849 * math.sin(2 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.00148 * math.sin(3 * gamma)
    )
    true_solar_min = hour * 60 + eq_time_min + 4 * longitude_deg
    hour_angle = math.radians(true_solar_min / 4 - 180)
    lat = math.radians(latitude_deg)
    cos_zenith = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(
        hour_angle
    )
    cos_zenith = max(-1.0, min(1.0, cos_zenith))
    return 90.0 - math.degrees(math.acos(cos_zenith))


def is_daylight(when: datetime, latitude_deg: float, longitude_deg: float) -> bool:
    # -0.833° accounts for refraction + solar disc radius (standard sunrise definition)
    return sun_elevation_deg(when, latitude_deg, longitude_deg) > -0.833
