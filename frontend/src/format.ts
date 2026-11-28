import type { ValueKind } from "./api";

const TZ = "Europe/Istanbul";

export function num(v: number | null | undefined, digits = 2, unit = ""): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  const s = v.toLocaleString("tr-TR", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  return unit ? `${s} ${unit}` : s;
}

/** Up to `max` decimals, no trailing zeros (450 -> "450", 0.8 -> "0,8"). */
export function smart(v: number | null | undefined, max = 3): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return v.toLocaleString("tr-TR", { maximumFractionDigits: max });
}

export function time(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("tr-TR", { timeZone: TZ, hour: "2-digit", minute: "2-digit" });
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("tr-TR", {
    timeZone: TZ, day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

export function shortDate(isoDate: string): string {
  const [y, m, d] = isoDate.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d)).toLocaleDateString("tr-TR", { day: "2-digit", month: "short", timeZone: "UTC" });
}

export function age(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 90) return `${Math.round(seconds)} sn önce`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} dk önce`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)} sa önce`;
  return `${Math.round(seconds / 86400)} gün önce`;
}

export const KIND_LABEL: Record<ValueKind, string> = {
  measured: "ölçülen",
  assumed: "varsayılan",
  calculated: "hesaplanan",
  estimated: "tahmini",
};

export const STATUS_LABEL: Record<string, string> = {
  online: "Çevrimiçi",
  stale: "Veri gecikiyor",
  offline: "Bağlantı yok",
  sleeping: "Uyuyor (gece)",
};
