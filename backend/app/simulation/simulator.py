"""Synthetic sensor source. Produces messages in exactly the STM32 schema (v1),
so the same validator/ingest path is exercised as with real hardware.

Everything here is SYNTHETIC and always tagged data_source="simulation".
Magnitudes are kept physically plausible:
* clear-sky irradiance from sun elevation (Haurwitz model), scaled by a
  random-walk cloud factor;
* reference panel Isc ∝ irradiance; the "true" panel factor differs from the
  nameplate on purpose (calibration is needed, as in reality);
* panel temperature via NOCT model, ambient with a daily cycle;
* 3S Li-ion pack (default): charged by a 20 W panel, drained by ~2.7 W base
  load plus the 12 V fan (3.6 W at 12 V, scales with pack voltage²);
* night shutdown: `power_off()` / `power_on()` model a powered-down station
  (no load, no data; a new boot_id and sequence after every wake-up);
* occasional DS18B20 disconnects and dropped messages.
"""

from __future__ import annotations

import math
import random
from datetime import datetime

from app.config import SCHEMA_VERSION
from app.services.battery_monitor import OCV_CURVE
from app.services.solar_position import sun_elevation_deg

NAMEPLATE_ISC_A = 0.150
TRUE_ISC_FACTOR = 0.93  # real panel delivers 93 % of nameplate -> calibration matters
NOCT_C = 45.0
LOAD_W = 2.7  # UNO Q + sensors + LCD via 5 V buck (assumption)
FAN_W_AT_12V = 12.0 * 0.3  # 12 V 0.3 A fan, driven straight from the 3S pack
CHARGE_PANEL_W = 20.0
CHARGE_EFF = 0.75
PACK_CAPACITY_WH_PER_CELL = 3.7 * 3.0  # ~11 Wh per (aged) parallel group cell
SHUNT_PLUS_WIRING_OHM = 0.3


def clear_sky_ghi(elevation_deg: float) -> float:
    if elevation_deg <= 0:
        return 0.0
    s = math.sin(math.radians(elevation_deg))
    return 1098.0 * s * math.exp(-0.057 / s)


def _ocv(soc: float) -> float:
    soc = max(0.0, min(100.0, soc))
    for (v0, p0), (v1, p1) in zip(OCV_CURVE, OCV_CURVE[1:], strict=False):
        if p0 <= soc <= p1:
            return v0 + (v1 - v0) * (soc - p0) / (p1 - p0)
    return OCV_CURVE[-1][0]


