# Frontend — React + TypeScript + Vite

Solar Field Station web arayüzü. Backend'in FastAPI'sini (`/api/*`) kullanır;
grafikler **Recharts** ile çizilir.

| Sekme | İçerik |
|---|---|
| Genel bakış | Cihaz durumu (çevrimiçi / uyuyor), tahmini ışınım, sıcaklıklar, 3S pil, 12 V fan, gece kapanma saatleri; 6 sa / 24 sa / 3 gün grafikleri |
| Enerji | Günlük ışınım (tam / eksik günler), kalibrasyon uyarısı, gün gün tablo |
| Üretim tahmini | Planlanan sistem → günlük kWh; her değer *ölçülen / varsayılan / hesaplanan / tahmini* etiketli |
| Ayarlar | Kayıt aralığı, gece kapanma, fan eşikleri, referans panel (kaydetmek için `SFS_API_TOKEN`) |
| Olaylar | Cihaz olayları, sensör hataları, CSV indirme |

## Çalıştırma (geliştirme)

Node.js 18+ gerekir. Önce backend'i başlat (kök README → *Hızlı başlangıç*), sonra:

```powershell
cd frontend
npm install
npm run dev
```

Tarayıcı: <http://localhost:5173>. `/api` istekleri Vite tarafından
`http://127.0.0.1:8000`'e yönlendirilir; backend başka yerdeyse (ör. UNO Q):

```powershell
$env:SFS_BACKEND_URL="http://192.168.1.50:8000"; npm run dev
```

## Derleme

```powershell
npm run build      # tür kontrolü + dist/ klasörü
```

`dist/` statik dosyalardır; UNO Q'ya kopyalanıp backend tarafından servis
edilebilir (sonraki adım). Kartın üzerinde `npm` ile derleme yapma, bilgisayarda derle.

## Notlar
- Ekran 30 sn'de bir yenilenir; gece kapanma boşlukları grafikte kopukluk olarak görünür.
- Genel bakıştaki ışınım, backend'deki formülün aynısıyla tarayıcıda hesaplanır ve **tahmini** olarak gösterilir.
- API anahtarı yalnızca tarayıcı sekmesinde (sessionStorage) tutulur.
