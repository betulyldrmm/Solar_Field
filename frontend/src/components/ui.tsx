import type { ReactNode } from "react";
import type { ValueKind } from "../api";
import { KIND_LABEL } from "../format";

export function Card({ title, children, actions, className = "" }:
  { title?: ReactNode; children: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <header className="card-head">
          {title && <h2>{title}</h2>}
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}

export function Stat({ label, value, sub, tone }:
  { label: string; value: ReactNode; sub?: ReactNode; tone?: "ok" | "warn" | "bad" | "muted" }) {
  return (
    <div className={`stat ${tone ? `tone-${tone}` : ""}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
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
