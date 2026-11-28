import { useEffect, useState, type FormEvent } from "react";
import {
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  api,
  type DataSource,
  type EstimateRequest,
  type EstimateResponse,
  type SystemConfigOut,
} from "../api";
import { Card, ErrorBox, KindBadge, Stat } from "../components/ui";
import { num, shortDate, smart } from "../format";

const INPUT_LABEL: Record<string, string> = {
  panel_power_w: "Panel gücü",
  panel_count: "Panel sayısı",
  installed_kwp: "Kurulu güç",
  performance_ratio: "Performans oranı (PR)",
  insolation_kwh_m2_mean: "Ortalama ışınım",
  temp_coefficient_pct_per_c: "Sıcaklık katsayısı",
  reference_panel_temperature: "Referans panel sıcaklığı",
  peak_sun_hours: "Tepe güneş saati",
  daily_energy_kwh: "Günlük üretim",
};

export default function Estimate({
  config,
  source,
}: {
  config: SystemConfigOut | null;
  source: DataSource;
}) {
  const [form, setForm] = useState<EstimateRequest>({
    panel_power_w: 450,
    panel_count: 8,
    performance_ratio: 0.8,
    temp_coefficient_pct_per_c: -0.35,
    tilt_deg: 30,
    azimuth_deg: 180,
    method: "auto",
    peak_sun_hours: 4.5,
    days: 7,
  });
  const [result, setResult] = useState<EstimateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!config) return;
    setForm((f) => ({
      ...f,
      panel_power_w: config.panel_power_w,
      panel_count: config.panel_count,
      performance_ratio: config.performance_ratio,
      temp_coefficient_pct_per_c: config.temp_coefficient_pct_per_c,
      tilt_deg: config.tilt_deg,
      azimuth_deg: config.azimuth_deg,
    }));
  }, [config]);

  const set = <K extends keyof EstimateRequest>(k: K, v: EstimateRequest[K]) =>
    setForm((f) => ({ ...f, [k]: v }));
  const numIn = (k: keyof EstimateRequest, step = "any") => (
    <input
      type="number"
      step={step}
      value={(form[k] as number | null) ?? ""}
      onChange={(e) => set(k, (e.target.value === "" ? null : Number(e.target.value)) as never)}
    />
  );

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResult(await api.estimate(form, source));
    } catch (err) {
      setResult(null);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const kwp = (form.panel_power_w * form.panel_count) / 1000;

  return (
    <div className="grid-side">
      <Card title="Planlanan GES Sistemi" subtitle="Panel adedi ve gücünü değiştirerek saha fizibilitesini hesaplayın">
        <form className="form" onSubmit={submit}>
          <label>Panel gücü (W){numIn("panel_power_w", "5")}</label>
          <label>Panel sayısı{numIn("panel_count", "1")}</label>
          <div className="hint">
            Toplam kurulu güç: <b>{num(kwp, 2, "kWp")}</b>
          </div>
          <label>Performans oranı (PR){numIn("performance_ratio", "0.01")}</label>
          <label>Sıcaklık katsayısı (%/°C){numIn("temp_coefficient_pct_per_c", "0.01")}</label>
          <label>Eğim (°){numIn("tilt_deg", "1")}</label>
          <label>Azimut (° — 180 = Güney){numIn("azimuth_deg", "1")}</label>
          <label>
            Hesaplama Yöntemi
            <select
              value={form.method}
              onChange={(e) => set("method", e.target.value as EstimateRequest["method"])}
            >
              <option value="auto">Otomatik (Tam saha günü varsa ölçüm)</option>
              <option value="measured_reference">Saha Ölçümü (Referans Panel)</option>
              <option value="theoretical_psh">Teorik (Tepe Güneş Saati)</option>
            </select>
          </label>
          {form.method !== "measured_reference" && (
            <label>Yedek tepe güneş saati (saat/gün){numIn("peak_sun_hours", "0.1")}</label>
          )}
          <label>Son kaç günün verisi kullanılsın{numIn("days", "1")}</label>
          <button className="primary" disabled={busy}>
            {busy ? "Hesaplanıyor…" : "⚡ Üretimi Hesapla"}
          </button>
        </form>
      </Card>

      <div className="stack">
        <ErrorBox message={error} />
        {!result && !error && (
          <Card>
            <div className="empty">
              Soldan planladığınız güneş santralini girip <b>Üretimi Hesapla</b> düğmesine basın.
            </div>
          </Card>
        )}
        {result && (
          <>
            <div className="grid-stats">
              <Stat
                label="Günlük Ortalama Üretim"
                tone="ok"
                value={num(result.daily_energy_kwh, 2, "kWh/gün")}
                sub={`${num(result.installed_kwp, 2, "kWp")} kurulu güç`}
              />
              <Stat
                label="Aylık Tahmini Üretim"
                value={num(result.daily_energy_kwh * 30, 0, "kWh/ay")}
                sub="Mevcut dönem ışınım ortalamasıyla"
              />
              <Stat
                label="Yıllık Projeksiyon"
                value={num((result.daily_energy_kwh * 365) / 1000, 2, "MWh/yıl")}
                sub={result.method === "measured_reference" ? "Saha verisine dayalı" : "Teorik PSH"}
              />
            </div>

            {result.warnings.length > 0 && (
              <div className="alert alert-warn">
                <ul>
                  {result.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              </div>
            )}

            {result.per_day.length > 0 && (
              <Card
                title="Gün Gün Üretim (kWh) & Sıcaklık Çarpanı (f_T)"
                subtitle="Hücre sıcaklığı 25 °C üzerine çıktığında sıcaklık katsayısı verimi düşürür"
              >
                <ResponsiveContainer width="100%" height={260}>
                  <ComposedChart
                    data={result.per_day.map((p) => ({
                      ...p,
                      label: shortDate(String(p.date)),
                      ftPct: Number((Number(p.temperature_factor) * 100).toFixed(2)),
                    }))}
                  >
                    <CartesianGrid stroke="var(--grid)" vertical={false} />
                    <XAxis dataKey="label" stroke="var(--muted-ink)" fontSize={11} />
                    <YAxis yAxisId="kwh" stroke="#10b981" fontSize={11} width={44} />
                    <YAxis yAxisId="ft" orientation="right" domain={[90, 108]} stroke="#fb7185" fontSize={11} width={44} />
                    <Tooltip
                      contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 10 }}
                    />
                    <Legend />
                    <Bar
                      yAxisId="kwh"
                      dataKey="energy_kwh"
                      name="Tahmini Üretim (kWh)"
                      fill="var(--leaf)"
                      radius={[6, 6, 0, 0]}
                      isAnimationActive={false}
                    />
                    <Line
                      yAxisId="ft"
                      type="monotone"
                      dataKey="ftPct"
                      name="Termal Verim (%)"
                      stroke="#fb7185"
                      strokeWidth={2.2}
                      isAnimationActive={false}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </Card>
            )}

            <Card title="Hesaba Giren Etiketli Büyüklükler">
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Değer</th>
                      <th>Miktar</th>
                      <th>Tür</th>
                      <th>Açıklama</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(result.inputs).map(([k, v]) => (
                      <tr key={k}>
                        <td>{INPUT_LABEL[k] ?? k}</td>
                        <td>
                          {smart(v.value)} {v.unit !== "-" && v.unit !== "pcs" ? v.unit : ""}
                        </td>
                        <td>
                          <KindBadge kind={v.kind} />
                        </td>
                        <td className="muted">{v.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </>
        )}
      </div>
    </div>
  );
}
