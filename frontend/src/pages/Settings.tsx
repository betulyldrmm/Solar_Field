import { useEffect, useState, type FormEvent } from "react";
import { api, type DataSource, type SystemConfig, type SystemConfigOut } from "../api";

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
  source,
  onSourceChange,
  refreshSec,
  onRefreshSecChange,
  theme,
  onThemeChange,
}: {
  config: SystemConfigOut | null;
  onSaved: (c: SystemConfigOut) => void;
  source: DataSource;
  onSourceChange: (s: DataSource) => void;
  refreshSec: number;
  onRefreshSecChange: (sec: number) => void;
  theme: "light" | "dark";
  onThemeChange: (t: "light" | "dark") => void;
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

  if (!draft) {
    return (
      <section className="card">
        <div className="empty">Ayarlar yükleniyor…</div>
      </section>
    );
  }

  const set = <K extends keyof SystemConfig>(k: K, v: SystemConfig[K]) =>
    setDraft((d) => (d ? { ...d, [k]: v } : d));

  async function save(e: FormEvent) {
    e.preventDefault();
    if (!draft) return;
    setBusy(true);
    setMsg(null);
    try {
      sessionStorage.setItem(TOKEN_KEY, token);
    } catch {
      /* ignore */
    }
    try {
      const saved = await api.saveConfig(draft, token);
      onSaved(saved);
      setMsg({ ok: true, text: "Ayarlar kaydedildi." });
    } catch (err) {
      setMsg({ ok: false, text: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="stack" onSubmit={save}>
      <div className="grid-2">
        {/* ---- Ekran ve Görünüm Ayarları ---- */}
        <section className="card">
          <div className="card-head">
            <div>
              <h2>Ekran ve Okuma Ayarları</h2>
              <p className="card-sub">Verilerin ekranda nasıl gösterileceğini seçin</p>
            </div>
          </div>

          <div className="form">
            <label>
              Ekran Otomatik Yenileme Hızı
              <select
                value={String(refreshSec)}
                onChange={(e) => onRefreshSecChange(Number(e.target.value))}
              >
                <option value="0">Kapalı — Sadece açıldığında ve tarih seçildiğinde oku (Önerilen)</option>
                <option value="60">1 dakikada bir yenile</option>
                <option value="300">5 dakikada bir yenile</option>
                <option value="30">30 saniyede bir yenile</option>
              </select>
              <span className="hint">
                İstasyon dağda 12 ay veri biriktirip Bluetooth ile okunduğu için sürekli yenilemeye gerek yoktur.
              </span>
            </label>

            <label>
              Gösterilen Veri Kaynağı
              <select
                value={source}
                onChange={(e) => onSourceChange(e.target.value as DataSource)}
              >
                <option value="simulation">12 Aylık Örnek Saha Verisi (Simülasyon)</option>
                <option value="hardware">Gerçek Sensör Kayıtları (Cihaz Donanımı)</option>
              </select>
            </label>

            <label>
              Görünüm Teması
              <select
                value={theme}
                onChange={(e) => onThemeChange(e.target.value as "light" | "dark")}
              >
                <option value="light">Açık Tema (Sade Beyaz)</option>
                <option value="dark">Koyu Tema</option>
              </select>
            </label>
          </div>
        </section>

        {/* ---- Güneş Paneli ve İstasyon Ayarları ---- */}
        <section className="card">
          <div className="card-head">
            <div>
              <h2>Güneş Paneli ve İstasyon Ayarları</h2>
              <p className="card-sub">Hesaplanan Watt gücü ve cihazın saha kayıt sıklığı</p>
            </div>
          </div>

          <div className="form">
            <label>
              İstasyon Veri Kayıt Sıklığı (Sahada Kaç Dakikada Bir Kaydetsin?)
              <select
                value={String(draft.measurement_interval_s)}
                onChange={(e) => set("measurement_interval_s", Number(e.target.value))}
              >
                <option value="300">5 dakikada bir (12 ay saha kullanımı — Önerilen)</option>
                <option value="900">15 dakikada bir</option>
                <option value="1800">30 dakikada bir</option>
                <option value="3600">1 saatte bir</option>
                <option value="60">1 dakikada bir (Kısa süreli test)</option>
              </select>
            </label>

            <label>
              Tek Panelin Gücü (Watt)
              <input
                type="number"
                step="5"
                value={draft.panel_power_w}
                onChange={(e) => set("panel_power_w", Number(e.target.value))}
              />
            </label>

            <label>
              Panel Sayısı (Adet)
              <input
                type="number"
                step="1"
                value={draft.panel_count}
                onChange={(e) => set("panel_count", Number(e.target.value))}
              />
              <span className="hint">
                Toplam sistem gücü: <b>{((draft.panel_power_w * draft.panel_count) / 1000).toFixed(2)} kW</b>
              </span>
            </label>

            <label>
              Soğutma Fanı Açılma Sıcaklığı (°C)
              <input
                type="number"
                step="1"
                value={draft.fan_on_c}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setDraft((d) =>
                    d ? { ...d, fan_on_c: v, fan_off_c: Math.min(d.fan_off_c, v - 3) } : d,
                  );
                }}
              />
            </label>

            <label>
              Gece Otomatik Uyku Modu
              <select
                value={draft.night_shutdown_enabled ? "1" : "0"}
                onChange={(e) => set("night_shutdown_enabled", e.target.value === "1")}
              >
                <option value="1">Açık (Gece pil tasarrufu için uyur)</option>
                <option value="0">Kapalı (24 saat açık kalır)</option>
              </select>
            </label>
          </div>
        </section>
      </div>

      <section className="card">
        <div className="row-gap" style={{ justifyContent: "space-between" }}>
          <label style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
            <span>Cihaz Şifresi:</span>
            <input
              type="password"
              value={token}
              onChange={(e) => setToken(e.target.value)}
              style={{ width: 160 }}
            />
          </label>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "Kaydediliyor…" : "💾 Ayarları Kaydet"}
          </button>
        </div>

        {msg && (
          <div className={`alert ${msg.ok ? "alert-ok" : "alert-bad"}`} style={{ marginTop: 12 }}>
            {msg.ok ? "✓ " : "⚠️ "}
            {msg.text}
          </div>
        )}
      </section>
    </form>
  );
}
