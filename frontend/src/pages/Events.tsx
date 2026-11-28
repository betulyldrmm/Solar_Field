import { api } from "../api";
import { Card, Empty, ErrorBox } from "../components/ui";
import { dateTime } from "../format";
import { usePolling } from "../usePolling";

export default function Events() {
  const ev = usePolling(() => api.events(200), 60_000);
  const since7d = new Date(Date.now() - 7 * 86400_000).toISOString();

  return (
    <div className="stack">
      <ErrorBox message={ev.error} />
      <Card title="Veri dışa aktarma">
        <div className="row-gap">
          <a className="button" href={api.csvUrl(since7d)} download>Son 7 gün (CSV)</a>
          <a className="button" href={api.csvUrl()} download>Tüm veri (CSV)</a>
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
