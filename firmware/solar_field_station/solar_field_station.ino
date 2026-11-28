/*
 * Solar Field Station — Arduino UNO Q (STM32U585, Zephyr core) Firmware
 *
 * Architecture:
 *   - Samples connected electrical, thermal/heat, and environmental sensors every 2 s.
 *   - Maintains autonomous local 12 V fan control with hysteresis (works even if Linux reboots).
 *   - Averages all active channels over `measurement_interval_s` (sends an immediate boot
 *     snapshot after 5 s so field technicians see live telemetry right away).
 *   - Streams validated JSON frames (schema_version = 1) to the Linux QRB2210 side over
 *     `Arduino_RouterBridge` (`Bridge.notify("measurement", json)`).
 *
 * Plug-and-Play Multi-Sensor Support:
 *   1. Internal STM32U585 MCU die temperature (`mcu_temperature_c`) via Zephyr sensor API.
 *   2. I2C (`Wire`) auto-discovery:
 *      - 0x40: INA226 reference panel (`voltage_v`, `current_a`, `power_w`)
 *      - 0x41: INA226 3S battery pack (`battery_voltage_v`, `battery_current_a`)
 *      - 0x44 / 0x45: SHT3x digital temp & humidity (`ambient_temperature_c`, `humidity_pct`)
 *      - 0x48..0x4F: Up to 8x LM75/TMP102/PCT2075 I2C heat sensors (`case_temperature_c`,
 *        `heatsink_temperature_c`, `battery_temperature_c`, + `extra_sensors.i2c_temp_0xXX_c`)
 *      - 0x5A: MLX90614 IR surface heat sensor (`surface_temperature_c`)
 *      - 0x23: BH1750 ambient light sensor (`extra_sensors.illuminance_lux`)
 *   3. 1-Wire Multi-Drop DS18B20 bus on pin D2 (up to 8 probes with ROM search + CRC8):
 *      - Probes 0..4 -> panel, ambient, case, heatsink, battery temperatures
 *      - Probes 5..7 -> `extra_sensors.ds18b20_N_c`
 *   4. Optional NTC 10k thermistors on A0..A3 (set `NTC_ENABLED_MASK` bitmask below when wired).
 */

#include <Arduino.h>
#include <Arduino_RouterBridge.h>
#include <Wire.h>
#include <math.h>
#include <stdio.h>
#include <string.h>

#if __has_include(<zephyr/drivers/sensor.h>)
#include <zephyr/device.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/sensor.h>
#define HAS_ZEPHYR_SENSOR 1
#else
#define HAS_ZEPHYR_SENSOR 0
#endif

// ---------------------------- Pin & Hardware Configuration ----------------------------
static const uint8_t PIN_ONEWIRE = 2;       // D2: 1-Wire DS18B20 multi-probe bus (4.7k pull-up to 3.3V)
static const uint8_t PIN_FAN_MOSFET = 9;    // D9: Logic-level MOSFET gate for 12 V enclosure fan

// Bitmask for analog 10k NTC thermistors on A0..A3 (0x00 = disabled until wired; e.g. 0x03 enables A0 & A1)
static uint8_t g_ntc_mask = 0x00;
static const float NTC_BETA = 3950.0f;
static const float NTC_R0 = 10000.0f;
static const float NTC_PULLUP_R = 10000.0f;

// INA226 shunt resistances (Ohms)
static const float SHUNT_REF_OHM = 0.100f;   // 100 mOhm for 0..200 mA reference cell
static const float SHUNT_BATT_OHM = 0.010f;  // 10 mOhm for 3S battery charge/load current

// Runtime configurable thresholds (updated from Linux via `set_config` RPC)
static uint32_t g_interval_s = 10;           // Fast 10s default; Linux pushes SystemConfig via set_config
static float g_fan_on_c = 40.0f;
static float g_fan_off_c = 35.0f;
static bool g_fan_state = false;
static float g_last_adc_mv[4] = {0.0f, 0.0f, 0.0f, 0.0f};

static char g_boot_id[12] = "unoq0000";
static uint32_t g_sequence = 0;
static uint32_t g_last_sample_ms = 0;
static uint32_t g_last_report_ms = 0;
static bool g_first_report_sent = false;

