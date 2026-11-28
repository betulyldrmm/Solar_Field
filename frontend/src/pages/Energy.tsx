import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api";
import { Card, Empty, ErrorBox, KindBadge, Stat } from "../components/ui";
import { num, shortDate } from "../format";
import { usePolling } from "../usePolling";

export default function Energy() {
  const [days, setDays] = useState(14);
  const summary = usePolling(api.energySummary, 60_000);
  const daily = usePolling(() => api.energyDaily(days), 60_000, [days]);
  const s = summary.data;
  const rows = (daily.data ?? []).map((d) => ({ ...d, label: shortDate(d.date), h: d.insolation_kwh_m2 ?? 0 }));

  return (
    <div className="stack">
      <ErrorBox message={summary.error ?? daily.error} />
      {s?.calibration_status === "uncalibrated" && (
        <div className="alert alert-warn">
          Referans panel kalibre edilmedi: ışınım değerleri <b>tahmini</b>dir (±%20 veya daha fazla hata olabilir).
          Yöntem: <code>docs/calibration.md</code>
        </div>
      )}

      <div className="grid-stats">
        <Stat label="Bugünkü ışınım" value={num(s?.today?.insolation_kwh_m2, 2, "kWh/m²")}
          sub={s?.today ? `Gün ölçüm kapsamı %${Math.round(s.today.daylight_coverage * 100)}` : "Bugün veri yok"} />
        <Stat label="Ortalama (tam günler)" value={num(s?.mean_insolation_kwh_m2, 2, "kWh/m²")}
          sub="Tahmin bu değeri kullanır" />
        <Stat label="Tam gün sayısı" value={s ? `${s.complete_days} / ${s.days_available}` : "—"}
          sub="Aydınlık sürenin ≥ %90'ı ölçülen günler" />
      </div>

      <Card title="Günlük ışınım (kWh/m²)" actions={
        <div className="seg">
          {[7, 14, 30].map((d) => (
            <button key={d} className={d === days ? "active" : ""} onClick={() => setDays(d)}>{d} gün</button>
          ))}
        </div>
      }>
        {rows.length === 0 ? <Empty>{daily.loading ? "Yükleniyor…" : "Henüz günlük veri yok."}</Empty> : (
          <>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis dataKey="label" stroke="var(--muted-ink)" fontSize={12} />
                <YAxis stroke="var(--muted-ink)" fontSize={12} width={40} />
                <Tooltip formatter={(v: number) => num(v, 2, "kWh/m²")}
                  contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 8 }} />
                <Bar dataKey="h" name="Işınım" radius={[4, 4, 0, 0]} isAnimationActive={false}>
                  {rows.map((r) => <Cell key={r.date} fill={r.complete ? "var(--sun)" : "var(--sun-faint)"} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
            <p className="footnote">Soluk çubuklar <b>eksik</b> günlerdir; üretim tahminine katılmaz.</p>
          </>
        )}
      </Card>

      <Card title="Gün gün">
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Tarih</th><th>Işınım</th><th>Tür</th><th>Kapsam</th><th>Panel T (ağırlıklı)</th><th>Örnek</th><th>Boşluk</th><th>Durum</th></tr>
            </thead>
            <tbody>
              {[...(daily.data ?? [])].reverse().map((d) => (
                <tr key={d.date}>
                  <td>{shortDate(d.date)}</td>
                  <td>{num(d.insolation_kwh_m2, 2, "kWh/m²")}</td>
                  <td><KindBadge kind={d.insolation_kind} /></td>
                  <td>%{Math.round(d.daylight_coverage * 100)}</td>
                  <td>{num(d.irradiance_weighted_panel_temp_c, 1, "°C")}</td>
                  <td>{d.sample_count}</td>
                  <td>{d.gap_count}</td>
                  <td>{d.complete ? <span className="badge ok">tam</span> : <span className="badge warn">eksik</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
