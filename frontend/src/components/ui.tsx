import type { ReactNode } from "react";
import type { ValueKind } from "../api";
import { KIND_LABEL, num } from "../format";

export function Card({
  title,
  subtitle,
  children,
  actions,
  className = "",
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {(title || actions || subtitle) && (
        <header className="card-head">
          <div>
            {title && <h2>{title}</h2>}
            {subtitle && <p className="card-sub">{subtitle}</p>}
          </div>
          {actions && <div className="card-actions">{actions}</div>}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({
  label,
  value,
  sub,
  tone,
  spark,
  color = "var(--sun)",
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  tone?: "ok" | "warn" | "bad" | "muted";
  spark?: (number | null | undefined)[];
  color?: string;
}) {
  const clean = (spark ?? []).filter((x): x is number => typeof x === "number" && Number.isFinite(x));
  let path = "";
  if (clean.length >= 2) {
    const lo = Math.min(...clean);
    const hi = Math.max(...clean);
    const span = hi - lo || 1;
    path = clean
      .map((v, idx) => {
        const x = (idx / (clean.length - 1)) * 100;
        const y = 26 - ((v - lo) / span) * 22;
        return `${idx === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  }

  return (
    <div className={`stat ${tone ? `tone-${tone}` : ""}`}>
      <div className="stat-top">
        <div className="stat-label">{label}</div>
        {path && (
          <svg className="spark-svg" viewBox="0 0 100 28" preserveAspectRatio="none">
            <path d={path} fill="none" stroke={color} strokeWidth="2.2" strokeLinecap="round" />
          </svg>
        )}
      </div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

export function RadialGauge({
  label,
  value,
  min = 0,
  max = 100,
  unit = "",
  digits = 1,
  color = "#f59e0b",
  sub,
  badge,
}: {
  label: string;
  value: number | null | undefined;
  min?: number;
  max?: number;
  unit?: string;
  digits?: number;
  color?: string;
  sub?: ReactNode;
  badge?: ReactNode;
}) {
  const valid = typeof value === "number" && Number.isFinite(value);
  const pct = valid ? Math.max(0, Math.min(1, (value - min) / (max - min || 1))) : 0;
  const r = 52;
  const cx = 68;
  const cy = 66;
  const startDeg = 135;
  const sweepDeg = 270;

  function polar(deg: number) {
    const rad = (deg * Math.PI) / 180;
    return { x: cx + r * Math.cos(rad), y: cy + r * Math.sin(rad) };
  }

  function arcPath(fraction: number) {
    const clamped = Math.max(0.001, Math.min(1, fraction));
    const endDeg = startDeg + sweepDeg * clamped;
    const p0 = polar(startDeg);
    const p1 = polar(endDeg);
    const large = sweepDeg * clamped > 180 ? 1 : 0;
    return `M ${p0.x.toFixed(2)} ${p0.y.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${p1.x.toFixed(2)} ${p1.y.toFixed(2)}`;
  }

  return (
    <div className="gauge-card">
      <div className="gauge-head">
        <span className="gauge-title">{label}</span>
        {badge}
      </div>
      <div className="gauge-body">
        <svg viewBox="0 0 136 116" className="gauge-svg">
          <path
            d={arcPath(1)}
            fill="none"
            stroke="var(--gauge-track)"
            strokeWidth="10"
            strokeLinecap="round"
          />
          {valid && (
            <path
              d={arcPath(pct)}
              fill="none"
              stroke={color}
              strokeWidth="10"
              strokeLinecap="round"
              style={{ filter: `drop-shadow(0 0 6px ${color}66)` }}
            />
          )}
          <text x={cx} y={cy - 2} textAnchor="middle" className="gauge-val-text">
            {valid ? num(value, digits) : "—"}
          </text>
          <text x={cx} y={cy + 16} textAnchor="middle" className="gauge-unit-text">
            {unit}
          </text>
          <text x="22" y="108" textAnchor="start" className="gauge-bound-text">{min}</text>
          <text x="114" y="108" textAnchor="end" className="gauge-bound-text">{max}</text>
        </svg>
      </div>
      {sub && <div className="gauge-sub">{sub}</div>}
    </div>
  );
}

export function ThermalTile({
  name,
  code,
  tempC,
  peakC,
  minC = 0,
  maxC = 85,
}: {
  name: string;
  code: string;
  tempC: number | null | undefined;
  peakC?: number | null;
  minC?: number;
  maxC?: number;
}) {
  const valid = typeof tempC === "number" && Number.isFinite(tempC);
  const pct = valid ? Math.max(4, Math.min(100, ((tempC - minC) / (maxC - minC)) * 100)) : 0;
  const color = !valid
    ? "var(--muted-ink)"
    : tempC >= 55
      ? "#ef4444"
      : tempC >= 40
        ? "#f59e0b"
        : tempC >= 28
          ? "#10b981"
          : "#38bdf8";

  return (
    <div className={`thermal-tile ${valid ? "active" : "inactive"}`}>
      <div className="thermal-tile-top">
        <span className="thermal-name">{name}</span>
        <span className="thermal-dot" style={{ background: color }} />
      </div>
      <div className="thermal-value" style={{ color: valid ? color : undefined }}>
        {valid ? num(tempC, 2, "°C") : "Bekleniyor"}
      </div>
      <div className="thermal-bar-bg">
        <div className="thermal-bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
      <div className="thermal-meta">
        <code>{code}</code>
        {typeof peakC === "number" && <span>Tepe {num(peakC, 1, "°C")}</span>}
      </div>
    </div>
  );
}

export function KindBadge({ kind }: { kind: ValueKind }) {
  return <span className={`badge kind-${kind}`}>{KIND_LABEL[kind]}</span>;
}

export function ErrorBox({ message }: { message: string | null }) {
  if (!message) return null;
  return <div className="alert alert-bad">⚠️ {message}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