// ---------------------------- Running Window Accumulator ----------------------------
struct Acc {
  double sum;
  uint32_t count;
  float peak;
  void reset() { sum = 0.0; count = 0; peak = -999.0f; }
  void add(float v) {
    if (isfinite(v)) {
      sum += v;
      count++;
      if (v > peak) peak = v;
    }
  }
  bool valid() const { return count > 0; }
  float mean() const { return count > 0 ? (float)(sum / (double)count) : NAN; }
};

static Acc acc_v_ref, acc_i_ref, acc_p_ref;
static Acc acc_v_batt, acc_i_batt;
static Acc acc_t_panel, acc_t_ambient, acc_t_case, acc_t_heatsink, acc_t_battery;
static Acc acc_t_mcu, acc_t_surface, acc_humidity, acc_lux;
static Acc acc_extra_temp[8];
static char extra_temp_names[8][28];
static uint8_t extra_temp_count = 0;

static bool st_ina_ref = false;
static bool st_ina_batt = false;
static bool st_sht3x = false;
static uint8_t sht3x_addr = 0x44;
static bool st_mlx90614 = false;
static bool st_bh1750 = false;
static uint8_t lm75_addrs[8];
static uint8_t lm75_count = 0;

// ---------------------------- Minimal Inline Math & JSON Helpers ----------------------------
static float fastLn(float x) {
  if (x <= 0.0f) return 0.0f;
  int exp2 = 0;
  while (x > 1.41421356f) { x *= 0.5f; exp2++; }
  while (x < 0.70710678f) { x *= 2.0f; exp2--; }
  float y = (x - 1.0f) / (x + 1.0f);
  float y2 = y * y;
  float ln_x = 2.0f * y * (1.0f + y2 * (0.33333333f + y2 * (0.2f + y2 * 0.14285714f)));
  return ln_x + (float)exp2 * 0.69314718f;
}

static bool jsonExtractNumber(const char* json, const char* key, float* out) {
  char pattern[40];
  snprintf(pattern, sizeof(pattern), "\"%s\":", key);
  const char* pos = strstr(json, pattern);
  if (!pos) return false;
  pos += strlen(pattern);
  while (*pos == ' ' || *pos == '\t') pos++;
  bool neg = false;
  if (*pos == '-') { neg = true; pos++; }
  else if (*pos == '+') { pos++; }
  if (*pos < '0' || *pos > '9') return false;
  double val = 0.0;
  while (*pos >= '0' && *pos <= '9') {
    val = val * 10.0 + (double)(*pos - '0');
    pos++;
  }
  if (*pos == '.') {
    pos++;
    double scale = 0.1;
    while (*pos >= '0' && *pos <= '9') {
      val += (double)(*pos - '0') * scale;
      scale *= 0.1;
      pos++;
    }
  }
  *out = (float)(neg ? -val : val);
  return true;
}

// ---------------------------- I2C Register Helpers ----------------------------
static bool i2cProbe(uint8_t addr) {
  Wire.beginTransmission(addr);
  return Wire.endTransmission() == 0;
}

static bool i2cWrite16(uint8_t addr, uint8_t reg, uint16_t val) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write((uint8_t)(val >> 8));
  Wire.write((uint8_t)(val & 0xFF));
  return Wire.endTransmission() == 0;
}

static bool i2cRead16(uint8_t addr, uint8_t reg, uint16_t* out) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(addr, (uint8_t)2) != 2) return false;
  uint16_t hi = Wire.read();
  uint16_t lo = Wire.read();
  *out = (hi << 8) | lo;
  return true;
}

static bool readINA226(uint8_t addr, float shunt_ohm, float* bus_v, float* current_a) {
  uint16_t raw_bus = 0, raw_shunt = 0;
  if (!i2cRead16(addr, 0x02, &raw_bus)) return false;
  if (!i2cRead16(addr, 0x01, &raw_shunt)) return false;
  *bus_v = (float)raw_bus * 0.00125f;  // 1.25 mV/LSB
  int16_t signed_shunt = (int16_t)raw_shunt;
  float shunt_v = (float)signed_shunt * 0.0000025f;  // 2.5 uV/LSB
  *current_a = shunt_v / shunt_ohm;
  return true;
}

