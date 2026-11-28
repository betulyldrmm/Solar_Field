// Typed client for the FastAPI backend (see docs/api.md).
// Field names mirror backend/app/models/schemas.py — units are in the suffix.

export type DataSource = "simulation" | "hardware";
export type ValueKind = "measured" | "assumed" | "calculated" | "estimated";

export interface Measurement {
  timestamp_utc: string;
  boot_id: string;
  sequence: number;
  voltage_v: number | null;
  current_a: number | null;
  power_w: number | null;
  panel_temperature_c: number | null;
  ambient_temperature_c: number | null;
  case_temperature_c: number | null;
  heatsink_temperature_c: number | null;
  battery_temperature_c: number | null;
  mcu_temperature_c: number | null;
  mpu_temperature_c: number | null;
  surface_temperature_c: number | null;
  humidity_pct: number | null;
  pressure_hpa: number | null;
  wind_speed_ms: number | null;
  irradiance_measured_wm2: number | null;
  battery_voltage_v: number | null;
  battery_current_a: number | null;
  fan_state: boolean | null;
  sensor_status: string;
  sensor_states: Record<string, string>;
  extra_sensors: Record<string, number>;
  quality_flags: string[];
  data_source: DataSource;
}

export interface BluetoothInfo {
  device_name: string;
  adapter_mac: string | null;
  pan_ip: string;
  pan_url: string;
  connected_peers: string[];
  ble_service_uuid: string;
  ble_live_char_uuid: string;
  ble_json_char_uuid: string;
}

export interface LivePoint {
  timestamp_utc: string;
  seq: number;
  uptime_s: number;
  mcu_online: boolean;
  mcu_age_s: number | null;
  cpu_pct: number;
  mem_pct: number;
  mem_used_mb: number;
  disk_free_mb: number;
  wifi_dbm: number | null;
  mpu_temperature_c: number | null;
  mcu_temperature_c: number | null;
  panel_temperature_c: number | null;
  ambient_temperature_c: number | null;
  case_temperature_c: number | null;
  heatsink_temperature_c: number | null;
  battery_temperature_c: number | null;
  surface_temperature_c: number | null;
  humidity_pct: number | null;
  pressure_hpa: number | null;
  voltage_v: number | null;
  current_a: number | null;
  power_w: number | null;
  irradiance_wm2: number | null;
  battery_voltage_v: number | null;
  battery_current_a: number | null;
  battery_soc_pct: number | null;
  fan_state: boolean | null;
  adc_mv: Record<string, number>;
  thermal_zones: Record<string, number>;
  extra_sensors: Record<string, number>;
  data_source: DataSource;
}

export interface LiveTelemetryResponse {
  source: DataSource;
  count: number;
  latest: LivePoint | null;
  series: LivePoint[];
}

export interface MeasurementPage {
  items: Measurement[];
  total: number;
  limit: number;
  offset: number;
}

export interface BatteryStatus {
  voltage_v: number | null;
  current_a: number | null;
  soc_pct_estimated: number | null;
  series_cells: number;
  note: string;
}

export interface DeviceStatus {
  device_status: "online" | "stale" | "offline" | "sleeping";
  data_mode: DataSource;
  last_measurement_utc: string | null;
  age_s: number | null;
  sensor_status: string | null;
  sensor_states: Record<string, string>;
  fan_state: boolean | null;
  battery: BatteryStatus;
  scheduled_on: boolean;
  next_shutdown_utc: string | null;
  next_wake_utc: string | null;
  bridge: string;
  measurements_total: number;
}

export interface DailyEnergy {
  date: string;
  reference_energy_wh: number;
  insolation_kwh_m2: number | null;
  insolation_kind: ValueKind;
  daylight_coverage: number;
  sample_count: number;
  gap_count: number;
  irradiance_weighted_panel_temp_c: number | null;
  complete: boolean;
  data_source: string;
}

export interface EnergySummary {
  today: DailyEnergy | null;
  days_available: number;
  complete_days: number;
  mean_insolation_kwh_m2: number | null;
  calibration_status: "calibrated" | "uncalibrated";
  data_source: string;
}

