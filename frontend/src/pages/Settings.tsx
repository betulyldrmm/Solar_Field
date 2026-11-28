import { useEffect, useState, type FormEvent, type ReactNode } from "react";
import { api, type SystemConfig, type SystemConfigOut } from "../api";
import { Card, ErrorBox } from "../components/ui";

type Field = { key: keyof SystemConfig; label: string; step?: string; hint?: string };

const GROUPS: { title: string; fields: Field[] }[] = [
  {
    title: "Ölçüm ve Kayıt Sıklığı",
    fields: [{ key: "measurement_interval_s", label: "SQLite kayıt aralığı (sn)", step: "1", hint: "Canlı test için 5–10 sn, uzun saha görevi için 300 sn" }],
  },
  {
    title: "Gece Kapanma Takvimi",
    fields: [
      { key: "shutdown_after_sunset_min", label: "Gün batımından sonra kapan (dk)", step: "1" },
      { key: "wake_before_sunrise_min", label: "Gün doğumundan önce açıl (dk)", step: "1" },
    ],
  },
  {
    title: "12 V Soğutma Fanı (STM32 Yerel Kontrol)",
    fields: [
      { key: "fan_on_c", label: "Açılma sıcaklığı (°C)", step: "0.5" },
      { key: "fan_off_c", label: "Kapanma sıcaklığı (°C)", step: "0.5", hint: "En az 2 °C histerezis gerekir" },
    ],
  },
  {
    title: "Planlanan Sistem Varsayılanları",
    fields: [
      { key: "panel_power_w", label: "Panel gücü (W)", step: "1" },
      { key: "panel_count", label: "Panel sayısı", step: "1" },
      { key: "performance_ratio", label: "Performans oranı", step: "0.01" },
      { key: "temp_coefficient_pct_per_c", label: "Sıcaklık katsayısı (%/°C)", step: "0.01" },
      { key: "tilt_deg", label: "Eğim (°)", step: "1" },
      { key: "azimuth_deg", label: "Azimut (°)", step: "1" },
    ],
  },
  {
    title: "Referans Panel Kalibrasyonu",
    fields: [
      { key: "ref_isc_per_wm2_a", label: "k: Isc / ışınım (A per W/m²)", step: "any", hint: "Kalibrasyonla bulunur" },
      { key: "ref_isc_temp_coeff_per_c", label: "Isc sıcaklık katsayısı (1/°C)", step: "any" },
    ],
  },
];

const TOKEN_KEY = "sfs_api_token";

function readToken(): string {
  try {
    return sessionStorage.getItem(TOKEN_KEY) ?? "vegas123";
  } catch {
    return "vegas123";
  }
}