static bool readLM75(uint8_t addr, float* temp_c) {
  uint16_t raw = 0;
  if (!i2cRead16(addr, 0x00, &raw)) return false;
  int16_t signed_raw = (int16_t)raw;
  *temp_c = (float)(signed_raw >> 5) * 0.125f;  // 11-bit 0.125 C resolution
  return (*temp_c > -55.0f && *temp_c < 125.0f);
}

static bool readSHT3x(uint8_t addr, float* temp_c, float* rh_pct) {
  Wire.beginTransmission(addr);
  Wire.write(0x24);  // Single-shot, high repeatability, no clock stretching
  Wire.write(0x00);
  if (Wire.endTransmission() != 0) return false;
  delay(16);
  if (Wire.requestFrom(addr, (uint8_t)6) != 6) return false;
  uint16_t raw_t = ((uint16_t)Wire.read() << 8) | Wire.read();
  Wire.read();  // CRC
  uint16_t raw_h = ((uint16_t)Wire.read() << 8) | Wire.read();
  Wire.read();  // CRC
  *temp_c = -45.0f + 175.0f * ((float)raw_t / 65535.0f);
  *rh_pct = 100.0f * ((float)raw_h / 65535.0f);
  return true;
}

static bool readMLX90614(float* obj_c) {
  Wire.beginTransmission(0x5A);
  Wire.write(0x07);  // Tobj1 RAM address
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom((uint8_t)0x5A, (uint8_t)3) != 3) return false;
  uint16_t lo = Wire.read();
  uint16_t hi = Wire.read();
  Wire.read();  // PEC
  uint16_t raw = (hi << 8) | lo;
  if (raw & 0x8000) return false;  // error flag
  *obj_c = ((float)raw * 0.02f) - 273.15f;
  return (*obj_c > -70.0f && *obj_c < 380.0f);
}

static bool readBH1750(float* lux) {
  if (Wire.requestFrom((uint8_t)0x23, (uint8_t)2) != 2) return false;
  uint16_t raw = ((uint16_t)Wire.read() << 8) | Wire.read();
  *lux = (float)raw / 1.2f;
  return true;
}

// ---------------------------- 1-Wire DS18B20 Driver (Pin D2) ----------------------------
static uint8_t ds18_roms[8][8];
static uint8_t ds18_count = 0;

static bool owReset() {
  pinMode(PIN_ONEWIRE, OUTPUT);
  digitalWrite(PIN_ONEWIRE, LOW);
  delayMicroseconds(480);
  pinMode(PIN_ONEWIRE, INPUT_PULLUP);
  delayMicroseconds(70);
  bool presence = (digitalRead(PIN_ONEWIRE) == LOW);
  delayMicroseconds(410);
  return presence;
}

static void owWriteBit(uint8_t b) {
  pinMode(PIN_ONEWIRE, OUTPUT);
  digitalWrite(PIN_ONEWIRE, LOW);
  if (b) {
    delayMicroseconds(6);
    pinMode(PIN_ONEWIRE, INPUT_PULLUP);
    delayMicroseconds(64);
  } else {
    delayMicroseconds(60);
    pinMode(PIN_ONEWIRE, INPUT_PULLUP);
    delayMicroseconds(10);
  }
}

static uint8_t owReadBit() {
  pinMode(PIN_ONEWIRE, OUTPUT);
  digitalWrite(PIN_ONEWIRE, LOW);
  delayMicroseconds(3);
  pinMode(PIN_ONEWIRE, INPUT_PULLUP);
  delayMicroseconds(10);
  uint8_t r = digitalRead(PIN_ONEWIRE) ? 1 : 0;
  delayMicroseconds(50);
  return r;
}

static void owWriteByte(uint8_t v) {
  for (uint8_t i = 0; i < 8; i++) owWriteBit((v >> i) & 0x01);
}

static uint8_t owReadByte() {
  uint8_t r = 0;
  for (uint8_t i = 0; i < 8; i++) {
    if (owReadBit()) r |= (1 << i);
  }
  return r;
}

static uint8_t owCrc8(const uint8_t* data, uint8_t len) {
  uint8_t crc = 0;
  for (uint8_t i = 0; i < len; i++) {
    uint8_t inbyte = data[i];
    for (uint8_t j = 0; j < 8; j++) {
      uint8_t mix = (crc ^ inbyte) & 0x01;
      crc >>= 1;
      if (mix) crc ^= 0x8C;
      inbyte >>= 1;
    }
  }
  return crc;
}

