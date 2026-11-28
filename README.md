# Solar Field Station

**Güneş Enerjisi Sistemleri İçin Bağımsız Saha Ön Ölçüm ve Simülasyon İstasyonu**
Arduino UNO Q tabanlı, kendi paneli ve piliyle çalışan, kurulum noktasında
referans panelle ölçüm yapıp planlanan PV sisteminin günlük üretimini tahmin
eden cihaz.

> **Durum:** Phase 2 tamam (backend + simülasyon, 52 test geçiyor).
> Firmware ve arayüz henüz yok. Ayrıntı: [`docs/status.md`](docs/status.md)

## Hızlı başlangıç (simülasyon modu)

```bash
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

python scripts/seed_simulation.py --days 3             # 3 günlük sentetik geçmiş
SFS_API_TOKEN=demo uvicorn app.main:app --host 127.0.0.1 --port 8000
```
Tarayıcı: <http://127.0.0.1:8000/docs> — tüm endpoint'leri buradan deneyebilirsin.

Windows PowerShell'de ortam değişkeni: `$env:SFS_API_TOKEN="demo"`.

## Testler

```bash
cd backend
pytest -q        # 52 test
ruff check .
```

## Proje yapısı

```text
backend/
  app/
    config.py               ortam ayarları (SFS_*)
    models/schemas.py       Pydantic modelleri (birim son ekli alanlar)
    services/
      validator.py          STM32 mesaj doğrulama (schema v1)
      ingest.py             doğrula → SQLite → CSV
      energy_calculator.py  enerji, ışınım, üretim tahmini
      battery_monitor.py    gerilimden tahmini SoC
      solar_position.py     güneş yüksekliği (gece/gündüz)
      bridge_service.py     simülatör döngüsü + UNO Q Bridge adaptörü
    simulation/simulator.py sentetik, fiziksel olarak makul veri
    storage/                database.py (SQLite), csv_logger.py
    api/routes.py           FastAPI endpoint'leri
  scripts/seed_simulation.py
  tests/
docs/                       mimari, enerji modeli, kalibrasyon, API, durum
firmware/  frontend/        sonraki aşamalar
```

## Temel ilkeler
- **Simülasyon ve gerçek veri karışmaz:** her satır `data_source` taşır, API
  yalnızca aktif moddaki satırları gösterir.
- **Hatalı ölçüm gerçek veri gibi kaydedilmez:** alan `null` olur, hata ayrıca loglanır.
- **Boşluklar üretim sayılmaz.**
- **Tahmindeki her değer etiketli:** `measured / assumed / calculated / estimated`.
- **Kalibrasyonsuz referans panel = "estimated"** + uyarı ([kalibrasyon](docs/calibration.md)).
- Varsayılan olarak yalnızca `127.0.0.1`'e açık; ayar değişikliği token ister.

## Enerji yöntemi (özet)
`E_gün = kWp × H_POA × f_T × PR` — H_POA referans panelin kısa devre akımından,
f_T ölçülen panel sıcaklığından. Ayrıntı: [`docs/energy-model.md`](docs/energy-model.md).

## Bilinen sınırlamalar
- Birkaç günlük veri yıllık üretimi temsil etmez.
- Referans panel kalibre edilmeden sonuç ±%20+ hatalı olabilir.
- UNO Q Bridge API'si ve RTC bağlantısı henüz donanımda doğrulanmadı.
- BLE (canlı ölçüm, adım 1) yazıldı ama UNO Q'da denenmedi: [`docs/ble.md`](docs/ble.md).
- Güç sistemi (CN3791, boost dönüştürücü, fan sürücüsü) tasarım kararı bekliyor.

## Yol haritası
Phase 3 firmware → Phase 4 React → Phase 5 kalibrasyon + (veri yeterse) ML →
Phase 6 saha testi (48 saat, ardından uzun süreli).
