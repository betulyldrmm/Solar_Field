# Mimari

```text
STM32U585 (Arduino C++)                 Linux (Qualcomm, Python)
┌──────────────────────────┐  Bridge   ┌──────────────────────────────────────────┐
│ INA226 (ref) / INA226 (pil)│ ──JSON──▶ │ HardwareBridgeAdapter ─┐                 │
│ DS18B20 ×3, fan, LCD, enc. │ ◀─config─ │ SimulationRunner ──────┼▶ IngestService  │
└──────────────────────────┘           │                        │   ├ validator    │
                                       │                        │   ├ SQLite (WAL) │
                                       │                        │   └ CSV (günlük) │
                                       │ energy_calculator ◀────┘                 │
                                       │ FastAPI  /api/*  ──▶ React (Phase 4)     │
                                       └──────────────────────────────────────────┘
```

* Donanım okuma, iş mantığı ve API birbirinden ayrı: simülatör ve gerçek
  Bridge **aynı** `IngestService.ingest()` yolunu kullanır.
* Her satır `data_source` (`simulation` | `hardware`) taşır. API yalnızca
  `SFS_DATA_MODE` ile eşleşen satırları gösterir — iki kaynak asla karışmaz.
* Fan kontrolü STM32'de yereldir: Linux/Bridge çökse bile çalışır. 12 V fan 3S
  pakete doğrudan bağlıdır, STM32 lojik seviyeli MOSFET ile anahtarlar.
* **Kayıt aralığı 5 dk** (`measurement_interval_s=300`): STM32 kısa aralıklarla
  ölçer, 5 dk'lık ortalamayı tek mesaj olarak gönderir (anlık bulut gölgesi kaçmasın).
* **Gece kapanma:** `app/services/power_schedule.py` gün doğumu/batımını hesaplar;
  istasyon gün batımı + 30 dk kapanır, gün doğumu − 30 dk açılır. Her açılış
  STM32 için yeni `boot_id` demektir. Gece boşluğu gün tamlığını bozmaz.

## STM32 → Linux mesajı (schema v1)

```json
{
  "schema_version": 1,
  "boot_id": "a1b2c3d4",
  "sequence": 101,
  "uptime_ms": 505000,
  "voltage_v": 0.03, "current_a": 0.139, "power_w": 0.0042,
  "panel_temperature_c": 35.2, "ambient_temperature_c": 27.4, "case_temperature_c": 31.0,
  "battery_voltage_v": 3.92, "battery_current_a": -0.4,
  "fan_state": false,
  "sensor_status": "OK",
  "sensors": {"ina_ref": "OK", "ina_batt": "OK", "t_panel": "OK",
              "t_ambient": "DISCONNECTED", "t_case": "OK"}
}
```

| Alan | Kural |
|---|---|
| `boot_id` | STM32 her açılışta rastgele üretir. `(boot_id, sequence)` tekildir → tekrar gelen mesaj kaydedilmez. |
| `sequence` | Her ölçümde +1. Atlama → `sequence_gap` olayı. |
| Sensör alanı `null` | Ölçüm yok. Firmware bozuk değeri göndermemeli; yine de -127 / 85.0 gelirse doğrulayıcı atar. |
| `sensors.*` | `OK` dışındaki her durum o alanı `null` yapar ve `sensor_errors` tablosuna yazılır. `NOT_INSTALLED` sessizdir. |

## Zaman damgası

**Zamanı Linux verir** (alındığı an, UTC). STM32 yalnızca `uptime_ms` ve
`sequence` gönderir. İnternetsiz çalışma için:

1. DS3231 RTC (I²C) Linux'a `rtc-ds1307` sürücüsüyle `/dev/rtcX` olarak
   tanıtılır, açılışta `hwclock -s` ile sistem saati RTC'den alınır.
   **DOĞRULAMA GEREKİYOR:** UNO Q Linux imajında bu sürücünün ve RTC'nin
   bağlanabileceği I²C hattının varlığı. Olmazsa RTC STM32'ye bağlanır ve
   STM32 zamanı mesaja ekler (schema v2).
2. İnternet varsa NTP, RTC'yi düzeltir.
3. Sistem saati 2025'ten önceyse satır `clock_unsynced` bayrağı alır.

Sepetteki **DS1302** yerine **DS3231** önerilir (I²C, ±2 ppm, alarm çıkışı → gece kapatma).

## Kayıt güvenliği

| Risk | Önlem |
|---|---|
| Ani elektrik kesilmesi | SQLite WAL + `synchronous=NORMAL` (dosya bozulmaz, en fazla son birkaç kayıt kaybolur); CSV her 12 satırda `fsync` |
| Tekrarlanan kayıt | `UNIQUE(boot_id, sequence)` |
| Disk dolması | Boş alan `SFS_MIN_FREE_DISK_MB` altına inerse CSV yazımı durur, saatte bir `disk_low` olayı |
| Hatalı sensör | Doğrulayıcı → alan `null` + `sensor_errors` |
| Zaman sorunu | `clock_unsynced` bayrağı |

Depolama: UNO Q'nun dahili eMMC'si fazlasıyla yeterli (5 dakikada bir kayıt,
yalnızca gündüz → yılda ~1 MB SQLite). Her CSV satırı `fsync` edilir. Ayrı microSD modülü **gerekli değil**; ancak veriyi cihazdan sökerek
almak isteniyorsa opsiyonel.

## Kaynak tüketimi

Backend saf Python + stdlib (sqlite3, csv). NumPy/Pandas/scikit-learn **şimdilik
eklenmedi**: çekirdek hesaplar bunlara ihtiyaç duymuyor; ML aşamasında
(yeterli gerçek veri olunca) eklenecek.
