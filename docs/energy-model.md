# Enerji modeli

Kod: `backend/app/services/energy_calculator.py`

## 1. Referans panel elektrik enerjisi (ölçülen → hesaplanan)

```
P = V × I
E[Wh] = Σ ½(P_k + P_k+1) · Δt_k / 3600
```
* Δt gerçek zaman damgalarından alınır (sabit aralık varsayılmaz).
* Δt > max(3 × ölçüm aralığı, 30 s) ise aralık **boşluk** sayılır, üretim olarak eklenmez.
* İki uçtan birinde değer yoksa (sensör hatası) o aralık atlanır.

> Not: Önerilen ölçüm yönteminde (kısa devre) V ≈ 0 olduğundan bu enerji çok
> küçüktür ve tahmin için kullanılmaz; tanılama amaçlı tutulur.

## 2. Işınım (POA) — referans panel Isc'den

```
G[W/m²] = I_sc / ( k · (1 + α·(T_panel − 25)) )
H[kWh/m²] = Σ ½(G_k + G_k+1) · Δt / 3.6e6
```
* `k` = `ref_isc_per_wm2_a` (A/(W/m²)). Varsayılan 0.150/1000 = **etiket varsayımı**.
* `α` ≈ 0.0005 /°C (kristal Si Isc katsayısı, varsayım).
* `ref_calibrated=false` iken H'nin türü **"estimated"**, kalibrasyondan sonra **"calculated"**.

### Gün tamlığı
Güneş konumu (NOAA) ile günün aydınlık süresi hesaplanır. Aydınlık sürenin
%90'ından fazlası ölçüldüyse gün **tam** sayılır. Gece boşlukları tamlığı
etkilemez (cihaz geceleri kapatılabilir). Eksik günler tahmine girmez.

## 3. Planlanan sistemin günlük üretimi

**measured_reference (saha ölçümlü):**
```
E_gün[kWh] = P_kurulu[kWp] · H[kWh/m²] / (1 kW/m²) · f_T · PR
f_T = 1 + γ · (T_w − 25)
```
* `T_w`: ışınım ağırlıklı referans panel sıcaklığı (ölçülen). Varsayım: büyük
  modüller referans panelle benzer sıcaklıkta.
* `γ` = modül sıcaklık katsayısı (varsayılan −0,35 %/°C, kullanıcı girer).
* `PR` = inverter + kablo + kirlenme + uyumsuzluk kayıpları (sıcaklık hariç, varsayılan 0,80).
* **Gölgeleme ayrıca uygulanmaz**: referans panel kurulum noktasında olduğu
  için gölge zaten H'nin içindedir. Bu yüzden referans panel planlanan
  **eğim ve yönde** monte edilmelidir; model eğim/yön dönüşümü yapmaz.

**theoretical_psh (ölçümsüz):**
```
E_gün = P_kurulu · PSH · PR       (örn. 3,6 kWp × 4,25 h × 0,80 = 12,24 kWh)
```
PSH kullanıcı varsayımıdır; saha ölçümü değildir.

## Çıktı etiketleri

API yanıtındaki her girdi şu türlerden biriyle işaretlenir:
`measured`, `assumed`, `calculated`, `estimated`. Simülasyon verisiyle yapılan
her tahminin ilk uyarısı `SİMÜLASYON VERİSİ` olur.

## Sınırlar
* 48 saat / birkaç gün veri **yıllık üretimi temsil etmez**; < 30 tam gün için uyarı verilir.
* Kalibrasyonsuz sonuç ±%20'nin üzerinde hatalı olabilir.
