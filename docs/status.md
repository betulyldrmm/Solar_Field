# Durum raporu — Phase 2 (backend + simülasyon)

Tarih: 2026-11-26

## Gerçekten çalışan (bilgisayarda test edildi)
- Doğrulayıcı (schema v1), sensör hata yönetimi, tekrar/atlama tespiti
- SQLite (WAL, migrasyon), günlük CSV, CSV dışa aktarma
- Enerji integrasyonu, Isc → ışınım, gün tamlığı, üretim tahmini (iki yöntem)
- FastAPI: 11 endpoint, token korumalı ayar değişikliği
- Simülatör + canlı arka plan döngüsü, geçmiş veri üretme betiği
- **52/52 pytest testi geçti, ruff temiz** (Python 3.13)

## Simülasyon olan
- Tüm ölçüm verisi (`data_source=simulation`). Simülatörde referans panelin
  gerçek katsayısı bilerek etiketten %7 düşük → kalibrasyonun etkisi görülebilir.

## Donanım doğrulaması bekleyen
| Konu | Durum |
|---|---|
| CN3791 çıkış gerilimi (4,2 V mı 12,6 V mı) | **Pil bağlanmadan önce ölçülmeli** |
| 1S → 5 V güç yolu (XL6009 datasheet min. 5 V giriş) | Tasarım kararı bekliyor |
| Fan MOSFET (IRFZ44N lojik seviyeli değil) | Lojik seviyeli MOSFET gerekli |
| UNO Q Bridge API (App Lab) | DOĞRULAMA GEREKİYOR — `bridge_service.py` |
| DS3231'in Linux'a mı STM32'ye mi bağlanacağı | DOĞRULAMA GEREKİYOR |
| Referans panel kısa devre bağlantısı, INA226 şönt | DOĞRULAMA GEREKİYOR |
| Enerji bütçesi (kışın 7/24 yetmiyor) | Gece kapatma veya daha büyük panel/pil |

## Henüz başlanmayan
- Phase 3: STM32 firmware · Phase 4: React arayüz · ML altyapısı · systemd servisi