static void discoverDS18B20() {
  ds18_count = 0;
  uint8_t rom[8] = {0};
  int last_discrepancy = 0;
  bool last_device = false;

  while (!last_device && ds18_count < 8) {
    if (!owReset()) break;
    owWriteByte(0xF0);  // SEARCH ROM
    int id_bit_number = 1;
    int last_zero = 0;
    int rom_byte_number = 0;
    uint8_t rom_byte_mask = 1;
    bool search_ok = true;

    do {
      uint8_t id_bit = owReadBit();
      uint8_t cmp_id_bit = owReadBit();
      if (id_bit == 1 && cmp_id_bit == 1) {
        search_ok = false;
        break;
      }
      uint8_t dir;
      if (id_bit != cmp_id_bit) {
        dir = id_bit;
      } else {
        if (id_bit_number < last_discrepancy) {
          dir = ((rom[rom_byte_number] & rom_byte_mask) > 0) ? 1 : 0;
        } else {
          dir = (id_bit_number == last_discrepancy) ? 1 : 0;
        }
        if (dir == 0) last_zero = id_bit_number;
      }
      if (dir == 1) rom[rom_byte_number] |= rom_byte_mask;
      else rom[rom_byte_number] &= ~rom_byte_mask;
      owWriteBit(dir);

      id_bit_number++;
      rom_byte_mask <<= 1;
      if (rom_byte_mask == 0) {
        rom_byte_number++;
        rom_byte_mask = 1;
      }
    } while (rom_byte_number < 8);

    if (!search_ok || id_bit_number <= 64) break;
    last_discrepancy = last_zero;
    if (last_discrepancy == 0) last_device = true;

    if (owCrc8(rom, 7) == rom[7] && (rom[0] == 0x28 || rom[0] == 0x10 || rom[0] == 0x22)) {
      memcpy(ds18_roms[ds18_count++], rom, 8);
    }
  }
}

static void startAllDS18B20() {
  if (ds18_count == 0) return;
  if (owReset()) {
    owWriteByte(0xCC);  // SKIP ROM
    owWriteByte(0x44);  // CONVERT T
  }
}

static bool readDS18B20(uint8_t idx, float* out_c) {
  if (idx >= ds18_count || !owReset()) return false;
  owWriteByte(0x55);  // MATCH ROM
  for (uint8_t i = 0; i < 8; i++) owWriteByte(ds18_roms[idx][i]);
  owWriteByte(0xBE);  // READ SCRATCHPAD
  uint8_t sp[9];
  for (uint8_t i = 0; i < 9; i++) sp[i] = owReadByte();
  if (owCrc8(sp, 8) != sp[8]) return false;
  int16_t raw = (int16_t)((sp[1] << 8) | sp[0]);
  float c = (float)raw / 16.0f;
  if (c == -127.0f || c == 85.0f) return false;
  *out_c = c;
  return true;
}

// ---------------------------- STM32U585 Internal Die Temperature ----------------------------
static bool readMcuDieTemp(float* out_c) {
#if HAS_ZEPHYR_SENSOR
  const struct device* dev = DEVICE_DT_GET_ANY(st_stm32_temp);
  if (dev != nullptr && device_is_ready(dev)) {
    if (sensor_sample_fetch(dev) == 0) {
      struct sensor_value val;
      if (sensor_channel_get(dev, SENSOR_CHAN_DIE_TEMP, &val) == 0) {
        *out_c = (float)sensor_value_to_double(&val);
        return (*out_c > -40.0f && *out_c < 125.0f);
      }
    }
  }
#endif
  return false;
}

// ---------------------------- Scan Buses & Reset Window ----------------------------
static void scanBuses() {
  st_ina_ref = i2cProbe(0x40);
  if (st_ina_ref) i2cWrite16(0x40, 0x00, 0x4527);  // 16x avg, continuous shunt+bus

  st_ina_batt = i2cProbe(0x41);
  if (st_ina_batt) i2cWrite16(0x41, 0x00, 0x4527);

  if (i2cProbe(0x44)) { st_sht3x = true; sht3x_addr = 0x44; }
  else if (i2cProbe(0x45)) { st_sht3x = true; sht3x_addr = 0x45; }
  else { st_sht3x = false; }

  st_mlx90614 = i2cProbe(0x5A);
  st_bh1750 = i2cProbe(0x23);
  if (st_bh1750) {
    Wire.beginTransmission(0x23);
    Wire.write(0x10);  // Continuous H-resolution mode
    Wire.endTransmission();
  }

  lm75_count = 0;
  for (uint8_t addr = 0x48; addr <= 0x4F && lm75_count < 8; addr++) {
    if (i2cProbe(addr)) lm75_addrs[lm75_count++] = addr;
  }

  discoverDS18B20();
  startAllDS18B20();
}

