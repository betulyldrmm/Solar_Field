import { useEffect, useState, type FormEvent } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type EstimateRequest, type EstimateResponse, type SystemConfigOut } from "../api";
import { Card, ErrorBox, KindBadge } from "../components/ui";
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

export default function Estimate({ config }: { config: SystemConfigOut | null }) {
  const [form, setForm] = useState<EstimateRequest>({
    panel_power_w: 450, panel_count: 8, performance_ratio: 0.8, temp_coefficient_pct_per_c: -0.35,
    tilt_deg: 30, azimuth_deg: 180, method: "auto", peak_sun_hours: null, days: 7,
  });
  const [result, setResult] = useState<EstimateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!config) return;
    setForm((f) => ({
      ...f,
      panel_power_w: config.panel_power_w, panel_count: config.panel_count,
      performance_ratio: config.performance_ratio, temp_coefficient_pct_per_c: config.temp_coefficient_pct_per_c,
      tilt_deg: config.tilt_deg, azimuth_deg: config.azimuth_deg,
    }));
  }, [config]);

  const set = <K extends keyof EstimateRequest>(k: K, v: EstimateRequest[K]) => setForm((f) => ({ ...f, [k]: v }));
  const numIn = (k: keyof EstimateRequest, step = "any") => (
    <input type="number" step={step} value={(form[k] as number | null) ?? ""}
      onChange={(e) => set(k, (e.target.value === "" ? null : Number(e.target.value)) as never)} />
  );

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResult(await api.estimate(form));
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
      <Card title="Planlanan sistem">
        <form className="form" onSubmit={submit}>
          <label>Panel gücü (W){numIn("panel_power_w", "1")}</label>
          <label>Panel sayısı{numIn("panel_count", "1")}</label>
          <div className="hint">Kurulu güç: <b>{num(kwp, 2, "kWp")}</b></div>
          <label>Performans oranı (PR){numIn("performance_ratio", "0.01")}</label>
          <label>Sıcaklık katsayısı (%/°C){numIn("temp_coefficient_pct_per_c", "0.01")}</label>
          <label>Eğim (°){numIn("tilt_deg", "1")}</label>
          <label>Yön / azimut (° — 180 = güney){numIn("azimuth_deg", "1")}</label>
          <label>Yöntem
            <select value={form.method} onChange={(e) => set("method", e.target.value as EstimateRequest["method"])}>
              <option value="auto">Otomatik (ölçüm varsa ölçüm)</option>
              <option value="measured_reference">Saha ölçümü</option>
              <option value="theoretical_psh">Teorik (tepe güneş saati)</option>
            </select>
          </label>
          {form.method === "theoretical_psh" && <label>Tepe güneş saati (saat/gün){numIn("peak_sun_hours", "0.1")}</label>}
          <label>Son kaç günün verisi{numIn("days", "1")}</label>
          <button className="primary" disabled={busy}>{busy ? "Hesaplanıyor…" : "Tahmin et"}</button>
        </form>
      </Card>

      <div className="stack">
        <ErrorBox message={error} />
        {!result && !error && (
          <Card><div className="empty">Planlanan sistemi girip <b>Tahmin et</b>'e bas.</div></Card>
        )}
        {result && (
          <>
            <Card className="result">
              <div className="result-label">Ortalama günlük üretim</div>
              <div className="result-value">{num(result.daily_energy_kwh, 2, "kWh/gün")}</div>
              <div className="result-sub">
                {num(result.installed_kwp, 2, "kWp")} · {result.method === "measured_reference" ? "saha ölçümüne dayalı" : "teorik hesap"}
                {" · "}yaklaşık {num(result.daily_energy_kwh * 30, 0, "kWh/ay")}
                {result.data_source === "simulation" && <> · <b>SİMÜLASYON verisi</b></>}
              </div>
            </Card>
            {result.warnings.length > 0 && (
              <div className="alert alert-warn"><ul>{result.warnings.map((w) => <li key={w}>{w}</li>)}</ul></div>
            )}
            {result.per_day.length > 0 && (
              <Card title="Gün gün tahmin (kWh)">
                <ResponsiveContainer width="100%" height={240}>
                  <BarChart data={result.per_day.map((p) => ({ ...p, label: shortDate(String(p.date)) }))}>
                    <CartesianGrid stroke="var(--grid)" vertical={false} />
                    <XAxis dataKey="label" stroke="var(--muted-ink)" fontSize={12} />
                    <YAxis stroke="var(--muted-ink)" fontSize={12} width={40} />
                    <Tooltip formatter={(v: number) => num(v, 2, "kWh")}
                      contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 8 }} />
                    <Bar dataKey="energy_kwh" name="Üretim" fill="var(--leaf)" radius={[4, 4, 0, 0]} isAnimationActive={false} />
                  </BarChart>
                </ResponsiveContainer>
              </Card>
            )}
            <Card title="Hesaba giren değerler">
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Değer</th><th>Miktar</th><th>Tür</th><th>Not</th></tr></thead>
                  <tbody>
                    {Object.entries(result.inputs).map(([k, v]) => (
                      <tr key={k}>
                        <td>{INPUT_LABEL[k] ?? k}</td>
                        <td>{smart(v.value)} {v.unit !== "-" && v.unit !== "pcs" ? v.unit : ""}</td>
                        <td><KindBadge kind={v.kind} /></td>
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
