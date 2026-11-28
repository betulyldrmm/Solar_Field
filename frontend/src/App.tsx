import { useEffect, useState } from "react";
import { api, type DataSource, type SystemConfigOut } from "./api";
import Overview from "./pages/Overview";
import Settings from "./pages/Settings";

export default function App() {
  const [tab, setTab] = useState<"data" | "settings">("data");
  const [config, setConfig] = useState<SystemConfigOut | null>(null);
  const [source, setSource] = useState<DataSource>("simulation");
  const [refreshSec, setRefreshSec] = useState<number>(0); // 0 = kapalı (sadece açıldığında / tarih seçildiğinde)
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  useEffect(() => {
    api
      .config()
      .then(async (cfg) => {
        setConfig(cfg);
        // Eğer sahada gerçek panel/güç verisi varsa donanımı seç, yoksa 12 aylık saha verisini göster
        try {
          const hw = await api.latest("hardware");
          if (hw.panel_temperature_c !== null || hw.current_a !== null) {
            setSource("hardware");
          } else {
            setSource("simulation");
          }
        } catch {
          setSource("simulation");
        }
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-icon">☀️</div>
          <div>
            <h1>Güneş Ölçüm İstasyonu</h1>
            <p>12 Aylık Saha Elektrik Üretimi ve Panel Sıcaklığı Takibi</p>
          </div>
        </div>

        <nav className="nav-pills">
          <button
            type="button"
            className={tab === "data" ? "active" : ""}
            onClick={() => setTab("data")}
          >
            📊 Güneş Verileri
          </button>
          <button
            type="button"
            className={tab === "settings" ? "active" : ""}
            onClick={() => setTab("settings")}
          >
            ⚙️ Ayarlar
          </button>
        </nav>
      </header>

      {error && <div className="alert alert-bad" style={{ marginBottom: 16 }}>⚠️ {error}</div>}

      <main>
        {tab === "data" ? (
          <Overview config={config} source={source} refreshSec={refreshSec} />
        ) : (
          <Settings
            config={config}
            onSaved={setConfig}
            source={source}
            onSourceChange={setSource}
            refreshSec={refreshSec}
            onRefreshSecChange={setRefreshSec}
            theme={theme}
            onThemeChange={setTheme}
          />
        )}
      </main>
    </div>
  );
}