static void resetWindow() {
  acc_v_ref.reset(); acc_i_ref.reset(); acc_p_ref.reset();
  acc_v_batt.reset(); acc_i_batt.reset();
  acc_t_panel.reset(); acc_t_ambient.reset(); acc_t_case.reset();
  acc_t_heatsink.reset(); acc_t_battery.reset();
  acc_t_mcu.reset(); acc_t_surface.reset();
  acc_humidity.reset(); acc_lux.reset();
  for (uint8_t i = 0; i < 8; i++) acc_extra_temp[i].reset();
  extra_temp_count = 0;
}

static int getOrAddExtraSlot(const char* name) {
  for (uint8_t i = 0; i < extra_temp_count; i++) {
    if (strcmp(extra_temp_names[i], name) == 0) return i;
  }
  if (extra_temp_count >= 8) return -1;
  strncpy(extra_temp_names[extra_temp_count], name, sizeof(extra_temp_names[0]) - 1);
  extra_temp_names[extra_temp_count][sizeof(extra_temp_names[0]) - 1] = '\0';
  return extra_temp_count++;
}

// ---------------------------- Bridge RPC Handlers ----------------------------
bool onSetConfig(String jsonStr) {
  const char* s = jsonStr.c_str();
  float v = 0.0f;
  if (jsonExtractNumber(s, "measurement_interval_s", &v) && v >= 2.0f && v <= 3600.0f) {
    g_interval_s = (uint32_t)v;
  }
  if (jsonExtractNumber(s, "fan_on_c", &v) && v >= 25.0f && v <= 75.0f) {
    g_fan_on_c = v;
  }
  if (jsonExtractNumber(s, "fan_off_c", &v) && v >= 20.0f && v <= g_fan_on_c - 2.0f) {
    g_fan_off_c = v;
  }
  return true;
}

String onGetStatus() {
  char buf[160];
  snprintf(buf, sizeof(buf),
           "{\"boot_id\":\"%s\",\"seq\":%lu,\"interval_s\":%lu,\"ds18b20\":%u,\"lm75\":%u,\"fan\":%s}",
           g_boot_id, (unsigned long)g_sequence, (unsigned long)g_interval_s,
           ds18_count, lm75_count, g_fan_state ? "true" : "false");
  return String(buf);
}

