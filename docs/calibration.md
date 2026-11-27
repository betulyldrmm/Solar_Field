# Referans panel ölçümü ve kalibrasyon

## Ölçüm yöntemi: kısa devre akımı (önerilen)

Referans panel (12 V 150 mA, 110×110 mm) **INA226 şöntü üzerinden kısa devre**
çalıştırılır. Isc ışınımla doğrusal değişir ve sıcaklıktan az etkilenir
(~+0,05 %/°C). Ucuz referans hücreler de bu prensiple çalışır.

```
Panel + ──► INA226 VIN+ ──[şönt 0,1 Ω]── VIN− ──► Panel −   (ve GND)
```
* 0,17 A'de şönt gerilimi ≈ 17 mV (INA226 sınırı 81,92 mV) — uygun.
* Yük direnci kullanılırsa (`reference_mode=resistive_load`) çalışma noktası
  ışınıma bağlı olarak kayar; bu modda ışınım hesaplanmaz.
* **DOĞRULAMA GEREKİYOR:** modüldeki şönt değeri (R100 = 0,1 Ω yazısı),
  INA226 GND'sinin panel eksi ucuyla ortak olması.

## Kalibrasyon (k katsayısı)

Etiketteki "150 mA" büyük ihtimalle Isc değil, maksimum güç akımıdır ve ucuz
panellerde ±%10–20 sapma normaldir. Bu yüzden `k` sahada belirlenir:

1. Referans paneli planlanan eğim/yönde, gölgesiz bir yere koy.
2. **Bulutsuz** 2–3 günde, güneş öğlesi ±1 saat aralığında Isc'yi kaydet.
3. Aynı saatler için PVGIS veya Open-Meteo'dan **eğik düzlem (POA)** ışınımını al.
   (Daha iyisi: bir pyranometre ya da kalibre referans hücre ödünç almak.)
4. `k = ortalama(Isc) / ortalama(G_POA)` → `PUT /api/config` ile
   `ref_isc_per_wm2_a = k`, `ref_calibrated = true`.
5. Kalibrasyon tarihini ve yöntemini `device_events`'e not düş.

Kalibrasyon yapılana kadar tüm ışınım ve üretim değerleri **"estimated"**
etiketiyle ve uyarıyla döner.

## 20 W camlı panel
Elde bulunan 20 W panel referans olarak değil, **çapraz kontrol** için
kullanılabilir: aynı anda Isc'si ölçülürse iki panelin oranı günler boyunca
sabit kalmalı; kalmıyorsa gölge/kirlenme/bağlantı sorunu vardır.