export default function Settings({
  config,
  onSaved,
}: {
  config: SystemConfigOut | null;
  onSaved: (c: SystemConfigOut) => void;
}) {
  const [draft, setDraft] = useState<SystemConfig | null>(null);
  const [token, setToken] = useState(readToken);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (config) {
      const { installed_kwp: _k, data_mode: _d, ...rest } = config;
      void _k;
      void _d;
      setDraft(rest);
    }
  }, [config]);

  if (!draft) return <Card><div className="empty">Ayarlar yükleniyor…</div></Card>;

  const set = <K extends keyof SystemConfig>(k: K, v: SystemConfig[K]) =>
    setDraft((d) => (d ? { ...d, [k]: v } : d));

  async function applyQuickInterval(sec: number) {
    if (!draft) return;
    const next = { ...draft, measurement_interval_s: sec };
    setDraft(next);
    setBusy(true);
    setMsg(null);
    try {
      const saved = await api.saveConfig(next, token);
      onSaved(saved);
      setMsg({ ok: true, text: `STM32 kayıt aralığı ${sec} saniye olarak güncellendi.` });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!draft) return;
    setBusy(true);
    setMsg(null);
    try {
      sessionStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* storage unavailable */
    }
    try {
      const saved = await api.saveConfig(draft, token);
      onSaved(saved);
      setMsg({ ok: true, text: "Ayarlar kaydedildi ve STM32U585'e iletildi." });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }

  const row = (key: string, label: ReactNode, input: ReactNode, hint?: string) => (
    <label key={key}>
      {label}
      {input}
      {hint && <span className="hint">{hint}</span>}
    </label>
  );

  return (
    <form className="stack" onSubmit={save}>
      <Card
        title="Hızlı MCU Kayıt Sıklığı Seçimi"
        subtitle="STM32U585'in veritabanına ve CSV'ye kaç saniyede bir ortalama kayıt göndereceğini tek dokunuşla seçin"
      >
        <div className="row-gap">
          {[
            { s: 5, label: "⚡ 5 sn (Canlı Masaüstü Test)" },
            { s: 15, label: "🚀 15 sn (Hızlı Saha Kontrol)" },
            { s: 60, label: "⏱️ 60 sn (Dakikalık)" },
            { s: 300, label: "🔋 300 sn (5 dk Uzun Saha Modu)" },
          ].map((p) => (
            <button
              key={p.s}
              type="button"
              className={draft.measurement_interval_s === p.s ? "primary" : "button"}
              disabled={busy}
              onClick={() => void applyQuickInterval(p.s)}
            >
              {p.label}
            </button>
          ))}
        </div>
      </Card>

      <div className="grid-2">
        {GROUPS.map((g) => (
          <Card key={g.title} title={g.title}>
            <div className="form">
              {g.title === "Gece Kapanma Takvimi" &&
                row(
                  "night",
                  "Gece kapanma açık",
                  <select
                    value={draft.night_shutdown_enabled ? "1" : "0"}
                    onChange={(e) => set("night_shutdown_enabled", e.target.value === "1")}
                  >
                    <option value="1">Evet</option>
                    <option value="0">Hayır (7/24 kesintisiz)</option>
                  </select>,
                )}
              {g.title === "Referans Panel Kalibrasyonu" &&
                row(
                  "mode",
                  "Ölçüm yöntemi",
                  <select
                    value={draft.reference_mode}
                    onChange={(e) =>
                      set("reference_mode", e.target.value as SystemConfig["reference_mode"])
                    }
                  >
                    <option value="short_circuit">Kısa devre akımı (önerilen)</option>
                    <option value="resistive_load">Yük direnci</option>
                  </select>,
                )}
              {g.fields.map((f) =>
                row(
                  f.key,
                  f.label,
                  <input
                    type="number"
                    step={f.step ?? "any"}
                    value={draft[f.key] as number}
                    onChange={(e) => set(f.key, Number(e.target.value) as never)}
                  />,
                  f.hint,
                ),
              )}
              {g.title === "Referans Panel Kalibrasyonu" &&
                row(
                  "cal",
                  "Kalibre edildi mi?",
                  <select
                    value={draft.ref_calibrated ? "1" : "0"}
                    onChange={(e) => set("ref_calibrated", e.target.value === "1")}
                  >
                    <option value="0">Hayır (etiket varsayımı)</option>
                    <option value="1">Evet</option>
                  </select>,
                  "Yalnızca sahada kalibrasyon yapıldıysa Evet seçin",
                )}
            </div>
          </Card>
        ))}
      </div>

      <Card title="Ayarları Uygula">
        <div className="form inline">
          {row(
            "token",
            "API anahtarı (SFS_API_TOKEN)",
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              autoComplete="off"
            />,
            "UNO Q varsayılan şifresi: vegas123",
          )}
          <button className="primary" disabled={busy}>
            {busy ? "Kaydediliyor…" : "💾 Ayarları Kaydet & MCU'ya Gönder"}
          </button>
        </div>
        {msg &&
          (msg.ok ? (
            <div className="alert alert-ok">✓ {msg.text}</div>
          ) : (
            <ErrorBox message={msg.text} />
          ))}
      </Card>
    </form>
  );
}