// ---------------------------- Fast Sensor Sampling ----------------------------
static void sampleAllSensors() {
  float mcu_c = 0.0f;
  if (readMcuDieTemp(&mcu_c)) {
    acc_t_mcu.add(mcu_c);
  }

  if (st_ina_ref) {
    float bv = 0.0f, cur = 0.0f;
    if (readINA226(0x40, SHUNT_REF_OHM, &bv, &cur)) {
      acc_v_ref.add(bv);
      acc_i_ref.add(cur);
      acc_p_ref.add(bv * cur);
    }
  }

  if (st_ina_batt) {
    float bv = 0.0f, cur = 0.0f;
    if (readINA226(0x41, SHUNT_BATT_OHM, &bv, &cur)) {
      acc_v_batt.add(bv);
      acc_i_batt.add(cur);
    }
  }

  if (st_sht3x) {
    float tc = 0.0f, rh = 0.0f;
    if (readSHT3x(sht3x_addr, &tc, &rh)) {
      acc_t_ambient.add(tc);
      acc_humidity.add(rh);
    }
  }

  if (st_mlx90614) {
    float surf_c = 0.0f;
    if (readMLX90614(&surf_c)) acc_t_surface.add(surf_c);
  }

  if (st_bh1750) {
    float lx = 0.0f;
    if (readBH1750(&lx)) acc_lux.add(lx);
  }

  // Read previous DS18B20 conversion, then kick off next non-blocking conversion
  for (uint8_t i = 0; i < ds18_count; i++) {
    float tc = 0.0f;
    if (readDS18B20(i, &tc)) {
      if (i == 0) acc_t_panel.add(tc);
      else if (i == 1 && !st_sht3x) acc_t_ambient.add(tc);
      else if (i == 2) acc_t_case.add(tc);
      else if (i == 3) acc_t_heatsink.add(tc);
      else if (i == 4) acc_t_battery.add(tc);
      else {
        char name[24];
        snprintf(name, sizeof(name), "ds18b20_%u_c", (unsigned)(i + 1));
        int slot = getOrAddExtraSlot(name);
        if (slot >= 0) acc_extra_temp[slot].add(tc);
      }
    }
  }
  startAllDS18B20();

  // Read up to 8 I2C LM75/TMP102/PCT2075 thermal sensors
  for (uint8_t i = 0; i < lm75_count; i++) {
    float tc = 0.0f;
    if (readLM75(lm75_addrs[i], &tc)) {
      if (!acc_t_case.valid() && i == 0) acc_t_case.add(tc);
      else if (!acc_t_heatsink.valid() && i <= 1) acc_t_heatsink.add(tc);
      else if (!acc_t_battery.valid() && i <= 2) acc_t_battery.add(tc);
      else {
        char name[24];
        snprintf(name, sizeof(name), "i2c_temp_0x%02x_c", lm75_addrs[i]);
        int slot = getOrAddExtraSlot(name);
        if (slot >= 0) acc_extra_temp[slot].add(tc);
      }
    }
  }

  // Sample raw analog voltages on A0..A3 (mV) + optional NTC 10k thermistors
  const uint8_t analog_pins[4] = {A0, A1, A2, A3};
  for (uint8_t ch = 0; ch < 4; ch++) {
    int raw = analogRead(analog_pins[ch]);
    g_last_adc_mv[ch] = ((float)raw * 3300.0f) / 4095.0f;
    if (!(g_ntc_mask & (1 << ch))) continue;
    if (raw > 20 && raw < 4075) {
      float ratio = (float)raw / (4095.0f - (float)raw);
      float r_ntc = NTC_PULLUP_R * ratio;
      float inv_t = (1.0f / 298.15f) + (1.0f / NTC_BETA) * fastLn(r_ntc / NTC_R0);
      float tc = (1.0f / inv_t) - 273.15f;
      if (tc > -40.0f && tc < 125.0f) {
        char name[20];
        snprintf(name, sizeof(name), "ntc_a%u_c", ch);
        int slot = getOrAddExtraSlot(name);
        if (slot >= 0) acc_extra_temp[slot].add(tc);
      }
    }
  }

  // Autonomous local 12 V fan control (uses highest enclosure/heatsink/battery/MCU thermal signal)
  float control_temp = -999.0f;
  if (acc_t_case.valid() && acc_t_case.mean() > control_temp) control_temp = acc_t_case.mean();
  if (acc_t_heatsink.valid() && (acc_t_heatsink.mean() - 4.0f) > control_temp) {
    control_temp = acc_t_heatsink.mean() - 4.0f;
  }
  if (acc_t_battery.valid() && acc_t_battery.mean() > control_temp) control_temp = acc_t_battery.mean();
  if (control_temp < -100.0f && acc_t_mcu.valid()) control_temp = acc_t_mcu.mean() - 10.0f;

  if (control_temp > -100.0f) {
    if (control_temp >= g_fan_on_c) g_fan_state = true;
    else if (control_temp <= g_fan_off_c) g_fan_state = false;
    digitalWrite(PIN_FAN_MOSFET, g_fan_state ? HIGH : LOW);
  }
}

// ---------------------------- JSON Serialization & Bridge Notify ----------------------------
static void appendFloatField(char* buf, size_t cap, const char* key, const Acc& a, int decimals) {
  if (!a.valid()) return;
  size_t len = strlen(buf);
  snprintf(buf + len, cap - len, ",\"%s\":%.*f", key, decimals, (double)a.mean());
}