class Simulator:
    def __init__(
        self,
        *,
        latitude_deg: float,
        longitude_deg: float,
        series_cells: int = 3,
        parallel_cells: int = 6,
        fan_on_c: float = 40.0,
        fan_off_c: float = 35.0,
        seed: int = 42,
        fault_rate: float = 0.002,
        drop_rate: float = 0.001,
    ):
        self.lat = latitude_deg
        self.lon = longitude_deg
        self.series = series_cells
        self.capacity_wh = PACK_CAPACITY_WH_PER_CELL * series_cells * parallel_cells
        self.fan_on_c = fan_on_c
        self.fan_off_c = fan_off_c
        self.rng = random.Random(seed)
        self.fault_rate = fault_rate
        self.drop_rate = drop_rate
        self.boot_id = self._new_boot_id()
        self.uptime_ms = 0
        self.powered = True
        self.sequence = 0
        self.cloud = 0.8
        self.soc = 70.0
        self.fan = False
        self._last: datetime | None = None

    def _new_boot_id(self) -> str:
        return f"sim{self.rng.randrange(16**5):05x}"

    def power_off(self, when: datetime) -> None:
        """Station switched off (night). No load is drawn while off."""
        self.powered = False
        self.fan = False
        self._last = when

    def power_on(self, when: datetime) -> None:
        """Wake-up = STM32 reboot: new boot_id, sequence restarts."""
        if not self.powered:
            self.powered = True
            self.boot_id = self._new_boot_id()
            self.sequence = 0
            self.uptime_ms = 0
            self._last = when

    def _ambient(self, when: datetime) -> float:
        month = when.month
        # rough monthly mean for Thrace region, °C
        mean = 13.5 - 11.0 * math.cos(2 * math.pi * (month - 1.5) / 12)
        local_hour = (when.hour + when.minute / 60 + self.lon / 15) % 24
        return mean + 5.0 * math.cos(2 * math.pi * (local_hour - 15) / 24)

    def sample(self, when: datetime) -> dict | None:
        """Returns a raw message, or None when the message is 'dropped'."""
        if not self.powered:
            raise RuntimeError("sample() while powered off; call power_on() first")
        dt_s = 5.0 if self._last is None else max(0.0, (when - self._last).total_seconds())
        self._last = when
        self.sequence += 1
        self.uptime_ms += int(dt_s * 1000)

        # cloud random walk, mean-reverting toward 0.75
        steps = max(1, int(dt_s / 5))
        for _ in range(min(steps, 200)):
            self.cloud += 0.02 * (0.75 - self.cloud) + self.rng.gauss(0, 0.03)
            self.cloud = min(1.0, max(0.15, self.cloud))

        elev = sun_elevation_deg(when, self.lat, self.lon)
        g = clear_sky_ghi(elev) * self.cloud
        t_amb = self._ambient(when) + self.rng.gauss(0, 0.1)
        t_panel = t_amb + g / 800.0 * (NOCT_C - 20.0) + self.rng.gauss(0, 0.2)
        t_case = t_amb + 4.0 + g / 1000.0 * 12.0 - (6.0 if self.fan else 0.0)
        t_heatsink = t_case + 3.2 + (2.5 if self.fan else 4.8) * (g / 1000.0)
        t_batt = t_amb + 2.1 + g / 1000.0 * 4.5
        t_mcu = t_case + 5.4
        humidity = max(20.0, min(95.0, 68.0 - 0.9 * (t_amb - 15.0) + self.rng.gauss(0, 0.6)))
        pressure = 1013.2 + 3.5 * math.sin(2 * math.pi * (when.timetuple().tm_yday / 14.0))

        if max(t_case, t_heatsink - 4.0) >= self.fan_on_c:
            self.fan = True
        elif max(t_case, t_heatsink - 4.0) <= self.fan_off_c:
            self.fan = False

        isc = NAMEPLATE_ISC_A * TRUE_ISC_FACTOR * g / 1000.0 * (1 + 0.0005 * (t_panel - 25))
        isc = max(0.0, isc + self.rng.gauss(0, 0.0002))
        v = isc * SHUNT_PLUS_WIRING_OHM

        charge_w = CHARGE_PANEL_W * g / 1000.0 * CHARGE_EFF
        pack_v = self.series * _ocv(self.soc)
        fan_w = FAN_W_AT_12V * (pack_v / 12.0) ** 2 if self.fan else 0.0
        load_w = LOAD_W + fan_w
        self.soc += (charge_w - load_w) * dt_s / 3600.0 / self.capacity_wh * 100.0
        self.soc = max(0.0, min(100.0, self.soc))
        batt_i = (charge_w - load_w) / (3.7 * self.series)
        batt_v = self.series * (_ocv(self.soc) + 0.05 * batt_i / max(1, self.series))

        if self.rng.random() < self.drop_rate:
            return None

        sensors = {
            "ina_ref": "OK", "ina_batt": "OK", "t_panel": "OK", "t_ambient": "OK",
            "t_case": "OK", "t_heatsink": "OK", "t_battery": "OK", "t_mcu": "OK",
            "env_humidity": "OK", "env_pressure": "OK",
        }
        temps = {
            "panel_temperature_c": t_panel,
            "ambient_temperature_c": t_amb,
            "case_temperature_c": t_case,
        }
        if self.rng.random() < self.fault_rate:
            key = self.rng.choice(list(temps))
            sensor = {"panel_temperature_c": "t_panel", "ambient_temperature_c": "t_ambient",
                      "case_temperature_c": "t_case"}[key]
            if self.rng.random() < 0.5:
                temps[key] = -127.0  # firmware bug-path: sentinel leaked through
            else:
                temps[key] = None
                sensors[sensor] = "DISCONNECTED"

        return {
            "schema_version": SCHEMA_VERSION,
            "boot_id": self.boot_id,
            "sequence": self.sequence,
            "uptime_ms": self.uptime_ms,
            "voltage_v": round(v, 4),
            "current_a": round(isc, 5),
            "power_w": round(v * isc, 6),
            **{k: (None if val is None else round(val, 2)) for k, val in temps.items()},
            "heatsink_temperature_c": round(t_heatsink, 2),
            "battery_temperature_c": round(t_batt, 2),
            "mcu_temperature_c": round(t_mcu, 2),
            "humidity_pct": round(humidity, 1),
            "pressure_hpa": round(pressure, 1),
            "battery_voltage_v": round(batt_v, 3),
            "battery_current_a": round(batt_i, 3),
            "fan_state": self.fan,
            "sensor_status": "OK" if all(s == "OK" for s in sensors.values()) else "DEGRADED",
            "sensors": sensors,
            "extra_sensors": {
                "panel_backsheet_2_c": round(t_panel - 1.1, 2),
                "regulator_buck_5v_c": round(t_heatsink - 1.8, 2),
            },
        }
