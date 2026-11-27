# API

Taban adres: `http://127.0.0.1:8000/api` · Etkileşimli doküman: `/docs` (FastAPI).
Tüm sayısal alanların adı birimini taşır (`_v`, `_a`, `_w`, `_wh`, `_kwh`, `_c`, `_s`).
Tarih parametreleri saat dilimi içermeli (`2026-11-26T00:00:00Z`), yoksa 422.

| Metot | Yol | Açıklama | Hata kodları |
|---|---|---|---|
| GET | `/health` | `status`, `db_ok`, sürüm | – |
| GET | `/device/status` | online/stale/offline, son ölçüm yaşı, sensör durumları, fan, pil (gerilimden **tahmini** SoC) | – |
| GET | `/measurements/latest` | Son ölçüm | 404 veri yok |
| GET | `/measurements/history?start&end&limit&offset&order` | Sayfalı geçmiş (`limit` ≤ 5000) | 422 |
| GET | `/measurements/export.csv?start&end` | CSV indirme | 413 > 200k satır, 422 |
| GET | `/energy/daily?days=7` | Günlük referans enerji, ışınım, tamlık | – |
| GET | `/energy/summary` | Bugün, tam gün sayısı, ortalama ışınım, kalibrasyon durumu | – |
| POST | `/simulation/estimate` | Planlanan sistem üretim tahmini (etiketli) | 422 |
| GET | `/config` | Sistem ayarları + `installed_kwp`, `data_mode` | – |
| PUT | `/config` | Ayar değiştir — **`X-API-Token` başlığı zorunlu** | 401, 403, 422 |
| GET | `/events?limit=100` | Cihaz olayları + sensör hataları | – |

## Örnek: üretim tahmini

```http
POST /api/simulation/estimate
{"panel_power_w": 450, "panel_count": 8, "performance_ratio": 0.8}
```
```json
{
  "method": "measured_reference",
  "installed_kwp": 3.6,
  "daily_energy_kwh": 5.256,
  "per_day": [{"date": "2026-11-24", "insolation_kwh_m2": 1.7924,
               "temperature_factor": 1.0109, "energy_kwh": 5.218}],
  "inputs": {
    "panel_power_w": {"value": 450, "unit": "W", "kind": "assumed"},
    "installed_kwp": {"value": 3.6, "unit": "kWp", "kind": "calculated"},
    "insolation_kwh_m2_mean": {"value": 1.807, "unit": "kWh/m²/day", "kind": "estimated"},
    "daily_energy_kwh": {"value": 5.256, "unit": "kWh/day", "kind": "estimated"}
  },
  "calibration_status": "uncalibrated",
  "data_source": "simulation",
  "warnings": ["SİMÜLASYON VERİSİ: ...", "Referans panel kalibre edilmedi: ...", "..."]
}
```
(Yukarıdaki değerler Kasım ayı simülasyon verisinden alınmış gerçek bir
çıktıdır; gerçek saha ölçümü değildir.)

`method`: `auto` (varsayılan; tam ölçüm günü varsa ölçümlü, yoksa
`peak_sun_hours` ile teorik), `measured_reference`, `theoretical_psh`.

## Güvenlik
* Varsayılan bağlanma adresi `127.0.0.1`. Yerel ağdan erişim için bilinçli
  olarak `SFS_HOST=0.0.0.0` verilmeli; cihaz internete açılmamalı.
* `SFS_API_TOKEN` boşsa `PUT /config` tamamen kapalıdır (403).
* Token sabit zamanlı karşılaştırılır; CORS yalnızca `SFS_CORS_ORIGINS`.
