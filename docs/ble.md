# BLE haberleşmesi (UNO Q ↔ bilgisayar)

**Durum:** Adım 1 (canlı ölçüm) yazıldı ve bilgisayarda Bluetooth olmadan test
edildi. **UNO Q donanımında henüz denenmedi.** Geçmiş veri senkronizasyonu ve
FastAPI/React'e aktarım sonraki adımlar.

```text
STM32 ─Bridge─▶ Linux: backend (SQLite/CSV)  ◀─okur─  app.ble.peripheral ─BLE notify─▶ PC: pc_receiver/ble_receiver.py
```

Ölçüm ve kayıt BLE'ye bağlı değildir: BLE süreci çökse ya da kimse bağlı
olmasa da backend kaydetmeye devam eder.

## GATT tanımı

| | UUID |
|---|---|
| Servis | `5f1d0001-3a6b-4c2e-9d6e-5f0a1c7b2e10` |
| Canlı ölçüm karakteristiği (read + notify) | `5f1d0002-3a6b-4c2e-9d6e-5f0a1c7b2e10` |
| Yayın adı | `SolarFieldStation` |

## Canlı paket v1 — 20 bayt, little-endian

JSON yerine sabit 20 baytlık ikili paket kullanılıyor: MTU pazarlığı olmasa
bile tek bildirime sığar, parçalama/sıralama gerekmez.

| Ofset | Tip | Alan | Birim |
|---|---|---|---|
| 0 | u8 | sürüm | = 1 |
| 1 | u8 | bayraklar | bit0 V, bit1 I, bit2 T_panel, bit3 pil, bit4 fan geçerli, bit5 fan açık, bit6 simülasyon |
| 2 | u16 | sıra no | mod 65536 |
| 4 | u32 | zaman | Unix saniye (UTC) |
| 8 | i32 | gerilim | µV |
| 12 | i32 | akım | µA |
| 16 | i16 | panel sıcaklığı | 0,01 °C |
| 18 | u16 | pil gerilimi | mV |

Bayrağı 0 olan değer `None` olarak çözülür, asla 0 olarak değil. Aralık
dışındaki değer kırpılmaz, geçersiz gönderilir. Güç gönderilmez; alıcı V×I
hesaplar ve `power_w_calculated` olarak etiketler. Kodu: `backend/app/ble/protocol.py`.

## Çalıştırma

**Bluetooth'suz test (hemen):**
```bash
python pc_receiver/ble_receiver.py --fake
```

**İki bilgisayarla gerçek BLE testi (UNO Q gelmeden):** `bless` Windows'ta da
çalıştığı için bir laptop istasyon rolünü oynayabilir.
```bash
# Laptop A (istasyon gibi)
cd backend && pip install -r requirements-ble.txt
python -m app.ble.peripheral --demo
# Laptop B (alıcı)
pip install -r pc_receiver/requirements.txt
python pc_receiver/ble_receiver.py --scan
python pc_receiver/ble_receiver.py
```

**UNO Q'da:**
```bash
cd backend && python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-ble.txt
python -m app.ble.peripheral --demo      # önce simülatörle
python -m app.ble.peripheral             # sonra backend'in DB'sinden (backend ayrı süreçte çalışır)
```
Alıcı gelen her paketi ekrana basar ve `pc_receiver/received/live_<tarih>_<kaynak>.csv` dosyasına ekler.

## Donanım doğrulama listesi (UNO Q üzerinde)

1. `bluetoothctl show` → adaptör var mı, `Powered: yes` mı. Değilse `bluetoothctl power on`.
2. `bluetoothctl --version` → BlueZ sürümü (≥ 5.50 önerilir).
3. `systemctl status bluetooth` → servis çalışıyor mu.
4. `--demo` ile peripheral'ı başlat; telefonda **nRF Connect** ile `SolarFieldStation` görünüyor mu, karakteristik okunuyor mu?
5. Çalışmazsa çıktıyı kaydet: `sudo btmon` (ayrı terminalde) + peripheral logu.
6. Menzil ve kopma testi: uzaklaş, geri gel; alıcı kendiliğinden yeniden bağlanmalı.

Bu adımlar geçmeden "BLE çalışıyor" denmez.

## Sonraki adım: geçmiş senkronizasyonu (tasarım, henüz kod yok)

* Yeni karakteristik `...0003` (write): PC istek yazar → `{since_sequence | since_time, max_records}`.
* Yeni karakteristik `...0004` (notify): kayıtlar, her biri tek pakette, başında
  `u32 kayıt_no` + `u16 toplam` + `u16 CRC16`.
* PC eksik kayıt numaralarını tespit edip yeniden ister; CRC tutmayan paket atılır.
* Bağlantı kurulunca sıra: önce geçmiş, sonra canlı dinleme.
* PC tarafı aldığı veriyi kendi FastAPI'sine (mevcut `IngestService`) verir → React grafikleri.
