import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Brush,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  api,
  type BluetoothInfo,
  type DataSource,
  type LiveTelemetryResponse,
  type Measurement,
  type SystemConfigOut,
} from "../api";
import { Card, Empty, ErrorBox, RadialGauge, Stat } from "../components/ui";
import { age, dateTime, num, STATUS_LABEL, time, timeSec } from "../format";
import { usePolling } from "../usePolling";

const RANGES = [
  { label: "15 dk", hours: 0.25 },
  { label: "1 saat", hours: 1 },
  { label: "6 saat", hours: 6 },
  { label: "24 saat", hours: 24 },
  { label: "3 gün", hours: 72 },
  { label: "7 gün", hours: 168 },
];

const LIVE_CHANNELS = [
  { key: "socTemp", label: "UNO Q SoC (°C)", color: "#38bdf8" },
  { key: "wlanTemp", label: "Wi-Fi/BT Isı (°C)", color: "#a78bfa" },
  { key: "panelTemp", label: "Panel (°C)", color: "#fb7185" },
  { key: "heatsinkTemp", label: "Soğutucu (°C)", color: "#f59e0b" },
  { key: "ambientTemp", label: "Ortam (°C)", color: "#10b981" },
  { key: "irradiance", label: "Işınım (W/m²)", color: "#eab308" },
  { key: "cpuPct", label: "CPU Yükü (%)", color: "#f472b6" },
  { key: "adcA0", label: "ADC A0 (V)", color: "#2dd4bf" },
] as const;

function calcIrradiance(m: Measurement, cfg: SystemConfigOut | null): number | null {
  if (m.irradiance_measured_wm2 !== null && m.irradiance_measured_wm2 !== undefined) {
    return m.irradiance_measured_wm2;
  }
  if (!cfg || m.current_a === null || cfg.reference_mode !== "short_circuit") return null;
  const t = m.panel_temperature_c ?? 25;
  const denom = cfg.ref_isc_per_wm2_a * (1 + cfg.ref_isc_temp_coeff_per_c * (t - 25));
  return denom > 0 ? Math.max(0, m.current_a / denom) : null;
}

