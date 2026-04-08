# GoFood Restaurant Scraper – Kabupaten Bangkalan

A modular, resumable Python tool for collecting restaurant data from GoFood
within the administrative boundary of Kabupaten Bangkalan.

---

## Project Structure

```
gofood_scraper/
├── app.py               ← Streamlit GUI (main entry point)
├── scraper.py           ← Core HTTP scraping engine
├── grid_generator.py    ← Coordinate grid generation
├── polygon_filter.py    ← GeoJSON polygon loading & point-in-polygon test
├── deduplicate.py       ← Deduplication, CSV/JSON persistence, multi-run merge
├── requirements.txt
├── README.md
│
├── config/
│   └── headers.json     ← Chrome request headers (you fill this in)
│
└── data/
    ├── bangkalan.geojson  ← Administrative boundary (you provide this)
    ├── result.csv         ← Output (auto-created)
    └── result.json        ← Output (auto-created)
```

---

## Quick Start

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Capture GoFood API headers from Chrome

This step is **required** — GoFood's API requires a session token.

1. Open **https://gofood.co.id** in Chrome (log in if needed).
2. Press **F12** → **Network** tab → enable **Preserve log**.
3. Search for a restaurant in the Bangkalan area on the website.
4. In the Network panel, filter by **Fetch/XHR**.
5. Look for a request URL containing `outlets`, `search`, or `restaurants`.
6. Click the request → **Headers** → copy all **Request Headers**.
7. Also note the exact **Request URL** and query parameter names.
8. Paste the headers into `config/headers.json`.
9. Update `SEARCH_URL` and `_parse_restaurant()` in `scraper.py` to match
   the real endpoint and response schema you observed.

### 3. Provide the GeoJSON boundary

Place your GeoJSON boundary file at `data/bangkalan.geojson`, or upload it
via the Streamlit UI.

### 4. Run the Streamlit app

```bash
streamlit run app.py
```

Open http://localhost:8501 in your browser.

---

## How It Works

```
GeoJSON → RegionPolygon
                ↓
         generate_grid()          (e.g. ~1200 points at 0.01° density)
                ↓
    for each (lat, lon) point:
         GoFood Search API  →  list of raw outlets
                ↓
         _parse_restaurant()      (normalise field names)
                ↓
         region.contains()        (spatial filter)
                ↓
         store.add()              (deduplication by restaurant_id)
                ↓
    periodic autosave → result.csv + result.json
```

---

## Configuration Reference

| Setting | Default | Description |
|---------|---------|-------------|
| `grid_density` | 0.01° | ~1.1 km step size. Smaller = more coverage + more requests. |
| `search_radius` | 2.0 km | Radius sent to the GoFood API per point. |
| `delay_min` | 0.8 s | Minimum random delay between requests. |
| `delay_max` | 2.5 s | Maximum random delay between requests. |
| `max_threads` | 3 | Parallel worker threads. |
| `fetch_details` | False | If True, also fetches the detail page per restaurant. |
| `resume_scraping` | True | Load existing result.csv and skip known restaurant_ids. |

---

## Adapting to the Real GoFood API

The two places you **must** edit after capturing your headers:

### 1. `scraper.py` — endpoint URLs

```python
SEARCH_URL = "https://gofood.co.id/api/outlets/v1/search"   # ← update
DETAIL_URL  = "https://gofood.co.id/api/outlets/v1/{outlet_id}"  # ← update
```

### 2. `scraper.py` — query parameters in `_search_restaurants_at_point()`

```python
params = {
    "lat": lat,
    "long": lon,      # ← might be "lng", "longitude", etc.
    "radius": radius_km,
    "page": page,
    "pageSize": page_size,
}
```

### 3. `scraper.py` — response field names in `_parse_restaurant()`

```python
rid  = raw.get("id") or raw.get("outletId") ...   # ← match real field name
name = raw.get("name") or raw.get("outletName") ... # ← match real field name
```

Use Chrome DevTools → Preview tab on the captured XHR request to inspect
the exact JSON structure returned by the API.

---

## Running Multiple Times & Merging

```bash
# Run 1 (daytime — catches open restaurants)
streamlit run app.py   # → data/result.csv

# Run 2 (evening — catches restaurants only open at night)
# Move result.csv to data/run2/result.csv first, then run again

# Merge in the UI — use the "Merge Runs" tab
# Or from Python:
from deduplicate import merge_result_files
merge_result_files("data/run1/result.csv", "data/run2/result.csv",
                   output_csv="data/result_final.csv",
                   output_json="data/result_final.json")
```

---

## Expected Output

| Field | Example |
|-------|---------|
| `restaurant_id` | `abc123` |
| `restaurant_name` | `Warung Nasi Pecel Bu Siti` |
| `category` | `Indonesian` |
| `rating` | `4.7` |
| `review_count` | `312` |
| `address` | `Jl. Raya Bangkalan No. 45` |
| `latitude` | `-7.0512` |
| `longitude` | `112.7483` |
| `opening_hours` | `Mon: 08:00-21:00 \| Tue: 08:00-21:00 ...` |
| `menu_count` | `24` |
| `price_range` | `Rp 10.000 – Rp 50.000` |
| `is_open_status` | `True` |

Estimated unique restaurants in Bangkalan: **1,000 – 3,000**.

---

## Legal & Ethical Notes

- This tool is for **research and data analysis purposes only**.
- Always respect GoFood's Terms of Service and `robots.txt`.
- Use reasonable delays (≥ 0.8 s) to avoid overloading their servers.
- Do not redistribute collected data commercially without permission.
