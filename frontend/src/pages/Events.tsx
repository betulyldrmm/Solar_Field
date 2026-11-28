import { api, type DataSource } from "../api";
import { Card, Empty, ErrorBox } from "../components/ui";
import { dateTime } from "../format";
import { usePolling } from "../usePolling";

export default function Events({ source }: { source: DataSource }) {
  const ev = usePolling(() => api.events(200), 60_000);
  const since24h = new Date(Date.now() - 86400_000).toISOString();
  const since7d = new Date(Date.now() - 7 * 86400_000).toISOString();

  return (
    <div className="stack">
      <ErrorBox message={ev.error} />
      <Card title={`Veri dışa aktarma (${source === "hardware" ? "Donanım / MCU" : "Simülasyon"})`}>
        <div className="row-gap">
          <a className="button primary-btn" href={api.csvUrl(since24h, source)} download>Son 24 saat (CSV)</a>
          <a className="button" href={api.csvUrl(since7d, source)} download>Son 7 gün (CSV)</a>
          <a className="button" href={api.csvUrl(undefined, source)} download>Tüm veri (CSV + Ek Sensörler)</a>
          <a className="button" href={api.jsonUrl(undefined, source)} download>Tüm veri (JSON)</a>
        </div>
      </Card>
      <div className="grid-2">
        <Card title="Cihaz olayları">
          {!ev.data?.events.length ? <Empty>Olay yok</Empty> : (
            <div className="table-wrap tall">
              <table>
                <thead><tr><th>Zaman</th><th>Seviye</th><th>Olay</th><th>Mesaj</th></tr></thead>
                <tbody>
                  {ev.data.events.map((e) => (
                    <tr key={e.id}>
                      <td className="nowrap">{dateTime(e.timestamp_utc)}</td>
                      <td><span className={`badge lvl-${e.level}`}>{e.level}</span></td>
                      <td><code>{e.kind}</code></td>
                      <td>{e.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <Card title="Sensör hataları">
          {!ev.data?.sensor_errors.length ? <Empty>Sensör hatası yok 👍</Empty> : (
            <div className="table-wrap tall">
              <table>
                <thead><tr><th>Zaman</th><th>Sensör</th><th>Hata</th><th>Sıra</th></tr></thead>
                <tbody>
                  {ev.data.sensor_errors.map((s) => (
                    <tr key={s.id}>
                      <td className="nowrap">{dateTime(s.timestamp_utc)}</td>
                      <td><code>{s.sensor}</code></td>
                      <td>{s.error}</td>
                      <td>{s.measurement_sequence ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
