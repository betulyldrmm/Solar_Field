import { useMemo } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
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
  type DataSource,
  type LiveTelemetryResponse,
  type SystemConfigOut,
} from "../api";
import { Card, Empty, ThermalTile } from "../components/ui";
import { num, timeSec } from "../format";
import { usePolling } from "../usePolling";

const ZONE_PALETTE = [
  "#fb7185",
  "#f59e0b",
  "#38bdf8",
  "#10b981",
  "#a78bfa",
  "#f472b6",
  "#2dd4bf",
  "#eab308",
];

const ZONE_FRIENDLY: Record<string, string> = {
  "cpuss0-thermal": "QRB2210 CPU Kümesi 0",
  "cpuss1-thermal": "QRB2210 CPU Kümesi 1",
  "gpu-thermal": "Adreno GPU Çekirdek",
  "wlan-thermal": "WCBN3536A Wi-Fi / BT",
  "hm-center-thermal": "SoC Merkez Isı",
  "camera-thermal": "ISP / Kamera Bölgesi",
  "video-thermal": "Video Kodek Bloğu",
  "mdm0-thermal": "Modem / RF Bölgesi 0",
  "mdm1-thermal": "Modem / RF Bölgesi 1",
  "mapss-thermal": "Güç Yönetimi (MAPSS)",
};

