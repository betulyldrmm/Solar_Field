import { useMemo, useState } from "react";
import {
  CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, type Measurement, type SystemConfigOut } from "../api";
import { Card, Empty, ErrorBox, Stat } from "../components/ui";
import { age, dateTime, num, STATUS_LABEL, time } from "../format";
import { usePolling } from "../usePolling";

const RANGES = [
  { label: "6 saat", hours: 6 },
  { label: "24 saat", hours: 24 },
  { label: "3 gün", hours: 72 },
];

/** Same formula as backend energy_calculator.irradiance_wm2 (display only). */
function irradiance(m: Measurement, cfg: SystemConfigOut | null): number | null {
  if (!cfg || m.current_a === null || cfg.reference_mode !== "short_circuit") return null;
  const t = m.panel_temperature_c ?? 25;
  const denom = cfg.ref_isc_per_wm2_a * (1 + cfg.ref_isc_temp_coeff_per_c * (t - 25));
  return denom > 0 ? Math.max(0, m.current_a / denom) : null;
}

export default function Overview({ config }: { config: SystemConfigOut | null }) {
  const [hours, setHours] = useState(24);
  const status = usePolling(api.deviceStatus, 30_000);
  const latest = usePolling(api.latest, 30_000);
  const history = usePolling(
    () => api.history(new Date(Date.now() - hours * 3600_000).toISOString()),
    60_000,
    [hours],
  );

  const rows = useMemo(() => {
    // A gap (night shutdown, lost data) must show as a break, not a straight line.
    const maxGapMs = 3 * (config?.measurement_interval_s ?? 300) * 1000;
    const out: Row[] = [];
    let prev: number | null = null;
    for (const m of history.data?.items ?? []) {
      const t = new Date(m.timestamp_utc).getTime();
      if (prev !== null && t - prev > maxGapMs) {
        out.push({ t: prev + 1, irradiance: null, panel: null, ambient: null, case_: null, battery: null });
      }
      out.push({
        t,
        irradiance: irradiance(m, config),
        panel: m.panel_temperature_c,
        ambient: m.ambient_temperature_c,
        case_: m.case_temperature_c,
        battery: m.battery_voltage_v,
      });
      prev = t;
    }
    return out;
  }, [history.data, config]);

  const s = status.data;
  const m = latest.data;
  const tone = s?.device_status === "online" ? "ok"
    : s?.device_status === "sleeping" ? "muted"
    : s?.device_status === "stale" ? "warn" : "bad";
  const soc = s?.battery.soc_pct_estimated ?? null;
  const g = m ? irradiance(m, config) : null;
  const badSensors = Object.entries(s?.sensor_states ?? {}).filter(([, v]) => v !== "OK" && v !== "NOT_INSTALLED");

  return (
    <div className="stack">
      <ErrorBox message={status.error} />

      <div className="grid-stats">
        <Stat label="Cihaz" tone={tone}
          value={s ? STATUS_LABEL[s.device_status] : "…"}
          sub={s ? `Son ölçüm ${age(s.age_s)}` : undefined} />
        <Stat label="Işınım (tahmini)"
          value={num(g, 0, "W/m²")}
          sub={config?.ref_calibrated ? "kalibre edilmiş" : "kalibrasyonsuz referans panel"} />
        <Stat label="Panel sıcaklığı" value={num(m?.panel_temperature_c, 1, "°C")}
          sub={`Ortam ${num(m?.ambient_temperature_c, 1, "°C")} · Kutu ${num(m?.case_temperature_c, 1, "°C")}`} />
        <Stat label="Pil (3S)"
          tone={soc === null ? undefined : soc < 20 ? "bad" : soc < 40 ? "warn" : "ok"}
          value={soc === null ? "—" : `%${Math.round(soc)}`}
          sub={`${num(s?.battery.voltage_v, 2, "V")} · gerilimden tahmini`} />
        <Stat label="Fan (12 V)" value={s?.fan_state === null || s?.fan_state === undefined ? "—" : s.fan_state ? "Açık" : "Kapalı"}
          sub={config ? `açılış ${config.fan_on_c} °C / kapanış ${config.fan_off_c} °C` : undefined} />
        <Stat label="Gece kapanma"
          value={s?.scheduled_on ? `Kapanış ${time(s.next_shutdown_utc)}` : `Açılış ${time(s?.next_wake_utc)}`}
          sub={s?.scheduled_on ? `Sonraki açılış ${dateTime(s.next_wake_utc)}` : "Planlı uyku — hata değil"} />
      </div>

      {badSensors.length > 0 && (
        <div className="alert alert-warn">
          Sensör sorunu: {badSensors.map(([k, v]) => `${k} = ${v}`).join(", ")}
        </div>
      )}

      <Card title="Işınım" actions={
        <div className="seg">
          {RANGES.map((r) => (
            <button key={r.hours} className={r.hours === hours ? "active" : ""} onClick={() => setHours(r.hours)}>
              {r.label}
            </button>
          ))}
        </div>
      }>
        {rows.length === 0 ? (
          <Empty>{history.loading ? "Yükleniyor…" : "Bu aralıkta ölçüm yok (gece kapanma sırasında veri olmaz)."}</Empty>
        ) : (
          <TimeChart rows={rows} lines={[{ key: "irradiance", name: "Işınım (W/m²)", color: "var(--sun)" }]} />
        )}
      </Card>

      <div className="grid-2">
        <Card title="Sıcaklıklar (°C)">
          {rows.length === 0 ? <Empty>Veri yok</Empty> : (
            <TimeChart rows={rows} lines={[
              { key: "panel", name: "Panel", color: "var(--hot)" },
              { key: "ambient", name: "Ortam", color: "var(--sky)" },
              { key: "case_", name: "Kutu", color: "var(--muted-ink)" },
            ]} />
          )}
        </Card>
        <Card title="Pil gerilimi (V)">
          {rows.length === 0 ? <Empty>Veri yok</Empty> : (
            <TimeChart rows={rows} lines={[{ key: "battery", name: "3S paket", color: "var(--leaf)" }]}
              domain={[9, 12.6]} ticks={[9, 10, 11, 12, 12.6]} />
          )}
        </Card>
      </div>

      <p className="footnote">
        Veri kaynağı: <b>{s?.data_mode === "simulation" ? "SİMÜLASYON (sentetik veri)" : s?.data_mode === "hardware" ? "donanım" : "…"}</b>
        {" · "}Toplam {s?.measurements_total ?? "—"} kayıt · Ekran 30 sn'de bir yenilenir.
      </p>
    </div>
  );
}

type Row = Record<string, number | null>;

function TimeChart({ rows, lines, domain, ticks }: {
  rows: Row[];
  lines: { key: string; name: string; color: string }[];
  domain?: [number, number];
  ticks?: number[];
}) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="var(--grid)" vertical={false} />
        <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]}
          tickFormatter={(v: number) => time(new Date(v).toISOString())} stroke="var(--muted-ink)" fontSize={12} />
        <YAxis stroke="var(--muted-ink)" fontSize={12} width={48} domain={domain ?? ["auto", "auto"]} ticks={ticks} />
        <Tooltip
          labelFormatter={(v: number) => dateTime(new Date(v).toISOString())}
          formatter={(v: number) => num(v, 1)}
          contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 8 }} />
        {lines.length > 1 && <Legend />}
        {lines.map((l) => (
          <Line key={l.key} dataKey={l.key} name={l.name} stroke={l.color} dot={false}
            strokeWidth={2} connectNulls={false} isAnimationActive={false} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}
