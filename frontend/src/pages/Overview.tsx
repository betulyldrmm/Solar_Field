import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  api,
  type DataSource,
  type DeviceStatus,
  type RangeOverviewResponse,
  type SystemConfigOut,
} from "../api";
import { num } from "../format";

type PeriodMode = "day" | "week" | "month" | "year" | "custom";

const MONTHS_FULL = [
  "Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran",
  "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık",
];
const DOW_SHORT = ["Pzt", "Sal", "Çar", "Per", "Cum", "Cmt", "Paz"];

function toLocalDateStr(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function parseLocalDate(ymd: string): Date {
  const [y, m, d] = ymd.split("-").map(Number);
  return new Date(y, m - 1, d, 0, 0, 0, 0);
}

function addDays(d: Date, days: number): Date {
  const next = new Date(d);
  next.setDate(next.getDate() + days);
  return next;
}

export default function Overview({
  config,
  source,
  refreshSec,
}: {
  config: SystemConfigOut | null;
  source: DataSource;
  refreshSec: number;
}) {
  const [mode, setMode] = useState<PeriodMode>("day");
  const [anchor, setAnchor] = useState<Date>(() => new Date());
  const [calMonth, setCalMonth] = useState<Date>(() => new Date());
  const [showCalendar, setShowCalendar] = useState<boolean>(false);

  const [customStart, setCustomStart] = useState<string>(() => toLocalDateStr(addDays(new Date(), -6)));
  const [customEnd, setCustomEnd] = useState<string>(() => toLocalDateStr(new Date()));
  const [rangeClickStage, setRangeClickStage] = useState<"start" | "end">("start");

  const [data, setData] = useState<RangeOverviewResponse | null>(null);
  const [status, setStatus] = useState<DeviceStatus | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [err, setErr] = useState<string | null>(null);

  // Seçilen mod ve tarihe göre [start, end] aralığını hesapla
  const { startDate, endDate, periodTitle, bucket } = useMemo(() => {
    if (mode === "day") {
      const s = new Date(anchor.getFullYear(), anchor.getMonth(), anchor.getDate(), 0, 0, 0);
      const e = new Date(anchor.getFullYear(), anchor.getMonth(), anchor.getDate(), 23, 59, 59);
      return {
        startDate: s,
        endDate: e,
        periodTitle: `${s.getDate()} ${MONTHS_FULL[s.getMonth()]} ${s.getFullYear()}`,
        bucket: "hour" as const,
      };
    }
    if (mode === "week") {
      const e = new Date(anchor.getFullYear(), anchor.getMonth(), anchor.getDate(), 23, 59, 59);
      const s = addDays(new Date(anchor.getFullYear(), anchor.getMonth(), anchor.getDate(), 0, 0, 0), -6);
      return {
        startDate: s,
        endDate: e,
        periodTitle: `${s.getDate()} ${MONTHS_FULL[s.getMonth()]} – ${e.getDate()} ${MONTHS_FULL[e.getMonth()]}`,
        bucket: "day" as const,
      };
    }
    if (mode === "month") {
      const s = new Date(anchor.getFullYear(), anchor.getMonth(), 1, 0, 0, 0);
      const e = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0, 23, 59, 59);
      return {
        startDate: s,
        endDate: e,
        periodTitle: `${MONTHS_FULL[s.getMonth()]} ${s.getFullYear()}`,
        bucket: "day" as const,
      };
    }
    if (mode === "year") {
      const e = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0, 23, 59, 59);
      const s = new Date(anchor.getFullYear() - 1, anchor.getMonth() + 1, 1, 0, 0, 0);
      return {
        startDate: s,
        endDate: e,
        periodTitle: `${MONTHS_FULL[s.getMonth()]} ${s.getFullYear()} – ${MONTHS_FULL[e.getMonth()]} ${e.getFullYear()}`,
        bucket: "month" as const,
      };
    }
    const s = parseLocalDate(customStart);
    const eRaw = parseLocalDate(customEnd);
    const e = eRaw >= s
      ? new Date(eRaw.getFullYear(), eRaw.getMonth(), eRaw.getDate(), 23, 59, 59)
      : new Date(s.getFullYear(), s.getMonth(), s.getDate(), 23, 59, 59);
    return {
      startDate: s,
      endDate: e,
      periodTitle: `${toLocalDateStr(s)} → ${toLocalDateStr(e)}`,
      bucket: "auto" as const,
    };
  }, [mode, anchor, customStart, customEnd]);

  const loadData = useCallback(async () => {
    setLoading(true);
    setErr(null);
    try {
      const [ov, st] = await Promise.all([
        api.rangeOverview(startDate.toISOString(), endDate.toISOString(), bucket, source),
        api.deviceStatus(source),
      ]);
      setData(ov);
      setStatus(st);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [startDate, endDate, bucket, source]);

  useEffect(() => {
    void loadData();
    if (refreshSec > 0) {
      const id = window.setInterval(() => void loadData(), refreshSec * 1000);
      return () => window.clearInterval(id);
    }
    return undefined;
  }, [loadData, refreshSec]);

  function stepPeriod(dir: -1 | 1) {
    setAnchor((prev) => {
      const d = new Date(prev);
      if (mode === "day") d.setDate(d.getDate() + dir);
      else if (mode === "week") d.setDate(d.getDate() + dir * 7);
      else if (mode === "month") d.setMonth(d.getMonth() + dir);
      else d.setFullYear(d.getFullYear() + dir);
      setCalMonth(new Date(d.getFullYear(), d.getMonth(), 1));
      return d;
    });
  }

  // Görsel mini takvim hücreleri
  const calendarCells = useMemo(() => {
    const y = calMonth.getFullYear();
    const m = calMonth.getMonth();
    const first = new Date(y, m, 1);
    const startOffset = (first.getDay() + 6) % 7; // Pazartesi = 0
    const daysInMonth = new Date(y, m + 1, 0).getDate();
    const cells: { date: Date | null; ymd: string }[] = [];
    for (let i = 0; i < startOffset; i++) cells.push({ date: null, ymd: `pad_${i}` });
    for (let day = 1; day <= daysInMonth; day++) {
      const d = new Date(y, m, day);
      cells.push({ date: d, ymd: toLocalDateStr(d) });
    }
    return cells;
  }, [calMonth]);

  function onCalendarDayClick(d: Date) {
    const ymd = toLocalDateStr(d);
    if (mode === "custom") {
      if (rangeClickStage === "start") {
        setCustomStart(ymd);
        if (ymd > customEnd) setCustomEnd(ymd);
        setRangeClickStage("end");
      } else {
        if (ymd < customStart) {
          setCustomStart(ymd);
        } else {
          setCustomEnd(ymd);
        }
        setRangeClickStage("start");
      }
    } else {
      setAnchor(d);
      setMode("day");
    }
  }

  const startYmd = toLocalDateStr(startDate);
  const endYmd = toLocalDateStr(endDate);
  const points = data?.points ?? [];
  const sum = data?.summary;
  const soc = status?.battery.soc_pct_estimated ?? null;

  return (
    <div className="stack">
      {/* ---- 1. TAKVİM VE DÖNEM SEÇİCİ ---- */}
      <section className="card">
        <div className="period-bar">
          <div className="period-tabs">
            {(
              [
                { id: "day", label: "Günlük" },
                { id: "week", label: "Haftalık" },
                { id: "month", label: "Aylık" },
                { id: "year", label: "Yıllık (12 Ay)" },
                { id: "custom", label: "Tarih Aralığı Seç" },
              ] as const
            ).map((t) => (
              <button
                key={t.id}
                type="button"
                className={mode === t.id ? "active" : ""}
                onClick={() => {
                  setMode(t.id);
                  if (t.id === "custom") setShowCalendar(true);
                }}
              >
                {t.label}
              </button>
            ))}
          </div>

          <div className="row-gap">
            {mode !== "custom" && (
              <div className="date-stepper">
                <button type="button" onClick={() => stepPeriod(-1)} title="Önceki">
                  ◀
                </button>
                <span className="date-stepper-label">{periodTitle}</span>
                <button type="button" onClick={() => stepPeriod(1)} title="Sonraki">
                  ▶
                </button>
              </div>
            )}

            <button
              type="button"
              className="btn"
              onClick={() => setShowCalendar((v) => !v)}
            >
              📅 {showCalendar ? "Takvimi Kapat" : "Takvim"}
            </button>

            <a
              className="btn btn-primary"
              href={api.csvUrl(startDate.toISOString(), source, endDate.toISOString())}
              download
            >
              ⬇ Veriyi İndir (CSV)
            </a>
          </div>
        </div>

        {showCalendar && (
          <div className="calendar-drawer">
            <div className="mini-cal">
              <div className="mini-cal-head">
                <button
                  type="button"
                  onClick={() => setCalMonth(new Date(calMonth.getFullYear(), calMonth.getMonth() - 1, 1))}
                >
                  ◀
                </button>
                <span>
                  {MONTHS_FULL[calMonth.getMonth()]} {calMonth.getFullYear()}
                </span>
                <button
                  type="button"
                  onClick={() => setCalMonth(new Date(calMonth.getFullYear(), calMonth.getMonth() + 1, 1))}
                >
                  ▶
                </button>
              </div>
              <div className="mini-cal-grid">
                {DOW_SHORT.map((d) => (
                  <div key={d} className="mini-cal-dow">
                    {d}
                  </div>
                ))}
                {calendarCells.map((c) => {
                  if (!c.date) return <div key={c.ymd} className="mini-cal-day muted-day" />;
                  const isEdge = c.ymd === startYmd || c.ymd === endYmd;
                  const inRange = c.ymd >= startYmd && c.ymd <= endYmd;
                  return (
                    <button
                      key={c.ymd}
                      type="button"
                      className={`mini-cal-day ${isEdge ? "selected" : inRange ? "in-range" : ""}`}
                      onClick={() => c.date && onCalendarDayClick(c.date)}
                    >
                      {c.date.getDate()}
                    </button>
                  );
                })}
              </div>
            </div>

            <div className="range-inputs">
              <h3>İstediğiniz Tarih Aralığını Seçin</h3>
              <p className="card-sub">
                Takvimden bir güne tıklayabilir veya aşağıdan başlangıç ve bitiş tarihini seçerek o günlerin elektrik üretimini görebilirsiniz.
              </p>
              <div className="range-row">
                <label>
                  Başlangıç Tarihi
                  <input
                    type="date"
                    value={mode === "custom" ? customStart : startYmd}
                    onChange={(e) => {
                      setMode("custom");
                      setCustomStart(e.target.value);
                    }}
                  />
                </label>
                <label>
                  Bitiş Tarihi
                  <input
                    type="date"
                    value={mode === "custom" ? customEnd : endYmd}
                    onChange={(e) => {
                      setMode("custom");
                      setCustomEnd(e.target.value);
                    }}
                  />
                </label>
                <button
                  type="button"
                  className="btn"
                  onClick={() => {
                    const now = new Date();
                    setAnchor(now);
                    setCalMonth(new Date(now.getFullYear(), now.getMonth(), 1));
                    setMode("day");
                  }}
                >
                  Bugüne Git
                </button>
              </div>
            </div>
          </div>
        )}
      </section>

      {err && <div className="alert alert-bad">⚠️ {err}</div>}

      {/* ---- 2. SADE ÖZET KUTULARI (TEK BAKIŞTA ANLAŞILIR) ---- */}
      <div className="summary-grid">
        <div className="big-stat">
          <div className="big-stat-title">⚡ En Yüksek Elektrik Gücü</div>
          <div className="big-stat-value" style={{ color: "var(--sun-dark)" }}>
            {num(sum?.peak_power_w, 0, "W")}
          </div>
          <div className="big-stat-sub">
            Gündüz ortalaması: <b>{num(sum?.avg_daylight_power_w, 0, "W")}</b>
          </div>
        </div>

        <div className="big-stat">
          <div className="big-stat-title">🔋 Toplam Üretilen Elektrik</div>
          <div className="big-stat-value" style={{ color: "var(--leaf)" }}>
            {num(sum?.total_energy_kwh, 1, "kWh")}
          </div>
          <div className="big-stat-sub">
            {config
              ? `${config.panel_count} adet × ${config.panel_power_w} W panel için`
              : "Seçilen tarih aralığı toplamı"}
          </div>
        </div>

        <div className="big-stat">
          <div className="big-stat-title">🌡️ Panel Sıcaklığı</div>
          <div className="big-stat-value" style={{ color: "var(--hot)" }}>
            {num(sum?.avg_panel_temperature_c, 1, "°C")}
          </div>
          <div className="big-stat-sub">
            En yüksek: <b>{num(sum?.peak_panel_temperature_c, 1, "°C")}</b> · Hava:{" "}
            <b>{num(sum?.avg_ambient_temperature_c, 1, "°C")}</b>
          </div>
        </div>

        <div className="big-stat">
          <div className="big-stat-title">🔌 İstasyon Pil Durumu</div>
          <div className="big-stat-value">
            {soc === null ? "—" : `%${Math.round(soc)}`}
          </div>
          <div className="big-stat-sub">
            {num(status?.battery.voltage_v, 2, "V")} · {data?.sample_count ?? 0} ölçüm kaydı okundu
          </div>
        </div>
      </div>

      {/* ---- 3. GRAFİK 1: ALINAN ELEKTRİK GÜCÜ (PÜRÜZSÜZ PARABOL) ---- */}
      <section className="card">
        <div className="card-head">
          <div>
            <h2>Alınan Elektrik Gücü (Watt) — {periodTitle}</h2>
            <p className="card-sub">
              {data?.bucket === "hour"
                ? "Sabah gün doğumundan öğle zirvesine ve akşam gün batımına saatlik üretim eğrisi"
                : data?.bucket === "month"
                  ? "Aylara göre mevsimsel ortalama gündüz elektrik gücü eğrisi"
                  : "Seçilen günlerde alınan ortalama elektrik gücü eğrisi"}
            </p>
          </div>
        </div>

        {points.length === 0 || data?.sample_count === 0 ? (
          <div className="empty">
            {loading
              ? "Veriler okunuyor…"
              : "Seçilen tarih aralığında kayıtlı güneş verisi bulunamadı."}
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={300}>
            <AreaChart data={points} margin={{ top: 10, right: 18, left: 0, bottom: 4 }}>
              <defs>
                <linearGradient id="wattSmoothGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#f59e0b" stopOpacity={0.42} />
                  <stop offset="95%" stopColor="#f59e0b" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="label" stroke="var(--muted-ink)" fontSize={12} tickMargin={8} />
              <YAxis
                stroke="var(--muted-ink)"
                fontSize={12}
                width={58}
                tickFormatter={(v: number) => `${Math.round(v)} W`}
              />
              <Tooltip
                formatter={(v: number) => [num(v, 0, "Watt"), "Alınan Güç"]}
                contentStyle={{
                  background: "var(--panel)",
                  border: "1px solid var(--line)",
                  borderRadius: 12,
                }}
              />
              <Area
                type="monotone"
                dataKey="power_w"
                name="Elektrik Gücü (Watt)"
                stroke="#f59e0b"
                strokeWidth={3}
                fill="url(#wattSmoothGrad)"
                dot={false}
                connectNulls
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </section>

      {/* ---- 4. GRAFİK 2: PANEL VE HAVA SICAKLIĞI (°C — PÜRÜZSÜZ EĞRİ) ---- */}
      <section className="card">
        <div className="card-head">
          <div>
            <h2>Panel Sıcaklığı (°C) — {periodTitle}</h2>
            <p className="card-sub">
              Güneş altında panelin ve dış ortam havasının sıcaklık değişimi
            </p>
          </div>
        </div>

        {points.length === 0 || data?.sample_count === 0 ? (
          <div className="empty">Sıcaklık verisi bulunamadı.</div>
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            <AreaChart data={points} margin={{ top: 10, right: 18, left: 0, bottom: 4 }}>
              <defs>
                <linearGradient id="tempPanelGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#ef4444" stopOpacity={0.28} />
                  <stop offset="95%" stopColor="#ef4444" stopOpacity={0.0} />
                </linearGradient>
                <linearGradient id="tempAmbGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="#0ea5e9" stopOpacity={0.2} />
                  <stop offset="95%" stopColor="#0ea5e9" stopOpacity={0.0} />
                </linearGradient>
              </defs>
              <CartesianGrid stroke="var(--grid)" vertical={false} />
              <XAxis dataKey="label" stroke="var(--muted-ink)" fontSize={12} tickMargin={8} />
              <YAxis
                stroke="var(--muted-ink)"
                fontSize={12}
                width={52}
                tickFormatter={(v: number) => `${Math.round(v)}°C`}
              />
              <Tooltip
                formatter={(v: number, name: string) => [num(v, 1, "°C"), name]}
                contentStyle={{
                  background: "var(--panel)",
                  border: "1px solid var(--line)",
                  borderRadius: 12,
                }}
              />
              <Legend />
              <Area
                type="monotone"
                dataKey="panel_temperature_c"
                name="Panel Sıcaklığı (°C)"
                stroke="#ef4444"
                strokeWidth={3}
                fill="url(#tempPanelGrad)"
                dot={false}
                connectNulls
              />
              <Area
                type="monotone"
                dataKey="ambient_temperature_c"
                name="Hava Sıcaklığı (°C)"
                stroke="#0ea5e9"
                strokeWidth={2.2}
                fill="url(#tempAmbGrad)"
                dot={false}
                connectNulls
              />
            </AreaChart>
          </ResponsiveContainer>
        )}
      </section>

      {/* ---- 5. SADE DÖNEM TABLOSU ---- */}
      {points.length > 0 && (data?.sample_count ?? 0) > 0 && (
        <section className="card">
          <div className="card-head">
            <div>
              <h2>Seçilen Dönem Özeti</h2>
              <p className="card-sub">Her zaman dilimi için ortalama güç, tepe güç, üretilen enerji ve sıcaklık</p>
            </div>
          </div>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Zaman</th>
                  <th>Ortalama Güç</th>
                  <th>En Yüksek Güç</th>
                  <th>Üretilen Elektrik</th>
                  <th>Panel Sıcaklığı</th>
                  <th>Hava Sıcaklığı</th>
                </tr>
              </thead>
              <tbody>
                {points
                  .filter((p) => p.power_w !== null || p.panel_temperature_c !== null)
                  .map((p) => (
                    <tr key={p.key}>
                      <td><b>{p.label}</b></td>
                      <td>{num(p.power_w, 0, "W")}</td>
                      <td>{num(p.peak_power_w, 0, "W")}</td>
                      <td>{num(p.energy_kwh, 2, "kWh")}</td>
                      <td>{num(p.panel_temperature_c, 1, "°C")}</td>
                      <td>{num(p.ambient_temperature_c, 1, "°C")}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
