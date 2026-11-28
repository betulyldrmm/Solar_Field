#  Solar Field Station

**Güneş Enerjisi Sistemleri İçin Bağımsız Saha Ön Ölçüm ve Simülasyon İstasyonu**

GES kurulması planlanan bir noktaya bırakılan, kendi paneli ve piliyle çalışan,
Arduino UNO Q tabanlı bir ölçüm istasyonu. Küçük bir **referans panelle** sahadaki
gerçek güneş ışınımını ölçer ve kullanıcının planladığı sistemin (ör. 8 × 450 W)
o noktada **günde kaç kWh üreteceğini** tahmin eder. Amaç: katalog değeri yerine
sahada ölçülmüş veriye dayanan bir ön fizibilite.

> **Durum:** Backend + simülasyon tamam, **67 test geçiyor**. React arayüzü
> çalışıyor (simülasyon verisiyle). BLE canlı veri aktarımı yazıldı (donanımda
> denenmedi). STM32 firmware henüz yok. Ayrıntı: [`docs/status.md`](docs/status.md)

---

## İçindekiler
1. [Nasıl çalışır?](#nasıl-çalışır)
2. [Donanım](#donanım)
3. [Yazılım mimarisi](#yazılım-mimarisi)
4. [Hızlı başlangıç (simülasyon)](#hızlı-başlangıç-simülasyon)
5. [Bluetooth (BLE) ile veri alma](#bluetooth-ble-ile-veri-alma)
6. [Enerji ve tahmin yöntemi](#enerji-ve-tahmin-yöntemi)
7. [Temel ilkeler](#temel-ilkeler)
8. [Proje yapısı](#proje-yapısı)
9. [Yol haritası ve bilinen sınırlamalar](#yol-haritası-ve-bilinen-sınırlamalar)

---

## Nasıl çalışır?

1. İstasyon sahaya bırakılır; 20 W panelle kendi 3S pilini şarj eder.
2. **Gündüz** çalışır: gün doğumundan 30 dk önce açılır, gün batımından 30 dk sonra kapanır.
3. STM32 sensörleri sık okur, **5 dakikada bir** ortalamayı Linux tarafına gönderir.
4. Linux tarafı (Python) veriyi doğrular, **SQLite + günlük CSV**'ye kaydeder, ışınım ve enerjiyi hesaplar.
5. Bilgisayarla yaklaşıldığında veriler **Bluetooth (BLE)** üzerinden alınır; grafikler bilgisayarda gösterilir.
6. Planlanan sistem bilgisi girilir → günlük üretim tahmini (her değer etiketli: ölçülen / varsayılan / hesaplanan / tahmini).

İstasyon bilgisayara **bağımlı değildir**: bağlantı yokken de ölçmeye ve kaydetmeye devam eder.



## Yazılım mimarisi

```text
Sensörler ─▶ STM32 (Arduino C++) ──Bridge/JSON──▶ UNO Q Linux (Python)
                                                  ├─ Doğrulayıcı (schema v1)
                                                  ├─ SQLite (WAL) + günlük CSV
                                                  ├─ Enerji / ışınım / tahmin
                                                  ├─ Gece kapanma takvimi
                                                  ├─ FastAPI (/api/*)
                                                  └─ BLE GATT sunucusu ──Bluetooth──▶ Bilgisayar (Bleak alıcı)
```

- Simülatör ve gerçek donanım **aynı kayıt yolunu** kullanır; bugün yazılan kod donanım gelince değişmeden çalışır.
- Zamanı Linux verir (RTC/NTP); saat senkron değilse kayıt `clock_unsynced` olarak işaretlenir.
- Fan kontrolü STM32'de yereldir; Linux çökse bile çalışır.
- Ayrıntı: [`docs/architecture.md`](docs/architecture.md) · API: [`docs/api.md`](docs/api.md)

## Hızlı başlangıç (simülasyon)

Donanım olmadan, tamamen sentetik veriyle çalışır. Python **3.11+** gerekir.

**Windows (PowerShell):**
```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt

python scripts/seed_simulation.py --days 3      # 3 günlük sentetik geçmiş (5 dk, sadece gündüz)
$env:SFS_API_TOKEN="demo"
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

**Linux / macOS / UNO Q:**
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
python scripts/seed_simulation.py --days 3
SFS_API_TOKEN=demo uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Tarayıcıda <http://127.0.0.1:8000/docs> adresinden tüm endpoint'leri deneyebilirsin.

**Arayüz (ikinci terminal, Node.js 18+):**
```powershell
cd frontend
npm install
npm run dev        # http://localhost:5173
```
Sekmeler: Genel bakış · Enerji · Üretim tahmini · Ayarlar · Olaylar. Ayrıntı: [`frontend/README.md`](frontend/README.md)
Ayarlar ortam değişkenleriyle yapılır (`SFS_*`): örnek için [`.env.example`](.env.example).

**Testler:**
```bash
cd backend
pytest -q        # 67 test
ruff check .
```

## Bluetooth (BLE) ile veri alma

UNO Q `SolarFieldStation` adıyla yayın yapar; bilgisayar bağlanınca canlı
ölçümleri **20 baytlık** bildirimlerle alır (parçalamaya gerek kalmaz). Alınan
veriler ekrana basılır ve `pc_receiver/received/` altına CSV olarak eklenir.

```powershell
# Bilgisayar tarafı
pip install -r pc_receiver/requirements.txt
python pc_receiver/ble_receiver.py --fake     # Bluetooth olmadan test
python pc_receiver/ble_receiver.py --scan     # yakındaki cihazları listele
python pc_receiver/ble_receiver.py            # istasyona bağlan ve dinle
```
```bash
# UNO Q tarafı (backend/ içinde)
pip install -r requirements-ble.txt
python -m app.ble.peripheral --demo           # kendi simülatörüyle
python -m app.ble.peripheral                  # backend'in veritabanından
```

UNO Q gelmeden iki laptopla da denenebilir (biri `--demo` ile istasyon rolünde).
Protokol, UUID'ler ve donanım doğrulama listesi: [`docs/ble.md`](docs/ble.md).
Geçmiş veri senkronizasyonu tasarlandı, henüz yazılmadı.

## Enerji ve tahmin yöntemi

```text
Işınım:   G = Isc / ( k · (1 + α·(T_panel − 25)) )        [W/m²]
Günlük:   H = Σ G·Δt                                      [kWh/m²]
Üretim:   E_gün = kWp × H × f_T × PR                      [kWh]
          f_T = 1 + γ·(T_w − 25),  PR ≈ 0,80 (inverter, kablo, kirlilik)
```

- `k`: referans panelin ışınım başına kısa devre akımı. Varsayılan değer etiketten gelir ve **kalibrasyon gerektirir**: bulutsuz 2–3 günde öğle ölçümleri PVGIS / Open-Meteo ile karşılaştırılır ([`docs/calibration.md`](docs/calibration.md)).
- Aydınlık sürenin %90'ından azı ölçülen günler **eksik** sayılır ve tahmine girmez; gece boşlukları tamlığı bozmaz.
- Saha verisi yoksa yedek yöntem: teorik tepe güneş saati.
- Ayrıntı: [`docs/energy-model.md`](docs/energy-model.md)

## Temel ilkeler

- **Simülasyon ve gerçek veri karışmaz:** her satır `data_source` taşır.
- **Hatalı ölçüm gerçek veri gibi kaydedilmez:** değer `null` olur, hata ayrıca loglanır (asla 0 yazılmaz).
- **Boşluklar üretim sayılmaz.**
- **Her değer etiketli:** `measured / assumed / calculated / estimated`.
- **Kalibrasyonsuz referans panel → "estimated"** + uyarı.
- **Güvenli varsayılanlar:** API yalnızca `127.0.0.1`'e açık; ayar değişikliği token ister.

## Proje yapısı

```text
backend/
  app/
    api/routes.py              FastAPI endpoint'leri
    ble/                       BLE protokolü (protocol.py) + UNO Q GATT sunucusu (peripheral.py)
    models/schemas.py          Pydantic modelleri (alan adlarında birim: _v, _a, _c, _s ...)
    services/
      validator.py             STM32 mesaj doğrulama (schema v1)
      ingest.py                doğrula → SQLite → CSV
      energy_calculator.py     enerji, ışınım, üretim tahmini
      power_schedule.py        gün doğumu/batımı, gece kapanma takvimi
      battery_monitor.py       gerilimden tahmini pil yüzdesi (3S)
      solar_position.py        güneş yüksekliği (NOAA)
      bridge_service.py        simülatör döngüsü + UNO Q Bridge adaptörü
    simulation/simulator.py    sentetik ama fiziksel olarak makul veri
    storage/                   database.py (SQLite), csv_logger.py
  scripts/seed_simulation.py   hızlı sentetik geçmiş üretimi
  tests/                       67 test
pc_receiver/                   bilgisayar tarafı BLE alıcısı (Bleak)
docs/                          mimari, enerji modeli, kalibrasyon, API, BLE, durum
firmware/                      STM32 (sonraki aşama)
frontend/                      React + TypeScript + Vite arayüz (Recharts grafikleri)
```

## Yol haritası ve bilinen sınırlamalar

| Aşama | İçerik | Durum |
|---|---|---|
| Phase 1–2 | Mimari, backend, simülasyon, enerji modeli | ✅ |
| Phase 3 | STM32 firmware + BLE (canlı ✅, geçmiş senkronizasyonu ⏳) | 🔄 |
| Phase 4 | React + TypeScript arayüz (grafikler) | ✅ ilk sürüm |
| Phase 5 | Kalibrasyon, yeterli veri olursa ML | ⏳ |
| Phase 6 | Saha testi: önce 48 saat, sonra uzun süreli | ⏳ |


