# 🍴 GoFood Restaurant Scraper

Tool scraping data restoran GoFood untuk wilayah administratif tertentu di Indonesia, dibangun dengan Python dan Streamlit.

> Dibuat untuk kebutuhan riset dan analisis data UMKM kuliner di Kabupaten Bangkalan, Madura.

---

## 📸 Tampilan Aplikasi

![Scraper UI](https://i.imgur.com/placeholder.png)


---

## ✨ Fitur Utama

- **Scraping per kecamatan** via endpoint `_next/data` GoFood (tanpa Selenium)
- **Fallback otomatis** untuk kecamatan kecil yang tidak punya halaman GoFood — menggunakan polygon GeoJSON + keyword search
- **Deduplikasi otomatis** berdasarkan `restaurant_id` (uid)
- **Multi-threading** — scrape beberapa kecamatan secara paralel
- **Resume scraping** — lanjutkan dari titik terakhir jika terhenti
- **Export CSV & Excel** langsung dari UI
- **Reverse geocode** — isi kolom alamat otomatis dari koordinat (via Nominatim/OpenStreetMap)
- **Merge hasil** dari beberapa run (pagi + malam) untuk coverage lebih lengkap
- **Pilih daerah** — tersedia Bangkalan, Sampang, Pamekasan, Sumenep, Surabaya, atau input kustom
- **Ambil cookie otomatis** dari browser Chrome (tombol 1 klik)

---

## 🗂 Struktur Project

```
gofood_scraper/
├── app.py                  # UI Streamlit (entry point)
├── scraper.py              # Engine scraping utama
├── polygon_filter.py       # Load GeoJSON, ekstrak polygon per kecamatan
├── grid_generator.py       # Generate titik koordinat dalam polygon
├── deduplicate.py          # Manajemen store restoran & dedup
├── fix_csv.py              # Konversi CSV ke Excel rapi
├── requirements.txt
│
├── config/
│   └── headers.json        # Cookie & headers GoFood (auto-generated)
│
└── data/
    ├── bangkalan.geojson   # Batas kecamatan Bangkalan (sediakan sendiri)
    ├── result.csv          # Output scraping
    └── result.json         # Output scraping (format JSON)
```

---

## ⚙️ Instalasi

### 1. Clone repository

```bash
git clone https://github.com/askab67gofood-scraper.git
cd gofood-scraper
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Jalankan aplikasi

```bash
streamlit run app.py
```

Buka browser di `http://localhost:8501`

---

## 🚀 Cara Penggunaan

### Langkah 1 — Ambil Cookie GoFood

Cookie diperlukan agar GoFood mengizinkan akses data.

**Cara otomatis (direkomendasikan):**
1. Pastikan sudah pernah membuka `gofood.co.id` di Chrome
2. **Tutup Chrome** terlebih dahulu
3. Di sidebar app, klik tombol **🍪 Ambil Cookie dari Chrome**
4. Cookie tersimpan otomatis ke `config/headers.json`

> ⚠️ Cookie biasanya expired setelah **1–4 jam**. Klik tombol lagi jika muncul error 403.

**Cara manual (jika otomatis gagal):**
1. Buka `gofood.co.id` di Chrome
2. Tekan `F12` → tab **Network** → centang **Preserve log**
3. Lakukan pencarian restoran
4. Klik kanan request `search` → **Copy → Copy as cURL (bash)**
5. Buat file `config/headers.json`:

```json
{
  "accept": "application/json, text/plain, */*",
  "accept-language": "id-ID,id;q=0.9",
  "user-agent": "Mozilla/5.0 ...",
  "_cookie": "OptanonConsent=...; csrfSecret=...; XSRF-TOKEN=...; w_tsfp=..."
}
```

---

### Langkah 2 — Siapkan GeoJSON (opsional tapi direkomendasikan)

GeoJSON batas kecamatan diperlukan untuk scrape kecamatan kecil yang tidak punya halaman GoFood sendiri.

- Download dari [Geoportal BIG](https://tanahair.indonesia.go.id/) atau [geoportal.bangkalankab.go.id](http://geoportal.bangkalankab.go.id)
- Format: GeoJSON FeatureCollection dengan field `WADMKC` (nama kecamatan) di properties
- Taruh di `data/bangkalan.geojson` atau upload langsung via UI

---

### Langkah 3 — Pilih Daerah & Mulai Scraping

1. Pilih daerah di sidebar (Bangkalan, Sampang, dll)
2. Upload GeoJSON jika tersedia
3. Klik **▶ Mulai Scraping**
4. Pantau progress di log
5. Download hasil di tab **Hasil**

---

## 📊 Output Data

| Field | Deskripsi | Contoh |
|---|---|---|
| `restaurant_id` | ID unik GoFood | `4deb51ef-f66c-...` |
| `restaurant_name` | Nama restoran | `Warung Sate Madura` |
| `category` | Kategori makanan | `Sate, Ayam` |
| `rating` | Rating rata-rata | `4.8` |
| `review_count` | Jumlah ulasan | `312` |
| `address` | Alamat (dari reverse geocode) | `Jl. Raya Bangkalan No. 45` |
| `latitude` | Koordinat lintang | `-7.0269` |
| `longitude` | Koordinat bujur | `112.7548` |
| `opening_hours` | Jam operasional | `Mon:10:00-22:00 \| Tue:10:00-22:00` |
| `price_range` | Level harga (1-4) | `Level 2` |
| `is_open_status` | Status buka/tutup | `open` |
| `resto_url` | URL halaman GoFood | `https://gofood.co.id/...` |

---

## 🏗 Cara Kerja

```
Upload GeoJSON → extract 18 polygon kecamatan
                        │
        ┌───────────────┴───────────────┐
        │                               │
   Kecamatan besar                Kecamatan kecil
   (bangkalan, kamal)             (burneh, socah, dll)
        │                               │
   _next/data endpoint            Polygon dari GeoJSON
   → dapat semua kategori         → generate 2-3 titik koordinat
   → tiap kategori → outlet list  → search keyword (nasi, ayam, sate...)
        │                               │
        └───────────────┬───────────────┘
                        │
               Dedup by uid
                        │
               Filter spasial
                        │
               Simpan CSV + JSON
```

---

## 💡 Tips

- **Jalankan 2x** (siang + malam) untuk hasil lebih lengkap — restoran yang tutup di satu waktu mungkin buka di waktu lain
- **Gunakan tab Merge** untuk gabungkan hasil beberapa run
- **Isi alamat** menggunakan tab **🗺 Peta Alamat** setelah scraping selesai
- **Kurangi threads ke 1** jika sering kena error 403/429

---

## 🔧 Menambah Daerah Baru

Edit `KNOWN_AREAS` di `app.py`:

```python
"Kabupaten Gresik": {
    "service_area": "surabaya",
    "localities": ["gresik", "kebomas", "manyar", "bungah", ...],
},
```

Cara cari `serviceArea` dan `locality`:
1. Buka GoFood, pilih lokasi yang diinginkan
2. Lihat URL: `gofood.co.id/id/{serviceArea}/{locality}-restaurants`

---

## 📦 Dependencies

| Library | Kegunaan |
|---|---|
| `requests` | HTTP requests ke GoFood API |
| `streamlit` | UI aplikasi |
| `shapely` | Operasi geometri polygon |
| `pandas` | Manipulasi data & export |
| `openpyxl` | Export ke Excel |
| `rookiepy` / `browser-cookie3` | Ambil cookie Chrome otomatis |

---

## ⚠️ Disclaimer

Tool ini dibuat untuk keperluan **riset dan analisis data** saja. Penggunaan harus mematuhi Terms of Service GoFood/Gojek. Jangan gunakan untuk tujuan komersial tanpa izin.

---

## 📄 Lisensi

MIT License — bebas digunakan dan dimodifikasi untuk keperluan non-komersial.
