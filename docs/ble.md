# BLE haberleşmesi (UNO Q ↔ bilgisayar)

**Durum:** Canlı ölçüm **ve geçmiş senkronizasyonu** yazıldı; Bluetooth olmadan
(sahte, %5–10 kayıplı bağlantıyla) uçtan uca test edildi. **UNO Q donanımında
henüz denenmedi.**

```text
UNO Q:  STM32 ─Bridge─▶ backend (SQLite/CSV) ◀─okur─ app.ble.peripheral
                                                        │ BLE
PC:     pc_receiver/ble_receiver.py ─yazar─▶ backend/data_ble (SQLite) ◀─ FastAPI ◀─ React arayüz
```

Bağlanınca önce **istasyonda biriken tüm kayıtlar** çekilir, sonra canlı
dinlemeye geçilir; her yeni canlı paket küçük bir artımlı senkronizasyon tetikler.

Ölçüm ve kayıt BLE'ye bağlı değildir: BLE süreci çökse ya da kimse bağlı
olmasa da backend kaydetmeye devam eder.

## GATT tanımı

| | UUID |
|---|---|
| Servis | `5f1d0001-3a6b-4c2e-9d6e-5f0a1c7b2e10` |
| Canlı ölçüm karakteristiği (read + notify) | `5f1d0002-3a6b-4c2e-9d6e-5f0a1c7b2e10` |
| Geçmiş istek (write) | `5f1d0003-3a6b-4c2e-9d6e-5f0a1c7b2e10` |
| Geçmiş veri (notify) | `5f1d0004-3a6b-4c2e-9d6e-5f0a1c7b2e10` |
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

## Geçmiş senkronizasyonu

Kod: `backend/app/ble/history.py` (protokol + cihaz tarafı), `backend/app/ble/client_sync.py` (PC tarafı).

1. PC isteği yazar: `since_id` (elindeki son kayıt no), en fazla kayıt sayısı, parça boyutu.
2. İstasyon `id > since_id` kayıtları bir **paket grubuna** koyar (her kayıt 40 bayt: zaman, boot_id,
   sıra no, V, I, 3 sıcaklık, pil V/I, fan, sensör durumu), sonuna **CRC32** ekler, MTU'ya göre parçalayıp bildirimle gönderir.
3. PC parçaları birleştirir. **Eksik parça varsa sadece onları** tekrar ister (RESEND, istekte en fazla 8 parça).
   CRC tutmazsa veya hiç cevap gelmezse isteğin tamamını birkaç kez tekrarlar.
4. Grup doğruysa kayıtlar PC veritabanına yazılır, `since_id` güncellenir (`ble_sync_state.json`); boş grup = güncel.

Güvenceler: aynı kayıt iki kez yazılmaz (`UNIQUE(boot_id, sequence)`); istasyon veritabanı sıfırlanırsa
PC bunu fark edip baştan senkronize eder; ilerleme her gruptan sonra kaydedilir (bağlantı koparsa kaldığı yerden devam).
MTU pazarlığı yoksa 20 baytlık parçalarla da çalışır (yavaş ama doğru).

## Çalıştırma

**Bluetooth'suz test (hemen):**
```powershell
python pc_receiver/ble_receiver.py --fake
```
İçeride 2 günlük geçmişi olan sahte bir istasyon kurar, %5 kayıplı bağlantı üzerinden senkronize eder, sonra canlı dinler.

**Alınan veriyi arayüzde görmek** (alıcı çalışırken, ayrı terminalde):
```powershell
cd backend
.venv\Scripts\activate
$env:SFS_DATA_DIR="data_ble"; $env:SFS_RUN_SIMULATOR="false"
python -m uvicorn app.main:app --port 8000
```
Sonra `frontend` içinde `npm run dev`. İstasyon gerçek sensörlerle çalışıyorsa ayrıca `$env:SFS_DATA_MODE="hardware"`.
`SFS_RUN_SIMULATOR=false` önemli: yoksa PC'deki backend kendi simülasyon verisini de aynı veritabanına yazar.

**İki bilgisayarla gerçek BLE testi (UNO Q gelmeden):** `bless` Windows'ta da çalıştığı için bir laptop istasyon rolünü oynayabilir.
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
python -m app.ble.peripheral --demo      # önce simülatörle (2 günlük geçmiş hazır gelir)
python -m app.ble.peripheral             # sonra backend'in DB'sinden (backend ayrı süreçte çalışır)
```

## Donanım doğrulama listesi (UNO Q üzerinde)

1. `bluetoothctl show` → adaptör var mı, `Powered: yes` mı. Değilse `bluetoothctl power on`.
2. `bluetoothctl --version` → BlueZ sürümü (≥ 5.50 önerilir).
3. `systemctl status bluetooth` → servis çalışıyor mu.
4. `--demo` ile peripheral'ı başlat; telefonda **nRF Connect** ile `SolarFieldStation` görünüyor mu, karakteristik okunuyor mu?
5. Çalışmazsa çıktıyı kaydet: `sudo btmon` (ayrı terminalde) + peripheral logu.
6. Menzil ve kopma testi: uzaklaş, geri gel; alıcı kendiliğinden yeniden bağlanmalı.

Bu adımlar geçmeden "BLE çalışıyor" denmez.

## Sonraki adımlar
* UNO Q'da donanım doğrulaması (yukarıdaki liste) ve gerçek menzil/hız ölçümü.
* Gerekirse BLE bağlantı parametrelerini hızlandırma (bağlantı aralığı), donanımda ölçülünce.