export interface SystemConfig {
  panel_power_w: number;
  panel_count: number;
  performance_ratio: number;
  temp_coefficient_pct_per_c: number;
  tilt_deg: number;
  azimuth_deg: number;
  reference_mode: "short_circuit" | "resistive_load";
  ref_isc_per_wm2_a: number;
  ref_calibrated: boolean;
  ref_isc_temp_coeff_per_c: number;
  measurement_interval_s: number;
  fan_on_c: number;
  fan_off_c: number;
  night_shutdown_enabled: boolean;
  shutdown_after_sunset_min: number;
  wake_before_sunrise_min: number;
}

export interface SystemConfigOut extends SystemConfig {
  installed_kwp: number;
  data_mode: DataSource;
}

export interface EstimateRequest {
  panel_power_w: number;
  panel_count: number;
  performance_ratio: number;
  temp_coefficient_pct_per_c: number;
  tilt_deg: number;
  azimuth_deg: number;
  method: "auto" | "measured_reference" | "theoretical_psh";
  peak_sun_hours: number | null;
  days: number;
}

export interface LabeledValue {
  value: number | null;
  unit: string;
  kind: ValueKind;
  note: string;
}

export interface EstimateResponse {
  method: "measured_reference" | "theoretical_psh";
  installed_kwp: number;
  daily_energy_kwh: number;
  per_day: Record<string, number | string>[];
  inputs: Record<string, LabeledValue>;
  calibration_status: "calibrated" | "uncalibrated" | "not_applicable";
  data_source: string;
  warnings: string[];
}

export interface DeviceEvent {
  id: number;
  timestamp_utc: string;
  level: string;
  kind: string;
  message: string;
}

export interface SensorError {
  id: number;
  timestamp_utc: string;
  sensor: string;
  error: string;
  measurement_sequence: number | null;
}

export interface EventsResponse {
  events: DeviceEvent[];
  sensor_errors: SensorError[];
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const BASE = import.meta.env.VITE_API_BASE ?? "/api";

function withSource(path: string, source?: DataSource): string {
  if (!source) return path;
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}source=${encodeURIComponent(source)}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(BASE + path, init);
  } catch {
    throw new ApiError(0, "Backend'e ulaşılamıyor. UNO Q servisi çalışıyor mu?");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail);
  }
  return (await res.json()) as T;
}

export const api = {
  bluetoothInfo: () => request<BluetoothInfo>("/bluetooth/info"),
  liveTelemetry: (source?: DataSource, limit = 120) =>
    request<LiveTelemetryResponse>(withSource(`/live/telemetry?limit=${limit}`, source)),
  deviceStatus: (source?: DataSource) => request<DeviceStatus>(withSource("/device/status", source)),
  latest: (source?: DataSource) => request<Measurement>(withSource("/measurements/latest", source)),
  history: (startIso: string, limit = 5000, source?: DataSource) =>
    request<MeasurementPage>(
      withSource(
        `/measurements/history?start=${encodeURIComponent(startIso)}&limit=${limit}&order=asc`,
        source,
      ),
    ),
  energyDaily: (days = 14, source?: DataSource) =>
    request<DailyEnergy[]>(withSource(`/energy/daily?days=${days}`, source)),
  energySummary: (source?: DataSource) => request<EnergySummary>(withSource("/energy/summary", source)),
  estimate: (body: EstimateRequest, source?: DataSource) =>
    request<EstimateResponse>(withSource("/simulation/estimate", source), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  config: (source?: DataSource) => request<SystemConfigOut>(withSource("/config", source)),
  saveConfig: (cfg: SystemConfig, token: string) =>
    request<SystemConfigOut>("/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-API-Token": token },
      body: JSON.stringify(cfg),
    }),
  events: (limit = 100) => request<EventsResponse>(`/events?limit=${limit}`),
  csvUrl: (startIso?: string, source?: DataSource) =>
    withSource(
      `${BASE}/measurements/export.csv${startIso ? `?start=${encodeURIComponent(startIso)}` : ""}`,
      source,
    ),
  jsonUrl: (startIso?: string, source?: DataSource) =>
    withSource(
      `${BASE}/measurements/export.json${startIso ? `?start=${encodeURIComponent(startIso)}` : ""}`,
      source,
    ),
};
