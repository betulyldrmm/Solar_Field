import { useEffect, useState } from "react";
import { api, type SystemConfigOut } from "./api";
import Energy from "./pages/Energy";
import Estimate from "./pages/Estimate";
import Events from "./pages/Events";
import Overview from "./pages/Overview";
import Settings from "./pages/Settings";

const TABS = [
  { id: "overview", label: "Genel bakış" },
  { id: "energy", label: "Enerji" },
  { id: "estimate", label: "Üretim tahmini" },
  { id: "settings", label: "Ayarlar" },
  { id: "events", label: "Olaylar" },
] as const;
type TabId = (typeof TABS)[number]["id"];

function tabFromHash(): TabId {
  const h = window.location.hash.slice(1);
  return (TABS.find((t) => t.id === h)?.id ?? "overview") as TabId;
}

export default function App() {
  const [tab, setTab] = useState<TabId>(tabFromHash);
  const [config, setConfig] = useState<SystemConfigOut | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);

  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    api.config().then(setConfig, (e: Error) => setConfigError(e.message));
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">☀️</span>
          <div>
            <h1>Solar Field Station</h1>
            <p>Saha ön ölçüm ve simülasyon istasyonu</p>
          </div>
        </div>
        {config && (
          <span className={`mode mode-${config.data_mode}`}>
            {config.data_mode === "simulation" ? "SİMÜLASYON" : "DONANIM"}
          </span>
        )}
      </header>

      <nav className="tabs">
        {TABS.map((t) => (
          <a key={t.id} href={`#${t.id}`} className={t.id === tab ? "active" : ""}>{t.label}</a>
        ))}
      </nav>

      <main>
        {configError && <div className="alert alert-bad">⚠️ Ayarlar alınamadı: {configError}</div>}
        {tab === "overview" && <Overview config={config} />}
        {tab === "energy" && <Energy />}
        {tab === "estimate" && <Estimate config={config} />}
        {tab === "settings" && <Settings config={config} onSaved={setConfig} />}
        {tab === "events" && <Events />}
      </main>
    </div>
  );
}
