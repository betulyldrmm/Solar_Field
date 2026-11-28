# Firmware — Arduino UNO Q (STM32U585, Zephyr Core)

Sketch: [`solar_field_station/solar_field_station.ino`](solar_field_station/solar_field_station.ino)

## Architecture
* Runs on the **STM32U585** real-time MCU (`arduino:zephyr:unoq`) alongside the Linux **QRB2210** MPU.
* Samples sensors every **2 seconds**, computes window averages + peak thermal metrics, and sends `schema_version = 1` JSON payloads to the Linux backend over `Arduino_RouterBridge` (`Bridge.notify("measurement", json)`).
* Accepts live configuration updates (`measurement_interval_s`, `fan_on_c`, `fan_off_c`) from FastAPI via `Bridge.provide_safe("set_config", onSetConfig)`.
* Controls the **12 V enclosure fan** locally on pin **D9** (logic-level MOSFET + flyback diode) with hysteresis across enclosure, heatsink, and battery temperatures—continuing safely even if Linux reboots.

## Multi-Sensor & Heat Channels (Auto-Discovered)
| Bus / Pin | Supported Hardware | Mapped Telemetry Fields |
|---|---|---|
| Internal ADC | STM32U585 die sensor (`st_stm32_temp`) | `mcu_temperature_c`, `extra_sensors.mcu_peak_c` |
| I²C (`0x40`) | INA226 (100 mΩ shunt) | `voltage_v`, `current_a`, `power_w` (`ina_ref`) |
| I²C (`0x41`) | INA226 (10 mΩ shunt) | `battery_voltage_v`, `battery_current_a` (`ina_batt`) |
| I²C (`0x44` / `0x45`) | SHT30 / SHT31 / SHT35 | `ambient_temperature_c`, `humidity_pct` |
| I²C (`0x48`–`0x4F`) | Up to 8× LM75 / TMP102 / PCT2075 | `case_temperature_c`, `heatsink_temperature_c`, `battery_temperature_c`, `extra_sensors.i2c_temp_0xXX_c` |
| I²C (`0x5A`) | MLX90614 IR thermometer | `surface_temperature_c` |
| I²C (`0x23`) | BH1750 light sensor | `extra_sensors.illuminance_lux` |
| 1-Wire (`D2`) | Up to 8× DS18B20 probes (4.7 kΩ pull-up to 3.3 V) | Probes 1–5 → `panel`, `ambient`, `case`, `heatsink`, `battery`; Probes 6–8 → `extra_sensors.ds18b20_N_c` |
| Analog (`A0`–`A3`) | 10 kΩ NTC thermistors (`B=3950`, 10 kΩ pull-up) | `extra_sensors.ntc_a0_c` .. `ntc_a3_c` (enable via `g_ntc_mask`) |

## Compile & Flash Directly on UNO Q
```bash
arduino-cli compile --fqbn arduino:zephyr:unoq --output-dir /tmp/sfs_build firmware/solar_field_station
sudo /opt/openocd/bin/openocd -s /opt/openocd -f openocd_gpiod.cfg \
  -c "set filename /tmp/sfs_build/solar_field_station.ino.elf-zsk.bin" \
  -f /home/arduino/.arduino15/packages/arduino/hardware/zephyr/0.51.0/variants/arduino_uno_q_stm32u585xx/flash_sketch.cfg
```