export default function Overview({
  config,
  source,
  btInfo,
  live,
}: {
  config: SystemConfigOut | null;
  source: DataSource;
  btInfo: BluetoothInfo | null;
  live: LiveTelemetryResponse | null;
}) {
  const [hours, setHours] = useState(24);
  const [enabledLive, setEnabledLive] = useState<Record<string, boolean>>({
    socTemp: true,
    wlanTemp: true,
    panelTemp: true,
    heatsinkTemp: true,
    ambientTemp: false,
    irradiance: source === "simulation",
    cpuPct: true,
    adcA0: true,
  });

  const status = usePolling(() => api.deviceStatus(source), 5_000, [source]);
  const latest = usePolling(() => api.latest(source), 5_000, [source]);
  const history = usePolling(
    () => api.history(new Date(Date.now() - hours * 3600_000).toISOString(), 5000, source),
    8_000,
    [hours, source],
  );

  const lp = live?.latest ?? null;
  const liveSeries = useMemo(() => {
    return (live?.series ?? []).map((p) => ({
      t: new Date(p.timestamp_utc).getTime(),
      socTemp: p.mpu_temperature_c,
      wlanTemp: p.thermal_zones?.["wlan-thermal"] ?? null,
      panelTemp: p.panel_temperature_c,
      heatsinkTemp: p.heatsink_temperature_c,
      ambientTemp: p.ambient_temperature_c,
      irradiance: p.irradiance_wm2,
      cpuPct: p.cpu_pct,
      adcA0: typeof p.adc_mv?.adc_a0_mv === "number" ? Number((p.adc_mv.adc_a0_mv / 1000).toFixed(3)) : null,
    }));
  }, [live]);

  const rows = useMemo(() => {
    const maxGapMs = 3 * (config?.measurement_interval_s ?? 300) * 1000;
    const out: Record<string, number | null>[] = [];
    let prev: number | null = null;
    for (const m of history.data?.items ?? []) {
      const t = new Date(m.timestamp_utc).getTime();
      if (prev !== null && t - prev > maxGapMs) {
        out.push({
          t: prev + 1,
          irradiance: null,
          current_ma: null,
          power_mw: null,
          voltage: null,
          panel: null,
          ambient: null,
          case_: null,
          heatsink: null,
          mpu: null,
          battery_v: null,
          battery_i: null,
        });
      }
      out.push({
        t,
        irradiance: calcIrradiance(m, config),
        current_ma: m.current_a !== null ? Number((m.current_a * 1000).toFixed(2)) : null,
        power_mw: m.power_w !== null ? Number((m.power_w * 1000).toFixed(3)) : null,
        voltage: m.voltage_v,
        panel: m.panel_temperature_c,
        ambient: m.ambient_temperature_c,
        case_: m.case_temperature_c,
        heatsink: m.heatsink_temperature_c,
        mpu: m.mpu_temperature_c,
        battery_v: m.battery_voltage_v,
        battery_i: m.battery_current_a,
      });
      prev = t;
    }
    return out;
  }, [history.data, config]);

  const s = status.data;
  const m = latest.data;
  const gLive = lp?.irradiance_wm2 ?? (m ? calcIrradiance(m, config) : null);
  const mainTemp = lp?.panel_temperature_c ?? lp?.mpu_temperature_c ?? m?.mpu_temperature_c ?? null;
  const mainTempTitle =
    lp?.panel_temperature_c !== null && lp?.panel_temperature_c !== undefined
      ? "Panel Isısı"
      : "UNO Q SoC Isısı";
  const soc = lp?.battery_soc_pct ?? s?.battery.soc_pct_estimated ?? null;
  const battV = lp?.battery_voltage_v ?? s?.battery.voltage_v ?? null;
  const powerMw =
    lp?.power_w !== null && lp?.power_w !== undefined
      ? lp.power_w * 1000
      : m?.power_w !== null && m?.power_w !== undefined
        ? m.power_w * 1000
        : null;

  const tone =
    s?.device_status === "online"
      ? "ok"
      : s?.device_status === "sleeping"
        ? "muted"
        : s?.device_status === "stale"
          ? "warn"
          : "bad";

  function toggleChannel(key: string) {
    setEnabledLive((prev) => ({ ...prev, [key]: !prev[key] }));
  }

  return (
    <div className="stack">
      <ErrorBox message={status.error} />

      {/* ---- Row 1: 4 Animated Radial Arc Gauges ---- */}
      <div className="grid-gauges">
        <RadialGauge
          label="Anlık Güneş Işınımı"
          value={gLive}
          min={0}
          max={1100}
          unit="W/m²"
          digits={0}
          color="#f59e0b"
          badge={<span className="badge warn">{config?.ref_calibrated ? "Kalibreli" : "Tahmini"}</span>}
          sub={
            gLive !== null
              ? `Akım: ${num((lp?.current_a ?? m?.current_a ?? 0) * 1000, 1, "mA")}`
              : "INA226 (0x40) bekleniyor"
          }
        />

        <RadialGauge
          label={mainTempTitle}
          value={mainTemp}
          min={0}
          max={85}
          unit="°C"
          digits={1}
          color={mainTemp !== null && mainTemp > 45 ? "#fb7185" : "#38bdf8"}
          badge={<span className="badge ok">Canlı Isı</span>}
          sub={`Ortam: ${num(lp?.ambient_temperature_c ?? m?.ambient_temperature_c, 1, "°C")} · Fan: ${
            (lp?.fan_state ?? s?.fan_state) ? "AÇIK" : "KAPALI"
          }`}
        />

        <RadialGauge
          label="3S Li-Ion Pil Paketi"
          value={soc}
          min={0}
          max={100}
          unit="% SoC"
          digits={0}
          color={soc !== null && soc < 25 ? "#ef4444" : "#10b981"}
          badge={<span className="badge">{num(battV, 2, "V")}</span>}
          sub={`Akım: ${num(lp?.battery_current_a ?? m?.battery_current_a, 2, "A")} · Pil Isısı: ${num(
            lp?.battery_temperature_c ?? m?.battery_temperature_c,
            1,
            "°C",
          )}`}
        />

        <RadialGauge
          label={powerMw !== null ? "Referans Panel Gücü" : "UNO Q İşlemci Yükü"}
          value={powerMw !== null ? powerMw : (lp?.cpu_pct ?? 0)}
          min={0}
          max={powerMw !== null ? 50 : 100}
          unit={powerMw !== null ? "mW" : "% CPU"}
          digits={1}
          color="#a78bfa"
          badge={<span className="badge kind-assumed">{source === "hardware" ? "MCU+MPU" : "Sim"}</span>}
          sub={
            powerMw !== null
              ? `Gerilim: ${num((lp?.voltage_v ?? 0) * 1000, 1, "mV")}`
              : `RAM: %${num(lp?.mem_pct, 0)} (${num(lp?.mem_used_mb, 0, "MB")})`
          }
        />
      </div>

      {/* ---- Row 2: 6 KPI Cards with Embedded Sparklines ---- */}
      <div className="grid-stats">
        <Stat
          label="İstasyon Durumu"
          tone={tone}
          value={s ? STATUS_LABEL[s.device_status] : "…"}
          sub={s ? `Son DB kaydı ${age(s.age_s)} · Toplam ${s.measurements_total}` : undefined}
        />
        <Stat
          label="SoC Çekirdek Isısı"
          value={num(lp?.mpu_temperature_c, 2, "°C")}
          spark={liveSeries.slice(-30).map((r) => r.socTemp)}
          color="#38bdf8"
          sub={`Wi-Fi/BT modülü: ${num(lp?.thermal_zones?.["wlan-thermal"], 1, "°C")}`}
        />
        <Stat
          label="ADC A0 Girişi (Canlı)"
          value={
            typeof lp?.adc_mv?.adc_a0_mv === "number"
              ? num(lp.adc_mv.adc_a0_mv, 0, "mV")
              : "—"
          }
          spark={liveSeries.slice(-30).map((r) => r.adcA0)}
          color="#2dd4bf"
          sub={`A1: ${num(lp?.adc_mv?.adc_a1_mv, 0, "mV")} · A2: ${num(lp?.adc_mv?.adc_a2_mv, 0, "mV")}`}
        />
        <Stat
          label="Nem & Basınç"
          value={num(lp?.humidity_pct ?? m?.humidity_pct, 1, "%")}
          spark={rows.slice(-30).map((r) => r.panel)}
          color="#10b981"
          sub={`Basınç: ${num(lp?.pressure_hpa ?? m?.pressure_hpa, 1, "hPa")}`}
        />
        <Stat
          label="Bluetooth Ağ (PAN)"
          tone="ok"
          value={btInfo?.connected_peers.length ? `${btInfo.connected_peers.length} Cihaz` : "Hazır"}
          sub={`Adres: http://${btInfo?.pan_ip ?? "10.44.0.1"}/`}
        />
        <Stat
          label="Gece Uyku Takvimi"
          value={s?.scheduled_on ? `Kapanış ${time(s.next_shutdown_utc)}` : `Açılış ${time(s?.next_wake_utc)}`}
          sub={s?.scheduled_on ? `Açılış: ${dateTime(s.next_wake_utc)}` : "Planlı gece uykusu"}
        />
      </div>

      {/* ---- Row 3: High-Speed 1.5s Real-Time Oscilloscope ---- */}
      <Card
        title="Canlı Çok Kanallı Osiloskop (1.5 sn Gerçek Zamanlı Akış)"
        subtitle="STM32U585 MCU + Qualcomm QRB2210 sensörlerinden gelen canlı dalga formu (veritabanını şişirmeden bellekte akar)"
      >
        <div className="pill-bar">
          {LIVE_CHANNELS.map((ch) => (
            <button
              key={ch.key}
              type="button"
              className={`channel-pill ${enabledLive[ch.key] ? "on" : ""}`}
              style={{ color: enabledLive[ch.key] ? ch.color : undefined }}
              onClick={() => toggleChannel(ch.key)}
            >
              <span className="channel-swatch" style={{ background: ch.color }} />
              {ch.label}
            </button>
          ))}
        </div>

        {liveSeries.length === 0 ? (
          <Empty>Canlı akış başlatılıyor…</Empty>
        ) : (
          <ResponsiveContainer width="100%" height={290}>
            <AreaChart data={liveSeries} margin={{ top: 10, right: 16, left: 0, bottom: 0 }}>
              <defs>
                {LIVE_CHANNELS.map((ch) => (
                  <linearGradient key={ch.key} id={`grad_${ch.key}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={ch.color} stopOpacity={0.28} />
                    <stop offset="95%" stopColor={ch.color} stopOpacity={0.0} />
                  </linearGradient>
                ))}
              </defs>
              <CartesianGrid stroke="var(--grid)" strokeDasharray="3 3" vertical={false} />
              <XAxis
                dataKey="t"
                type="number"
                scale="time"
                domain={["dataMin", "dataMax"]}
                tickFormatter={(v: number) => timeSec(new Date(v).toISOString())}
                stroke="var(--muted-ink)"
                fontSize={11}
              />
              <YAxis stroke="var(--muted-ink)" fontSize={11} width={46} domain={["auto", "auto"]} />
              <Tooltip
                labelFormatter={(v: number) => timeSec(new Date(v).toISOString())}
                formatter={(v: number) => num(v, 2)}
                contentStyle={{
                  background: "var(--panel)",
                  border: "1px solid var(--line)",
                  borderRadius: 10,
                }}
              />
              <Legend />
              {LIVE_CHANNELS.filter((ch) => enabledLive[ch.key]).map((ch) => (
                <Area
                  key={ch.key}
                  type="monotone"
                  dataKey={ch.key}
                  name={ch.label}
                  stroke={ch.color}
                  fill={`url(#grad_${ch.key})`}
                  strokeWidth={2.2}
                  dot={false}
                  isAnimationActive={false}
                  connectNulls
                />
              ))}
            </AreaChart>
          </ResponsiveContainer>
        )}
      </Card>

      {/* ---- Row 4: Historical Time-Range Selector + Zoomable Solar/Current Chart ---- */}
      <Card
        title="Tarihsel Işınım & Referans Akım Grafiği (Yakınlaştırma Kaydırıcılı)"
        subtitle="SQLite veritabanına kaydedilen doğrulanmış ölçümler — alttaki çubuktan istediğiniz saat aralığına yakınlaşın"
        actions={
          <div className="seg">
            {RANGES.map((r) => (
              <button
                key={r.label}
                type="button"
                className={r.hours === hours ? "active" : ""}
                onClick={() => setHours(r.hours)}
              >
                {r.label}
              </button>
            ))}
          </div>
        }
      >
        {rows.length === 0 ? (
          <Empty>
            {history.loading
              ? "Yükleniyor…"
              : "Seçilen aralıkta kayıtlı ölçüm yok (Simülasyon sekmesinden 6 günlük örnek veriyi inceleyebilirsiniz)."}
          </Empty>
        ) : (
          <ResponsiveContainer width="100%" height={310}>
            <ComposedChart data={rows} margin={{ top: 10, right: 16, left: 0, bottom: 4 }}>
              <defs>
                <linearGradient id="irrGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.4} />
                  <stop offset="95%" stopColor="#f59e0b" stopOpacity={0.0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis
                dataKey="t"
                type="number"
                scale="time"
                domain={["dataMin", "dataMax"]}
                tickFormatter={(v: number) => time(new Date(v).toISOString())}
                stroke="var(--muted-ink)"
                fontSize={11}
              />
              <YAxis yAxisId="left" stroke="#f59e0b" fontSize={11} width={48} />
              <YAxis yAxisId="right" orientation="right" stroke="#38bdf8" fontSize={11} width={48} />
              <Tooltip
                labelFormatter={(v: number) => dateTime(new Date(v).toISOString())}
                formatter={(v: number) => num(v, 2)}
                contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 10 }}
              />
              <Legend />
              <Area
                yAxisId="left"
                type="monotone"
                dataKey="irradiance"
                name="Işınım (W/m²)"
                stroke="#f59e0b"
                fill="url(#irrGrad)"
                strokeWidth={2}
                connectNulls={false}
                isAnimationActive={false}
              />
              <Line
                yAxisId="right"
                type="monotone"
                dataKey="current_ma"
                name="Kısa Devre Akımı (mA)"
                stroke="#38bdf8"
                dot={false}
                strokeWidth={2}
                connectNulls={false}
                isAnimationActive={false}
              />
              {rows.length > 15 && (
                <Brush
                  dataKey="t"
                  height={24}
                  stroke="var(--sun)"
                  fill="var(--panel-elev)"
                  tickFormatter={(v: number) => time(new Date(v).toISOString())}
                />
              )}
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </Card>

      {/* ---- Row 5: Dual Historical Thermal + Battery/Power Charts ---- */}
      <div className="grid-2">
        <Card
          title="Kayıtlı Sıcaklık Geçmişi (°C)"
          subtitle="Panel, ortam, kutu, soğutucu ve işlemci sıcaklık eğrileri + fan eşiği"
        >
          {rows.length === 0 ? (
            <Empty>Veri yok</Empty>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <LineChart data={rows} margin={{ top: 8, right: 14, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis
                  dataKey="t"
                  type="number"
                  scale="time"
                  domain={["dataMin", "dataMax"]}
                  tickFormatter={(v: number) => time(new Date(v).toISOString())}
                  stroke="var(--muted-ink)"
                  fontSize={11}
                />
                <YAxis stroke="var(--muted-ink)" fontSize={11} width={44} />
                <Tooltip
                  labelFormatter={(v: number) => dateTime(new Date(v).toISOString())}
                  formatter={(v: number) => num(v, 1, "°C")}
                  contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 10 }}
                />
                <Legend />
                {config && (
                  <ReferenceLine
                    y={config.fan_on_c}
                    stroke="#ef4444"
                    strokeDasharray="4 4"
                    label={{ value: `Fan ${config.fan_on_c}°C`, fill: "#ef4444", fontSize: 10 }}
                  />
                )}
                <Line dataKey="panel" name="Panel" stroke="#fb7185" dot={false} strokeWidth={2} connectNulls={false} isAnimationActive={false} />
                <Line dataKey="heatsink" name="Soğutucu" stroke="#f59e0b" dot={false} strokeWidth={2} connectNulls={false} isAnimationActive={false} />
                <Line dataKey="case_" name="Kutu" stroke="#a78bfa" dot={false} strokeWidth={2} connectNulls={false} isAnimationActive={false} />
                <Line dataKey="ambient" name="Ortam" stroke="#10b981" dot={false} strokeWidth={2} connectNulls={false} isAnimationActive={false} />
                <Line dataKey="mpu" name="UNO Q SoC" stroke="#38bdf8" dot={false} strokeWidth={2} connectNulls={false} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card
          title="3S Pil Gerilimi (V) & Akımı (A)"
          subtitle="Şarj / deşarj dengesi ve 9.0–12.6 V çalışma bandı"
        >
          {rows.length === 0 ? (
            <Empty>Veri yok</Empty>
          ) : (
            <ResponsiveContainer width="100%" height={260}>
              <ComposedChart data={rows} margin={{ top: 8, right: 14, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis
                  dataKey="t"
                  type="number"
                  scale="time"
                  domain={["dataMin", "dataMax"]}
                  tickFormatter={(v: number) => time(new Date(v).toISOString())}
                  stroke="var(--muted-ink)"
                  fontSize={11}
                />
                <YAxis yAxisId="v" domain={[9, 12.8]} stroke="#10b981" fontSize={11} width={42} />
                <YAxis yAxisId="i" orientation="right" stroke="#f59e0b" fontSize={11} width={42} />
                <Tooltip
                  labelFormatter={(v: number) => dateTime(new Date(v).toISOString())}
                  formatter={(v: number) => num(v, 2)}
                  contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 10 }}
                />
                <Legend />
                <Line
                  yAxisId="v"
                  dataKey="battery_v"
                  name="Pil Gerilimi (V)"
                  stroke="#10b981"
                  dot={false}
                  strokeWidth={2.2}
                  connectNulls={false}
                  isAnimationActive={false}
                />
                <Line
                  yAxisId="i"
                  dataKey="battery_i"
                  name="Pil Akımı (A)"
                  stroke="#f59e0b"
                  dot={false}
                  strokeWidth={1.8}
                  connectNulls={false}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>
    </div>
  );
}
