# Durum raporu — Phase 2 (backend + simülasyon)

Tarih: 2026-11-26

## Gerçekten çalışan (bilgisayarda test edildi)
- Doğrulayıcı (schema v1), sensör hata yönetimi, tekrar/atlama tespiti
- SQLite (WAL, migrasyon), günlük CSV, CSV dışa aktarma
- Enerji integrasyonu, Isc → ışınım, gün tamlığı, üretim tahmini (iki yöntem)
- FastAPI: 11 endpoint, token korumalı ayar değişikliği
- Simülatör + canlı arka plan döngüsü, geçmiş veri üretme betiği
- **67/67 pytest testi geçti, ruff temiz** (Python 3.13)

## Simülasyon olan
- Tüm ölçüm verisi (`data_source=simulation`). Simülatörde referans panelin
  gerçek katsayısı bilerek etiketten %7 düşük → kalibrasyonun etkisi görülebilir.

## Donanım doğrulaması bekleyen
| Konu | Durum |
|---|---|
| CN3791 çıkışı 3S için 12,6 V olmalı (12 V sürüm) | **Pil bağlanmadan önce ölçülmeli** |
| 3S (9–12,6 V) → 5 V güç yolu | **Karar: 3S.** XL6009 boost gerekmez; 12,6 V → 5 V **buck** (UNO Q için ≥ 3 A) seçilmeli |
| 12 V fan sürücüsü | **Karar: fan 3S pakete doğrudan bağlı** (9–12,6 V'ta çalışır). IRFZ44N 3,3 V ile tam açılmaz → lojik seviyeli MOSFET (ör. IRLZ44N) + fana ters diyot (1N400x) |
| UNO Q Bridge API (App Lab) | DOĞRULAMA GEREKİYOR — `bridge_service.py` |
| DS3231'in Linux'a mı STM32'ye mi bağlanacağı | DOĞRULAMA GEREKİYOR |
| Referans panel kısa devre bağlantısı, INA226 şönt | DOĞRULAMA GEREKİYOR |
| Enerji bütçesi | **Karar: gece kapanma** (gün batımı +30 dk kapanır, gün doğumu −30 dk açılır). Kodda takvim hazır; kapatma/açma devresi (DS3231 alarmı → güç anahtarı) DOĞRULAMA GEREKİYOR |
| Kayıt aralığı | **Karar: 5 dakikada bir kayıt.** STM32 bu sürede sık örnekleyip **ortalamayı** göndermeli (firmware) |

## Henüz başlanmayan
- Phase 3: STM32 firmware · ML altyapısı · systemd servisi

## Arayüz (Phase 4, ilk sürüm)
- React + TypeScript + Vite + Recharts; 5 sekme. Simülasyon verisiyle uçtan uca denendi.
- Eksik: UNO Q üzerinde backend'in `dist/` klasörünü servis etmesi, BLE ile alınan verinin arayüze bağlanması.