export default function ThermalLab({
  config,
  source,
  live,
}: {
  config: SystemConfigOut | null;
  source: DataSource;
  live: LiveTelemetryResponse | null;
}) {
  const latestDb = usePolling(() => api.latest(source), 8_000, [source]);
  const lp = live?.latest ?? null;
  const series = live?.series ?? [];
  const m = latestDb.data;

  // Compute session peak per thermal channel across the live ring buffer
  const peaks = useMemo(() => {
    const out: Record<string, number> = {};
    const bump = (k: string, v: number | null | undefined) => {
      if (typeof v === "number" && Number.isFinite(v)) {
        out[k] = out[k] === undefined ? v : Math.max(out[k], v);
      }
    };
    for (const p of series) {
      bump("panel", p.panel_temperature_c);
      bump("ambient", p.ambient_temperature_c);
      bump("case", p.case_temperature_c);
      bump("heatsink", p.heatsink_temperature_c);
      bump("battery", p.battery_temperature_c);
      bump("mcu", p.mcu_temperature_c);
      bump("mpu", p.mpu_temperature_c);
      bump("surface", p.surface_temperature_c);
      for (const [zk, zv] of Object.entries(p.thermal_zones ?? {})) bump(`z:${zk}`, zv);
      for (const [ek, ev] of Object.entries(p.extra_sensors ?? {})) bump(`e:${ek}`, ev);
    }
    return out;
  }, [series]);

  const mcuTiles = useMemo(() => {
    const base = [
      { name: "Referans Panel (DS18B20 #1)", code: "t_panel", val: lp?.panel_temperature_c ?? m?.panel_temperature_c, peak: peaks.panel },
      { name: "Ortam Havası (SHT3x / #2)", code: "t_ambient", val: lp?.ambient_temperature_c ?? m?.ambient_temperature_c, peak: peaks.ambient },
      { name: "Pano / Kutu İçi (#3 / LM75)", code: "t_case", val: lp?.case_temperature_c ?? m?.case_temperature_c, peak: peaks.case },
      { name: "MOSFET / Buck Soğutucu", code: "t_heatsink", val: lp?.heatsink_temperature_c ?? m?.heatsink_temperature_c, peak: peaks.heatsink },
      { name: "3S Li-Ion Pil Paketi", code: "t_battery", val: lp?.battery_temperature_c ?? m?.battery_temperature_c, peak: peaks.battery },
      { name: "STM32U585 MCU Çekirdek", code: "t_mcu", val: lp?.mcu_temperature_c ?? m?.mcu_temperature_c, peak: peaks.mcu },
      { name: "Yüzey / Kızılötesi (MLX90614)", code: "t_surface", val: lp?.surface_temperature_c ?? m?.surface_temperature_c, peak: peaks.surface },
    ];
    const extraObj = { ...(m?.extra_sensors ?? {}), ...(lp?.extra_sensors ?? {}) };
    for (const [k, v] of Object.entries(extraObj)) {
      if (k.endsWith("_c") && !k.startsWith("soc_")) {
        base.push({
          name: k.replace(/_c$/, "").replace(/_/g, " ").toUpperCase(),
          code: `extra.${k}`,
          val: v,
          peak: peaks[`e:${k}`],
        });
      }
    }
    return base;
  }, [lp, m, peaks]);

  const socZones = useMemo(() => {
    const z = lp?.thermal_zones ?? {};
    return Object.entries(z).map(([k, v]) => ({
      key: k,
      name: ZONE_FRIENDLY[k] ?? k,
      val: v,
      peak: peaks[`z:${k}`],
    }));
  }, [lp, peaks]);

  const zoneKeys = useMemo(() => {
    const keys = Object.keys(lp?.thermal_zones ?? {});
    return keys.slice(0, 6);
  }, [lp]);

  const multiTempRows = useMemo(() => {
    return series.map((p) => {
      const row: Record<string, number | null> = {
        t: new Date(p.timestamp_utc).getTime(),
        panel: p.panel_temperature_c,
        ambient: p.ambient_temperature_c,
        heatsink: p.heatsink_temperature_c,
        case_: p.case_temperature_c,
        mcu: p.mcu_temperature_c,
      };
      for (const zk of zoneKeys) {
        row[zk] = p.thermal_zones?.[zk] ?? null;
      }
      const amb = p.ambient_temperature_c ?? 22.0;
      row.deltaPanelAmb =
        p.panel_temperature_c !== null ? Number((p.panel_temperature_c - amb).toFixed(2)) : null;
      row.deltaSocAmb =
        p.mpu_temperature_c !== null ? Number((p.mpu_temperature_c - amb).toFixed(2)) : null;
      row.adc0 = p.adc_mv?.adc_a0_mv ?? null;
      row.adc1 = p.adc_mv?.adc_a1_mv ?? null;
      row.adc2 = p.adc_mv?.adc_a2_mv ?? null;
      row.adc3 = p.adc_mv?.adc_a3_mv ?? null;
      return row;
    });
  }, [series, zoneKeys]);

  const adcBars = useMemo(() => {
    const a = lp?.adc_mv ?? {};
    return [
      { pin: "A0", mv: a.adc_a0_mv ?? 0, color: "#38bdf8" },
      { pin: "A1", mv: a.adc_a1_mv ?? 0, color: "#f59e0b" },
      { pin: "A2", mv: a.adc_a2_mv ?? 0, color: "#10b981" },
      { pin: "A3", mv: a.adc_a3_mv ?? 0, color: "#a78bfa" },
    ];
  }, [lp]);

  return (
    <div className="stack">
      {/* ---- Section 1: MCU External & Bus Thermal Matrix ---- */}
      <Card
        title="STM32U585 MCU Termal & Harici Prob Matrisi"
        subtitle="1-Wire DS18B20 (8'e kadar prob), I²C LM75/TMP102 (0x48..0x4F), SHT3x, MLX90614 ve NTC kanalları — takıldığı anda otomatik aktifleşir"
      >
        <div className="thermal-grid">
          {mcuTiles.map((item) => (
            <ThermalTile
              key={item.code}
              name={item.name}
              code={item.code}
              tempC={item.val}
              peakC={item.peak}
            />
          ))}
        </div>
      </Card>

      {/* ---- Section 2: Qualcomm QRB2210 Live On-Board 10-Zone Thermal Heatmap ---- */}
      <Card
        title={`UNO Q İşlemci & Kablosuz Modül Canlı Isı Bölgeleri (${socZones.length} Aktif Sensör)`}
        subtitle="Kart üzerindeki gerçek donanım termistörlerinden her 1.5 saniyede bir okunan anlık sıcaklıklar"
      >
        {socZones.length === 0 ? (
          <Empty>Termal bölge verisi bekleniyor…</Empty>
        ) : (
          <div className="thermal-grid">
            {socZones.map((z) => (
              <ThermalTile
                key={z.key}
                name={z.name}
                code={z.key}
                tempC={z.val}
                peakC={z.peak}
                minC={20}
                maxC={75}
              />
            ))}
          </div>
        )}
      </Card>

      {/* ---- Section 3: Live Multi-Zone Thermal Curves + Delta-T Chart ---- */}
      <div className="grid-2">
        <Card
          title="Canlı Çok Bölgeli Sıcaklık Eğrileri (°C)"
          subtitle="MCU ve SoC ısı sensörlerinin eşzamanlı karşılaştırması"
        >
          {multiTempRows.length === 0 ? (
            <Empty>Veri bekleniyor…</Empty>
          ) : (
            <ResponsiveContainer width="100%" height={280}>
              <LineChart data={multiTempRows} margin={{ top: 8, right: 14, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis
                  dataKey="t"
                  type="number"
                  scale="time"
                  domain={["dataMin", "dataMax"]}
                  tickFormatter={(v: number) => timeSec(new Date(v).toISOString())}
                  stroke="var(--muted-ink)"
                  fontSize={11}
                />
                <YAxis stroke="var(--muted-ink)" fontSize={11} width={44} domain={["auto", "auto"]} />
                <Tooltip
                  labelFormatter={(v: number) => timeSec(new Date(v).toISOString())}
                  formatter={(v: number) => num(v, 2, "°C")}
                  contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 10 }}
                />
                <Legend />
                {config && (
                  <ReferenceLine y={config.fan_on_c} stroke="#ef4444" strokeDasharray="4 4" />
                )}
                <Line dataKey="panel" name="Panel" stroke="#fb7185" dot={false} strokeWidth={2.2} isAnimationActive={false} />
                <Line dataKey="heatsink" name="Soğutucu" stroke="#f59e0b" dot={false} strokeWidth={2} isAnimationActive={false} />
                <Line dataKey="ambient" name="Ortam" stroke="#10b981" dot={false} strokeWidth={2} isAnimationActive={false} />
                {zoneKeys.map((zk, idx) => (
                  <Line
                    key={zk}
                    dataKey={zk}
                    name={ZONE_FRIENDLY[zk] ?? zk}
                    stroke={ZONE_PALETTE[(idx + 2) % ZONE_PALETTE.length]}
                    dot={false}
                    strokeWidth={1.8}
                    isAnimationActive={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card
          title="ΔT Termal Yükselme Analizi (°C Ortam Üzeri)"
          subtitle="Güneş yükü ve elektronik ısınmanın ortam sıcaklığına göre farkı"
        >
          {multiTempRows.length === 0 ? (
            <Empty>Veri bekleniyor…</Empty>
          ) : (
            <ResponsiveContainer width="100%" height={280}>
              <AreaChart data={multiTempRows} margin={{ top: 8, right: 14, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="dtPanel" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#fb7185" stopOpacity={0.35} />
                    <stop offset="95%" stopColor="#fb7185" stopOpacity={0.0} />
                  </linearGradient>
                  <linearGradient id="dtSoc" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#38bdf8" stopOpacity={0.35} />
                    <stop offset="95%" stopColor="#38bdf8" stopOpacity={0.0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis
                  dataKey="t"
                  type="number"
                  scale="time"
                  domain={["dataMin", "dataMax"]}
                  tickFormatter={(v: number) => timeSec(new Date(v).toISOString())}
                  stroke="var(--muted-ink)"
                  fontSize={11}
                />
                <YAxis stroke="var(--muted-ink)" fontSize={11} width={44} />
                <Tooltip
                  labelFormatter={(v: number) => timeSec(new Date(v).toISOString())}
                  formatter={(v: number) => num(v, 2, "°C")}
                  contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 10 }}
                />
                <Legend />
                <Area
                  type="monotone"
                  dataKey="deltaPanelAmb"
                  name="ΔT (Panel − Ortam)"
                  stroke="#fb7185"
                  fill="url(#dtPanel)"
                  strokeWidth={2}
                  isAnimationActive={false}
                />
                <Area
                  type="monotone"
                  dataKey="deltaSocAmb"
                  name="ΔT (SoC − 22°C Ref)"
                  stroke="#38bdf8"
                  fill="url(#dtSoc)"
                  strokeWidth={2}
                  isAnimationActive={false}
                />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>

      {/* ---- Section 4: STM32U585 Analog Pin (A0..A3) Live Voltage Meter & Waveform ---- */}
      <div className="grid-2">
        <Card
          title="STM32U585 Analog Giriş Seviyeleri (A0–A3 mV)"
          subtitle="12-bit ADC (0–3300 mV) anlık gerilim çubukları — NTC termistör veya analog sensör taktığınızda canlı değişir"
        >
          <ResponsiveContainer width="100%" height={230}>
            <BarChart data={adcBars} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="pin" stroke="var(--muted-ink)" fontSize={12} />
              <YAxis domain={[0, 3300]} stroke="var(--muted-ink)" fontSize={11} width={48} />
              <Tooltip
                formatter={(v: number) => num(v, 1, "mV")}
                contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 10 }}
              />
              <Bar dataKey="mv" name="Gerilim (mV)" radius={[6, 6, 0, 0]} isAnimationActive={false}>
                {adcBars.map((b) => (
                  <Cell key={b.pin} fill={b.color} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>

        <Card
          title="STM32U585 Analog A0–A3 Osiloskop İzi (mV)"
          subtitle="Son dakikalardaki 4 kanallı ham ADC dalga formu"
        >
          {multiTempRows.length === 0 ? (
            <Empty>ADC akışı bekleniyor…</Empty>
          ) : (
            <ResponsiveContainer width="100%" height={230}>
              <LineChart data={multiTempRows} margin={{ top: 8, right: 14, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis
                  dataKey="t"
                  type="number"
                  scale="time"
                  domain={["dataMin", "dataMax"]}
                  tickFormatter={(v: number) => timeSec(new Date(v).toISOString())}
                  stroke="var(--muted-ink)"
                  fontSize={11}
                />
                <YAxis domain={[0, 3300]} stroke="var(--muted-ink)" fontSize={11} width={48} />
                <Tooltip
                  labelFormatter={(v: number) => timeSec(new Date(v).toISOString())}
                  formatter={(v: number) => num(v, 1, "mV")}
                  contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 10 }}
                />
                <Legend />
                <Line dataKey="adc0" name="A0 (mV)" stroke="#38bdf8" dot={false} strokeWidth={2} isAnimationActive={false} />
                <Line dataKey="adc1" name="A1 (mV)" stroke="#f59e0b" dot={false} strokeWidth={2} isAnimationActive={false} />
                <Line dataKey="adc2" name="A2 (mV)" stroke="#10b981" dot={false} strokeWidth={2} isAnimationActive={false} />
                <Line dataKey="adc3" name="A3 (mV)" stroke="#a78bfa" dot={false} strokeWidth={2} isAnimationActive={false} />
              </LineChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>
    </div>
  );
}
