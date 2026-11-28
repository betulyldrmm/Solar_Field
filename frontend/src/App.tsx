import { useEffect, useState } from "react";
import {
  api,
  type BluetoothInfo,
  type DataSource,
  type LiveTelemetryResponse,
  type SystemConfigOut,
} from "./api";
import { num, time } from "./format";
import Energy from "./pages/Energy";
import Estimate from "./pages/Estimate";
import Events from "./pages/Events";
import LiveInspector from "./pages/LiveInspector";
import Overview from "./pages/Overview";
import Settings from "./pages/Settings";
import ThermalLab from "./pages/ThermalLab";
import { usePolling } from "./usePolling";

const TABS = [
  { id: "overview", label: "⚡ Komuta Merkezi" },
  { id: "thermal", label: "🌡️ Isı & Sensörler" },
  { id: "live", label: "📡 Canlı Akış & Analiz" },
  { id: "energy", label: "☀️ Enerji & Işınım" },
  { id: "estimate", label: "📊 Üretim Tahmini" },
  { id: "settings", label: "⚙️ Ayarlar" },
  { id: "events", label: "📥 Veri İndir & Olaylar" },
] as const;
type TabId = (typeof TABS)[number]["id"];

function tabFromHash(): TabId {
  const h = window.location.hash.slice(1);
  return (TABS.find((t) => t.id === h)?.id ?? "overview") as TabId;
}

export default function App() {
  const [tab, setTab] = useState<TabId>(tabFromHash);
  const [config, setConfig] = useState<SystemConfigOut | null>(null);
  const [source, setSource] = useState<DataSource>("hardware");
  const [btInfo, setBtInfo] = useState<BluetoothInfo | null>(null);
  const [paused, setPaused] = useState(false);
  const [rateMs, setRateMs] = useState(1500);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [configError, setConfigError] = useState<string | null>(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);

  useEffect(() => {
    const onHash = () => setTab(tabFromHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    api.config().then(
      (c) => {
        setConfig(c);
        setSource(c.data_mode);
      },
      (e: Error) => setConfigError(e.message),
    );
  }, []);

  const btPoll = usePolling(api.bluetoothInfo, 10_000);
  useEffect(() => {
    if (btPoll.data) setBtInfo(btPoll.data);
  }, [btPoll.data]);

  const live = usePolling<LiveTelemetryResponse>(
    () => api.liveTelemetry(source, 150),
    paused ? 3_600_000 : rateMs,
    [source, paused, rateMs],
  );
  const lp = live.data?.latest ?? null;

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo">☀️</span>
          <div>
            <h1>Solar Field Station · UNO Q Mission Control</h1>
            <p>
              Çift İşlemcili (QRB2210 Linux + STM32U585 MCU) Canlı Güneş & Termal Ölçüm İstasyonu
            </p>
          </div>
        </div>

        <div className="topbar-actions">
          <div className="seg" title="Veri kaynağını seç (iki kaynak asla karışmaz)">
            <button
              type="button"
              className={source === "hardware" ? "active" : ""}
              onClick={() => setSource("hardware")}
            >
              🔌 DONANIM (MCU)
            </button>
            <button
              type="button"
              className={source === "simulation" ? "active" : ""}
              onClick={() => setSource("simulation")}
            >
              🧪 SİMÜLASYON
            </button>
          </div>

          <div className="seg" title="Canlı grafik yenileme hızı">
            {[
              { label: "1.5s", ms: 1500 },
              { label: "5s", ms: 5000 },
              { label: "15s", ms: 15000 },
            ].map((r) => (
              <button
                key={r.ms}
                type="button"
                className={!paused && rateMs === r.ms ? "active" : ""}
                onClick={() => {
                  setPaused(false);
                  setRateMs(r.ms);
                }}
              >
                {r.label}
              </button>
            ))}
            <button
              type="button"
              className={paused ? "active" : ""}
              onClick={() => setPaused((p) => !p)}
            >
              {paused ? "▶ Devam" : "⏸ Duraklat"}
            </button>
          </div>

          <button
            type="button"
            className="button"
            onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
            title="Tema değiştir"
          >
            {theme === "dark" ? "☀️ Açık" : "🌙 Koyu"}
          </button>

          <a className="button primary-btn" href={api.csvUrl(undefined, source)} download>
            ⬇ CSV
          </a>
          <a className="button" href={api.jsonUrl(undefined, source)} download>
            ⬇ JSON
          </a>
        </div>
      </header>

      <div className="live-ribbon">
        <div className="live-ribbon-left">
          <span className={`live-pill ${paused ? "paused" : ""}`}>
            <span className="pulse-dot" />
            {paused ? "DURAKLATILDI" : `CANLI AKIŞ · ${(rateMs / 1000).toFixed(1).replace(".0", "")} sn`}
          </span>
          <span>
            Paket <b>#{lp?.seq ?? "—"}</b> · Son: <b>{time(lp?.timestamp_utc)}</b>
          </span>
          <span>
            SoC Isı: <b>{num(lp?.mpu_temperature_c, 1, "°C")}</b>
          </span>
          <span>
            CPU: <b>%{num(lp?.cpu_pct, 0)}</b> · RAM: <b>%{num(lp?.mem_pct, 0)}</b>
          </span>
        </div>
        <div className="live-ribbon-right">
          <span>
            📶 BT PAN: <code>{btInfo?.pan_ip ?? "10.44.0.1"}</code>
            {btInfo?.connected_peers?.length
              ? ` (${btInfo.connected_peers.length} bağlı cihaz)`
              : " (Hazır)"}
          </span>
        </div>
      </div>

      <nav className="tabs">
        {TABS.map((t) => (
          <a key={t.id} href={`#${t.id}`} className={t.id === tab ? "active" : ""}>
            {t.label}
          </a>
        ))}
      </nav>

      <main>
        {configError && <div className="alert alert-bad">⚠️ Ayarlar alınamadı: {configError}</div>}
        {tab === "overview" && (
          <Overview config={config} source={source} btInfo={btInfo} live={live.data ?? null} />
        )}
        {tab === "thermal" && (
          <ThermalLab config={config} source={source} live={live.data ?? null} />
        )}
        {tab === "live" && (
          <LiveInspector config={config} source={source} btInfo={btInfo} live={live.data ?? null} />
        )}
        {tab === "energy" && <Energy source={source} />}
        {tab === "estimate" && <Estimate config={config} source={source} />}
        {tab === "settings" && <Settings config={config} onSaved={setConfig} />}
        {tab === "events" && <Events source={source} />}
      </main>
    </div>
  );
}
