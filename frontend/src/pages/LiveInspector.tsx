import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  api,
  type BluetoothInfo,
  type DataSource,
  type LivePoint,
  type LiveTelemetryResponse,
  type Measurement,
  type SystemConfigOut,
} from "../api";
import { Card, Empty } from "../components/ui";
import { num, timeSec } from "../format";
import { usePolling } from "../usePolling";

interface BleChar {
  readValue(): Promise<DataView>;
  startNotifications(): Promise<BleChar>;
  addEventListener(type: "characteristicvaluechanged", fn: (ev: Event) => void): void;
}
interface BleService {
  getCharacteristic(uuid: string): Promise<BleChar>;
}
interface BleDevice {
  name?: string;
  gatt?: { connect(): Promise<{ getPrimaryService(uuid: string): Promise<BleService> }> };
}
interface NavWithBluetooth extends Navigator {
  bluetooth?: {
    requestDevice(opts: {
      filters?: { name?: string; services?: string[] }[];
      optionalServices?: string[];
    }): Promise<BleDevice>;
  };
}

function calcIrr(m: Measurement, cfg: SystemConfigOut | null): number | null {
  if (m.irradiance_measured_wm2 !== null) return m.irradiance_measured_wm2;
  if (!cfg || m.current_a === null || cfg.reference_mode !== "short_circuit") return null;
  const t = m.panel_temperature_c ?? 25;
  const denom = cfg.ref_isc_per_wm2_a * (1 + cfg.ref_isc_temp_coeff_per_c * (t - 25));
  return denom > 0 ? Math.max(0, m.current_a / denom) : null;
}