static void publishWindow(uint32_t now_ms) {
  g_sequence++;
  char json[1200];
  snprintf(json, sizeof(json),
           "{\"schema_version\":1,\"boot_id\":\"%s\",\"sequence\":%lu,\"uptime_ms\":%lu,\"fan_state\":%s",
           g_boot_id, (unsigned long)g_sequence, (unsigned long)now_ms,
           g_fan_state ? "true" : "false");

  appendFloatField(json, sizeof(json), "voltage_v", acc_v_ref, 4);
  appendFloatField(json, sizeof(json), "current_a", acc_i_ref, 5);
  appendFloatField(json, sizeof(json), "power_w", acc_p_ref, 6);
  appendFloatField(json, sizeof(json), "battery_voltage_v", acc_v_batt, 3);
  appendFloatField(json, sizeof(json), "battery_current_a", acc_i_batt, 3);
  appendFloatField(json, sizeof(json), "panel_temperature_c", acc_t_panel, 2);
  appendFloatField(json, sizeof(json), "ambient_temperature_c", acc_t_ambient, 2);
  appendFloatField(json, sizeof(json), "case_temperature_c", acc_t_case, 2);
  appendFloatField(json, sizeof(json), "heatsink_temperature_c", acc_t_heatsink, 2);
  appendFloatField(json, sizeof(json), "battery_temperature_c", acc_t_battery, 2);
  appendFloatField(json, sizeof(json), "mcu_temperature_c", acc_t_mcu, 2);
  appendFloatField(json, sizeof(json), "surface_temperature_c", acc_t_surface, 2);
  appendFloatField(json, sizeof(json), "humidity_pct", acc_humidity, 1);

  // Sensor presence map (NOT_INSTALLED keeps validator silent for unwired optional buses)
  size_t len = strlen(json);
  snprintf(json + len, sizeof(json) - len,
           ",\"sensor_status\":\"OK\",\"sensors\":{"
           "\"ina_ref\":\"%s\",\"ina_batt\":\"%s\",\"t_panel\":\"%s\","
           "\"t_ambient\":\"%s\",\"t_case\":\"%s\",\"t_heatsink\":\"%s\","
           "\"t_battery\":\"%s\",\"t_mcu\":\"%s\"}",
           acc_v_ref.valid() ? "OK" : "NOT_INSTALLED",
           acc_v_batt.valid() ? "OK" : "NOT_INSTALLED",
           acc_t_panel.valid() ? "OK" : "NOT_INSTALLED",
           acc_t_ambient.valid() ? "OK" : "NOT_INSTALLED",
           acc_t_case.valid() ? "OK" : "NOT_INSTALLED",
           acc_t_heatsink.valid() ? "OK" : "NOT_INSTALLED",
           acc_t_battery.valid() ? "OK" : "NOT_INSTALLED",
           acc_t_mcu.valid() ? "OK" : "NOT_INSTALLED");

  // Extra dynamic sensors + peak thermal diagnostics + ADC millivolts
  double elapsed_s = (double)(now_ms - g_last_report_ms) / 1000.0;
  if (elapsed_s < 1.0) elapsed_s = 1.0;
  uint8_t i2c_total = (st_ina_ref ? 1 : 0) + (st_ina_batt ? 1 : 0) + (st_sht3x ? 1 : 0)
                    + (st_mlx90614 ? 1 : 0) + (st_bh1750 ? 1 : 0) + lm75_count;
  len = strlen(json);
  snprintf(json + len, sizeof(json) - len,
           ",\"extra_sensors\":{\"window_samples_hz\":%.2f,\"adc_a0_mv\":%.1f,\"adc_a1_mv\":%.1f,"
           "\"adc_a2_mv\":%.1f,\"adc_a3_mv\":%.1f,\"i2c_devices\":%u,\"ds18b20_probes\":%u",
           (double)acc_t_mcu.count / elapsed_s,
           (double)g_last_adc_mv[0], (double)g_last_adc_mv[1],
           (double)g_last_adc_mv[2], (double)g_last_adc_mv[3],
           (unsigned)i2c_total, (unsigned)ds18_count);
  if (acc_t_mcu.valid()) {
    len = strlen(json);
    snprintf(json + len, sizeof(json) - len, ",\"mcu_peak_c\":%.2f", (double)acc_t_mcu.peak);
  }
  if (acc_t_panel.valid()) {
    len = strlen(json);
    snprintf(json + len, sizeof(json) - len, ",\"panel_peak_c\":%.2f", (double)acc_t_panel.peak);
  }
  if (acc_lux.valid()) {
    len = strlen(json);
    snprintf(json + len, sizeof(json) - len, ",\"illuminance_lux\":%.1f", (double)acc_lux.mean());
  }
  for (uint8_t i = 0; i < extra_temp_count; i++) {
    if (!acc_extra_temp[i].valid()) continue;
    len = strlen(json);
    snprintf(json + len, sizeof(json) - len, ",\"%s\":%.2f",
             extra_temp_names[i], (double)acc_extra_temp[i].mean());
  }
  len = strlen(json);
  snprintf(json + len, sizeof(json) - len, "}}");

  Bridge.notify("measurement", String(json));
  Monitor.println(json);
  resetWindow();
}

