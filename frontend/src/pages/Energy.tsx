import { useMemo, useState } from "react";
import {
  Area,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api, type DataSource } from "../api";
import { Card, Empty, ErrorBox, KindBadge, Stat } from "../components/ui";
import { num, shortDate } from "../format";
import { usePolling } from "../usePolling";

export default function Energy({ source }: { source: DataSource }) {
  const [days, setDays] = useState(14);
  const summary = usePolling(() => api.energySummary(source), 30_000, [source]);
  const daily = usePolling(() => api.energyDaily(days, source), 30_000, [days, source]);
  const s = summary.data;

  const rows = useMemo(() => {
    let cumH = 0;
    return (daily.data ?? []).map((d) => {
      const h = d.insolation_kwh_m2 ?? 0;
      cumH += h;
      return {
        ...d,
        label: shortDate(d.date),
        h,
        cumH: Number(cumH.toFixed(2)),
        covPct: Math.round(d.daylight_coverage * 100),
      };
    });
  }, [daily.data]);

  return (
    <div className="stack">
      <ErrorBox message={summary.error ?? daily.error} />
      {s?.calibration_status === "uncalibrated" && (
        <div className="alert alert-warn">
          Referans panel kalibre edilmedi: ışınım değerleri <b>tahmini</b>dir (±%20 hata payı).
          Kalibrasyon yöntemi: <code>docs/calibration.md</code>
        </div>
      )}

      <div className="grid-stats">
        <Stat
          label="Bugünkü Işınım"
          value={num(s?.today?.insolation_kwh_m2, 2, "kWh/m²")}
          spark={rows.map((r) => r.h)}
          color="#f59e0b"
          sub={s?.today ? `Gündüz kapsamı %${Math.round(s.today.daylight_coverage * 100)}` : "Bugün henüz veri yok"}
        />
        <Stat
          label="Ortalama (Tam Günler)"
          value={num(s?.mean_insolation_kwh_m2, 2, "kWh/m²")}
          sub="Üretim simülasyonu bu ortalamayı baz alır"
        />
        <Stat
          label="Dönem Toplam Işınım"
          value={rows.length ? num(rows[rows.length - 1].cumH, 2, "kWh/m²") : "—"}
          spark={rows.map((r) => r.cumH)}
          color="#10b981"
          sub={`Son ${rows.length} kayıtlı gün toplamı`}
        />
        <Stat
          label="Tam Gün Sayısı"
          tone={s && s.complete_days > 0 ? "ok" : "warn"}
          value={s ? `${s.complete_days} / ${s.days_available}` : "—"}
          sub="Aydınlık sürenin ≥ %90'ı ölçülen günler"
        />
      </div>

      <div className="grid-2">
        <Card
          title="Günlük Güneşlenme / Işınım (kWh/m²)"
          subtitle="Parlak çubuklar tam günleri, soluk çubuklar eksik günleri gösterir"
          actions={
            <div className="seg">
              {[7, 14, 30].map((d) => (
                <button key={d} type="button" className={d === days ? "active" : ""} onClick={() => setDays(d)}>
                  {d} gün
                </button>
              ))}
            </div>
          }
        >
          {rows.length === 0 ? (
            <Empty>{daily.loading ? "Yükleniyor…" : "Henüz günlük enerji verisi yok."}</Empty>
          ) : (
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis dataKey="label" stroke="var(--muted-ink)" fontSize={11} />
                <YAxis stroke="var(--muted-ink)" fontSize={11} width={40} />
                <Tooltip
                  formatter={(v: number) => num(v, 2, "kWh/m²")}
                  contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 10 }}
                />
                <Bar dataKey="h" name="Günlük Işınım" radius={[6, 6, 0, 0]} isAnimationActive={false}>
                  {rows.map((r) => (
                    <Cell key={r.date} fill={r.complete ? "var(--sun)" : "var(--sun-faint)"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          )}
        </Card>

        <Card
          title="Kümülatif Işınım & Ağırlıklı Panel Sıcaklığı"
          subtitle="Birikimli güneş enerjisi (kWh/m²) ve günlük ışınım-ağırlıklı çalışma sıcaklığı (°C)"
        >
          {rows.length === 0 ? (
            <Empty>Veri yok</Empty>
          ) : (
            <ResponsiveContainer width="100%" height={280}>
              <ComposedChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="cumGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#10b981" stopOpacity={0.35} />
                    <stop offset="95%" stopColor="#10b981" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid stroke="var(--grid)" vertical={false} />
                <XAxis dataKey="label" stroke="var(--muted-ink)" fontSize={11} />
                <YAxis yAxisId="left" stroke="#10b981" fontSize={11} width={44} />
                <YAxis yAxisId="right" orientation="right" stroke="#fb7185" fontSize={11} width={44} />
                <Tooltip
                  contentStyle={{ background: "var(--panel)", border: "1px solid var(--line)", borderRadius: 10 }}
                />
                <Legend />
                <Area
                  yAxisId="left"
                  type="monotone"
                  dataKey="cumH"
                  name="Kümülatif (kWh/m²)"
                  stroke="#10b981"
                  fill="url(#cumGrad)"
                  strokeWidth={2.2}
                  isAnimationActive={false}
                />
                <Line
                  yAxisId="right"
                  type="monotone"
                  dataKey="irradiance_weighted_panel_temp_c"
                  name="Ağırlıklı Panel T (°C)"
                  stroke="#fb7185"
                  strokeWidth={2}
                  isAnimationActive={false}
                />
              </ComposedChart>
            </ResponsiveContainer>
          )}
        </Card>
      </div>

      <Card title="Gün Gün Saha Ölçüm Özeti">
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Tarih</th>
                <th>Işınım</th>
                <th>Ref Enerji</th>
                <th>Tür</th>
                <th>Kapsam</th>
                <th>Panel T (Ağırlıklı)</th>
                <th>Örnek</th>
                <th>Boşluk</th>
                <th>Durum</th>
              </tr>
            </thead>
            <tbody>
              {[...(daily.data ?? [])].reverse().map((d) => (
                <tr key={d.date}>
                  <td>{shortDate(d.date)}</td>
                  <td>{num(d.insolation_kwh_m2, 2, "kWh/m²")}</td>
                  <td>{num(d.reference_energy_wh * 1000, 1, "mWh")}</td>
                  <td><KindBadge kind={d.insolation_kind} /></td>
                  <td>%{Math.round(d.daylight_coverage * 100)}</td>
                  <td>{num(d.irradiance_weighted_panel_temp_c, 1, "°C")}</td>
                  <td>{d.sample_count}</td>
                  <td>{d.gap_count}</td>
                  <td>
                    {d.complete ? (
                      <span className="badge ok">tam</span>
                    ) : (
                      <span className="badge warn">eksik</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