export default function LiveInspector({
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
  const [selectedFrame, setSelectedFrame] = useState<LivePoint | null>(null);
  const [bleStatus, setBleStatus] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const hist = usePolling(
    () => api.history(new Date(Date.now() - 72 * 3600_000).toISOString(), 1500, source),
    20_000,
    [source],
  );

  const series = live?.series ?? [];
  const activeFrame = selectedFrame ?? live?.latest ?? null;

  const scatterPoints = useMemo(() => {
    const pts: { g: number; temp: number }[] = [];
    for (const m of hist.data?.items ?? []) {
      const g = calcIrr(m, config);
      const temp = m.panel_temperature_c ?? m.mpu_temperature_c;
      if (g !== null && temp !== null && g > 5) {
        pts.push({ g: Number(g.toFixed(1)), temp: Number(temp.toFixed(2)) });
      }
    }
    if (pts.length === 0) {
      for (const p of series) {
        if (p.irradiance_wm2 !== null && p.panel_temperature_c !== null) {
          pts.push({ g: p.irradiance_wm2, temp: p.panel_temperature_c });
        }
      }
    }
    return pts;
  }, [hist.data, config, series]);

  const sysRows = useMemo(() => {
    return series.map((p) => ({
      t: new Date(p.timestamp_utc).getTime(),
      cpu: p.cpu_pct,
      ram: p.mem_pct,
      soc: p.mpu_temperature_c,
    }));
  }, [series]);

  function downloadLiveBufferJson() {
    const blob = new Blob([JSON.stringify(series, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `uno_q_live_stream_${source}_${new Date().toISOString().slice(0, 19)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  async function copyFrameJson() {
    if (!activeFrame) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(activeFrame, null, 2));
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard blocked on plain HTTP */
    }
  }

  async function connectBleDirect() {
    const nav = navigator as NavWithBluetooth;
    if (!nav.bluetooth) {
      setBleStatus("HTTP üzerinden Web Bluetooth kısıtlı — zaten Bluetooth PAN ile doğrudan bağlısınız.");
      return;
    }
    const svcUuid = btInfo?.ble_service_uuid ?? "5f1d0001-3a6b-4c2e-9d6e-5f0a1c7b2e10";
    const jsonUuid = btInfo?.ble_json_char_uuid ?? "5f1d0003-3a6b-4c2e-9d6e-5f0a1c7b2e10";
    try {
      const dev = await nav.bluetooth.requestDevice({
        filters: [{ name: "SolarFieldStation" }, { services: [svcUuid] }],
        optionalServices: [svcUuid],
      });
      if (!dev.gatt) return;
      const gatt = await dev.gatt.connect();
      const svc = await gatt.getPrimaryService(svcUuid);
      const ch = await svc.getCharacteristic(jsonUuid);
      const dv = await ch.readValue();
      const txt = new TextDecoder().decode(dv.buffer);
      setBleStatus(`BLE GATT Okundu: ${txt}`);
    } catch (e) {
      setBleStatus(`BLE: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  return (
    <div className="stack">
      <div className="grid-2">
        {/* ---- Scatter Correlation Chart: Irradiance vs Panel Temperature ---- */}
        <Card
          title="Işınım – Sıcaklık Korelasyonu (NOCT Dağılım Grafiği)"
          subtitle="Güneş ışınımı arttıkça hücre sıcaklığının yükselişi ve verim kaybı eğilimi"
        >
          {scatterPoints.length === 0 ? (
            <Empty>Korelasyon grafiği için ışınım verisi bekleniyor (Simülasyon modunda deneyebilirsiniz).</Empty>
          ) : (
            <ResponsiveContainer width="100%" height={270}>
              <ScatterChart margin={{ top: 10, right: 16, left: 0, bottom: 8 }}>
                <CartesianGrid stroke="var(--grid)" />
                <XAxis
                  type="number"
                  dataKey="g"
                  name="Işınım"
                  unit=" W/m²"
                  stroke="var(--muted-ink)"
                  fontSize={11}
                />
                <YAxis
                  type="number"
                  dataKey="temp"
                  name="Sıcaklık"
                  unit=" °C"
                  stroke="var(--muted-ink)"
                  fontSize={11}
                  width={46}
                />
                <Tooltip
                  cursor={{ strokeDasharray: "3 3" }}
                  contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 10 }}
                />
                <Scatter name="Ölçüm Noktası" data={scatterPoints} fill="#f59e0b" fillOpacity={0.75} />
              </ScatterChart>
            </ResponsiveContainer>
          )}
        </Card>

        {/* ---- UNO Q Edge Computer CPU / RAM / SoC Telemetry ---- */}
        <Card
          title="UNO Q Linux Sistem Performansı (CPU %, RAM %, SoC °C)"
          subtitle={`Boş disk: ${num(live?.latest?.disk_free_mb, 0, "MB")} · Wi-Fi sinyali: ${num(
            live?.latest?.wifi_dbm,
            0,
            "dBm",
          )}`}
        >
          {sysRows.length === 0 ? (
            <Empty>Sistem verisi bekleniyor…</Empty>
          ) : (
            <ResponsiveContainer width="100%" height={270}>
              <AreaChart data={sysRows} margin={{ top: 10, right: 14, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="cpuG" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#a78bfa" stopOpacity={0.35} />
                    <stop offset="95%" stopColor="#a78bfa" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="ramG" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.3} />
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
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
                <YAxis domain={[0, 100]} stroke="var(--muted-ink)" fontSize={11} width={40} />
                <Tooltip
                  labelFormatter={(v: number) => timeSec(new Date(v).toISOString())}
                  formatter={(v: number) => num(v, 1)}
                  contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 10 }}
                />
                <Legend />
                <Area type="monotone" dataKey="cpu" name="CPU (%)" stroke="#a78bfa" fill="url(#cpuG)" strokeWidth={2} isAnimationActive={false} />
                <Area type="monotone" dataKey="ram" name="RAM (%)" stroke="#10b981" fill="url(#ramG)" strokeWidth={2} isAnimationActive={false} />
                <Area type="monotone" dataKey="soc" name="SoC Isı (°C)" stroke="#38bdf8" fill="none" strokeWidth={2} isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>

      {/* ---- Live Packet Table + Raw JSON Frame Inspector ---- */}
      <div className="grid-2">
        <Card
          title={`Canlı Telemetri Paket Akışı (${series.length} paket bellekte)`}
          subtitle="Bir satıra tıklayarak sağ tarafta tüm ham sensör ve ısı JSON içeriğini inceleyin"
          actions={
            <div className="row-gap">
              <button type="button" className="button" onClick={downloadLiveBufferJson}>
                ⬇ Canlı Akışı İndir (JSON)
              </button>
              <button type="button" className="button" onClick={() => void connectBleDirect()}>
                📶 BLE GATT Test
              </button>
            </div>
          }
        >
          {bleStatus && <p className="hint" style={{ marginBottom: 8 }}>{bleStatus}</p>}
          <div className="table-wrap tall">
            <table>
              <thead>
                <tr>
                  <th>Saat</th>
                  <th>#</th>
                  <th>SoC °C</th>
                  <th>Panel °C</th>
                  <th>Işınım</th>
                  <th>ADC A0</th>
                  <th>CPU</th>
                </tr>
              </thead>
              <tbody>
                {[...series].reverse().slice(0, 60).map((p, idx) => (
                  <tr
                    key={`${p.timestamp_utc}_${idx}`}
                    onClick={() => setSelectedFrame(p)}
                    style={{ cursor: "pointer" }}
                  >
                    <td className="nowrap">{timeSec(p.timestamp_utc)}</td>
                    <td>{p.seq}</td>
                    <td>{num(p.mpu_temperature_c, 1, "°C")}</td>
                    <td>{num(p.panel_temperature_c, 1, "°C")}</td>
                    <td>{num(p.irradiance_wm2, 0, "W/m²")}</td>
                    <td>{num(p.adc_mv?.adc_a0_mv, 0, "mV")}</td>
                    <td>%{num(p.cpu_pct, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card
          title="Ham Sensör & Termal Paket İnceleyici (JSON)"
          subtitle={
            selectedFrame
              ? `Seçilen paket: ${timeSec(selectedFrame.timestamp_utc)}`
              : "Otomatik olarak en son gelen canlı paketi gösterir"
          }
          actions={
            <div className="row-gap">
              {selectedFrame && (
                <button type="button" className="button" onClick={() => setSelectedFrame(null)}>
                  Canlıya Dön
                </button>
              )}
              <button type="button" className="button" onClick={() => void copyFrameJson()}>
                {copied ? "✓ Kopyalandı" : "📋 Kopyala"}
              </button>
            </div>
          }
        >
          <pre className="json-box">
            {activeFrame ? JSON.stringify(activeFrame, null, 2) : "Veri bekleniyor…"}
          </pre>
        </Card>
      </div>
    </div>
  );
}