// ---------------------------- Arduino Entry Points ----------------------------
void setup() {
  pinMode(PIN_FAN_MOSFET, OUTPUT);
  digitalWrite(PIN_FAN_MOSFET, LOW);
  analogReadResolution(12);

  Wire.begin();
  Wire.setClock(100000);

  // Derive a pseudo-unique boot_id from floating ADC noise + cycle timestamp
  uint32_t entropy = ((uint32_t)analogRead(A0) << 16) ^ ((uint32_t)analogRead(A1) << 6) ^ micros();
  snprintf(g_boot_id, sizeof(g_boot_id), "uq%06lx", (unsigned long)(entropy & 0xFFFFFFUL));

  Bridge.begin();
  Monitor.begin();

  Bridge.provide_safe("set_config", onSetConfig);
  Bridge.provide_safe("get_status", onGetStatus);

  resetWindow();
  scanBuses();
  g_last_sample_ms = millis();
  g_last_report_ms = g_last_sample_ms;
}

static void publishLiveTick(uint32_t now_ms) {
  char live[560];
  snprintf(live, sizeof(live),
           "{\"schema_version\":1,\"boot_id\":\"%s\",\"sequence\":%lu,\"uptime_ms\":%lu,\"fan_state\":%s",
           g_boot_id, (unsigned long)g_sequence, (unsigned long)now_ms,
           g_fan_state ? "true" : "false");
  appendFloatField(live, sizeof(live), "voltage_v", acc_v_ref, 4);
  appendFloatField(live, sizeof(live), "current_a", acc_i_ref, 5);
  appendFloatField(live, sizeof(live), "battery_voltage_v", acc_v_batt, 3);
  appendFloatField(live, sizeof(live), "battery_current_a", acc_i_batt, 3);
  appendFloatField(live, sizeof(live), "panel_temperature_c", acc_t_panel, 2);
  appendFloatField(live, sizeof(live), "ambient_temperature_c", acc_t_ambient, 2);
  appendFloatField(live, sizeof(live), "case_temperature_c", acc_t_case, 2);
  appendFloatField(live, sizeof(live), "heatsink_temperature_c", acc_t_heatsink, 2);
  appendFloatField(live, sizeof(live), "battery_temperature_c", acc_t_battery, 2);
  appendFloatField(live, sizeof(live), "mcu_temperature_c", acc_t_mcu, 2);
  appendFloatField(live, sizeof(live), "surface_temperature_c", acc_t_surface, 2);
  appendFloatField(live, sizeof(live), "humidity_pct", acc_humidity, 1);
  size_t len = strlen(live);
  snprintf(live + len, sizeof(live) - len,
           ",\"extra_sensors\":{\"adc_a0_mv\":%.1f,\"adc_a1_mv\":%.1f,"
           "\"adc_a2_mv\":%.1f,\"adc_a3_mv\":%.1f,\"ds18b20_probes\":%u}}",
           (double)g_last_adc_mv[0], (double)g_last_adc_mv[1],
           (double)g_last_adc_mv[2], (double)g_last_adc_mv[3],
           (unsigned)ds18_count);
  Bridge.notify("live", String(live));
}

void loop() {
  uint32_t now = millis();

  if (now - g_last_sample_ms >= 2000UL) {
    g_last_sample_ms = now;
    sampleAllSensors();
    publishLiveTick(now);
  }

  uint32_t target_window_ms = g_first_report_sent ? (g_interval_s * 1000UL) : 5000UL;
  if (now - g_last_report_ms >= target_window_ms) {
    publishWindow(now);
    g_last_report_ms = now;
    g_first_report_sent = true;
    // Re-scan hot-plugged I2C / 1-Wire sensors every report cycle
    scanBuses();
  }

  delay(25);
}
