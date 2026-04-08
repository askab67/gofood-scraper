"""
scraper.py
----------
GoFood scraper — strategi 2 langkah via _next/data per kecamatan.

CONFIRMED dari debug_find_endpoint.py:
  ✓ GET /_next/data/17.20.0/id/madura/bangkalan-restaurants/aneka-nasi.json
    → 200 OK | 69 uid | 56 displayName ← INI YANG BEKERJA

Strategi:
  Langkah 1: Fetch halaman utama kecamatan
             → ambil semua path kategori dari pageProps.contents[].data[].path
  Langkah 2: Fetch setiap halaman kategori
             → ambil semua outlet dari response
  Gabung + deduplikasi by uid

Contoh paths dari bangkalan:
  /madura/bangkalan-restaurants/aneka-nasi
  /madura/bangkalan-restaurants/ayam-bebek
  /madura/bangkalan-restaurants/cepat-saji
  dst.
"""

import json
import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from deduplicate import RestaurantStore

logger = logging.getLogger(__name__)

# ===========================================================================
# Endpoints
# ===========================================================================

GOFOOD_BASE  = "https://gofood.co.id"

# Halaman utama kecamatan — berisi daftar path kategori
# service_area dan kecamatan diisi saat runtime
MAIN_PAGE_URL = (
    "https://gofood.co.id/_next/data/{build_id}/id/{service_area}/{kecamatan}-restaurants.json"
)

# Halaman kategori — berisi outlet list (CONFIRMED bekerja dari debug)
CATEGORY_URL = (
    "https://gofood.co.id/_next/data/{build_id}/id{category_path}.json"
)

# Fallback: POST search per keyword
SEARCH_URL   = "https://gofood.co.id/api/outlets/search"
DETAIL_URL   = "https://gofood.co.id/api/outlets/v3/{outlet_id}"

DEFAULT_PAGE_SIZE           = 12
_EARLY_STOP_DUPLICATE_RATIO = 0.85

# Keyword untuk fallback search (kecamatan kecil yang tidak punya halaman GoFood)
SEARCH_KEYWORDS = [
    "nasi", "mie", "soto", "bakso", "sate", "ayam", "bebek",
    "seafood", "ikan", "bakar", "goreng", "warung", "depot",
    "kopi", "cafe", "minuman", "jus", "martabak", "roti",
    "madura", "rawon", "pecel", "lalapan", "padang",
    "snack", "jajanan", "indomie", "nasi goreng",
]

# 17 kecamatan Kabupaten Bangkalan
BANGKALAN_KECAMATAN = [
    "bangkalan", "kamal", "burneh", "socah", "arosbaya",
    "blega", "galis", "geger", "klampis", "kokop",
    "kwanyar", "labang", "modung", "sepulu", "tanah-merah",
    "tanjung-bumi", "tragah",
]

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
]


# ===========================================================================
# Session
# ===========================================================================

def _build_session(
    headers: dict,
    cookies: dict,
    proxies: Optional[Dict[str, str]] = None,
    max_retries: int = 3,
) -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=max_retries,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://",  adapter)
    session.headers.update(headers)
    if cookies:
        session.cookies.update(cookies)
    if proxies:
        session.proxies.update(proxies)
    return session


# ===========================================================================
# Headers
# ===========================================================================

def load_headers(path: str = "config/headers.json") -> Tuple[dict, dict]:
    p = Path(path)
    if not p.exists():
        logger.warning("headers.json tidak ditemukan di %s", path)
        return _default_headers(), {}
    try:
        with p.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        cookie_string = raw.get("_cookie", "")
        headers = {k: v for k, v in raw.items() if not k.startswith("_")}
        cookies = _parse_cookie_string(cookie_string)
        logger.info("Header: %d | Cookie: %d dari %s", len(headers), len(cookies), path)
        return headers, cookies
    except Exception as exc:
        logger.warning("Gagal baca headers: %s", exc)
        return _default_headers(), {}


def _default_headers() -> dict:
    return {
        "accept":           "*/*",
        "accept-language":  "id-ID,id;q=0.9,en-US;q=0.8",
        "user-agent":       random.choice(_USER_AGENTS),
        "referer":          "https://gofood.co.id/",
    }


def _parse_cookie_string(s: str) -> dict:
    out = {}
    for part in s.split(";"):
        part = part.strip()
        if "=" in part:
            k, _, v = part.partition("=")
            out[k.strip()] = v.strip()
    return out


# ===========================================================================
# Build ID
# ===========================================================================

_build_id_cache: dict = {}
_build_id_lock  = threading.Lock()


def get_build_id(session: requests.Session, force: bool = False,
                 service_area: str = "madura") -> str:
    with _build_id_lock:
        if not force and _build_id_cache.get("id"):
            return _build_id_cache["id"]
        # Coba fetch halaman apapun untuk dapat buildId
        for url in [
            f"{GOFOOD_BASE}/id/{service_area}/restaurants",
            f"{GOFOOD_BASE}/id/madura/bangkalan-restaurants",
            f"{GOFOOD_BASE}/id",
        ]:
            try:
                r = session.get(url, timeout=15)
                m = re.search(r'"buildId"\s*:\s*"([^"]+)"', r.text)
                if m:
                    _build_id_cache["id"] = m.group(1)
                    logger.info("Build ID: %s", _build_id_cache["id"])
                    return _build_id_cache["id"]
            except Exception:
                continue
        fallback = "17.20.0"
        _build_id_cache["id"] = fallback
        logger.warning("Build ID fallback: %s", fallback)
        return fallback


# ===========================================================================
# Langkah 1 — ambil category paths dari halaman utama kecamatan
# ===========================================================================

def get_category_paths(
    session:      requests.Session,
    kecamatan:    str,
    build_id:     str,
    service_area: str = "madura",
    timeout:      int = 15,
) -> List[str]:
    """
    Fetch halaman utama kecamatan dan ekstrak semua path kategori.

    CONFIRMED dari debug_category.py (bangkalan):
    - Section 0 'Rekomendasi kami': near_me, best_seller, affordable_price, most_loved, 24_hours
    - Section 1 'Aneka kuliner menarik': martabak, soto_bakso_sop, roti, chinese, ... (17 item)
    - Section 2 'Populer di areamu': brand pages → dilewati
    - Section 3 & 4: individual restaurant paths → dilewati

    serviceArea default "madura" untuk Bangkalan. Ganti sesuai daerah.
    """
    url = MAIN_PAGE_URL.format(
        build_id=build_id,
        service_area=service_area,
        kecamatan=kecamatan,
    )
    try:
        r = session.get(url, timeout=timeout)
        if r.status_code == 404:
            new_id = get_build_id(session, force=True)
            if new_id != build_id:
                url = MAIN_PAGE_URL.format(
                    build_id=new_id,
                    service_area=service_area,
                    kecamatan=kecamatan,
                )
                r = session.get(url, timeout=timeout)
        if r.status_code != 200:
            logger.warning("Kecamatan %s: HTTP %d", kecamatan, r.status_code)
            return []
        data = r.json()
    except Exception as exc:
        logger.warning("Gagal fetch halaman utama %s: %s", kecamatan, exc)
        return []

    paths: List[str] = []
    seen:  Set[str]  = set()
    contents = data.get("pageProps", {}).get("contents", [])

    for section in contents:
        for item in section.get("data", []):
            path = item.get("path", "").strip()
            if not path or path in seen or path.startswith("http"):
                continue
            # Lewati halaman restoran individual dan brand
            if "/restaurant/" in path or "/brand/" in path:
                continue
            if "restaurants/" in path:
                seen.add(path)
                paths.append(path)

    logger.info("Kecamatan %s: %d kategori ditemukan", kecamatan, len(paths))
    return paths


# ===========================================================================
# Langkah 2 — scrape satu halaman kategori
# ===========================================================================

def scrape_category_page(
    session:       requests.Session,
    category_path: str,
    build_id:      str,
    timeout:       int = 15,
) -> Tuple[List[dict], str]:
    """
    Fetch satu halaman kategori dan return (outlet_list, next_page_token).

    CONFIRMED: GET /_next/data/{buildId}/id{path}.json
      → 200 OK, 69 uid, 56 displayName untuk aneka-nasi Bangkalan

    Pagination: jika ada nextPageToken di response, akan dikembalikan
    untuk request berikutnya dengan parameter ?pageToken=...
    """
    url = CATEGORY_URL.format(build_id=build_id, category_path=category_path)
    try:
        r = session.get(url, timeout=timeout)
        if r.status_code != 200:
            logger.debug("Kategori %s: HTTP %d", category_path, r.status_code)
            return [], ""
        data = r.json()
    except Exception as exc:
        logger.debug("Gagal fetch kategori %s: %s", category_path, exc)
        return [], ""

    # Ekstrak outlet list dari pageProps
    outlets   = _extract_outlets(data)
    next_token = _extract_next_token(data)
    return outlets, next_token


def scrape_category_all_pages(
    session:       requests.Session,
    category_path: str,
    build_id:      str,
    max_pages:     int = 10,
    delay:         float = 0.4,
    timeout:       int = 15,
) -> Tuple[List[dict], int]:
    """Fetch semua halaman satu kategori, return (all_outlets, request_count)."""
    all_outlets: List[dict] = []
    req_count:   int        = 0

    # Halaman pertama — tanpa pageToken
    outlets, next_token = scrape_category_page(session, category_path, build_id, timeout)
    req_count += 1
    all_outlets.extend(outlets)

    page = 1
    while next_token and page < max_pages:
        # Halaman berikutnya — dengan pageToken sebagai query param
        url = CATEGORY_URL.format(build_id=build_id, category_path=category_path)
        try:
            r = session.get(url, params={"pageToken": next_token}, timeout=timeout)
            req_count += 1
            if r.status_code != 200:
                break
            data       = r.json()
            outlets    = _extract_outlets(data)
            next_token = _extract_next_token(data)
            if not outlets:
                break
            all_outlets.extend(outlets)
            page += 1
            time.sleep(delay)
        except Exception as exc:
            logger.debug("Pagination gagal %s: %s", category_path, exc)
            break

    return all_outlets, req_count


# ===========================================================================
# Ekstraksi outlet dari response _next/data (halaman kategori)
# ===========================================================================

def _extract_outlets(data: dict) -> List[dict]:
    """
    CONFIRMED dari debug_category_full.json:
    pageProps.outlets adalah list langsung — tidak perlu deep search.

    Struktur: data.pageProps.outlets = [
      { "uid": "...", "core": {...}, "ratings": {...}, "priceLevel": 1, "path": "..." },
      ...
    ]
    """
    return data.get("pageProps", {}).get("outlets") or []


def _deep_find_outlets(obj, depth: int = 0) -> List[dict]:
    """Cari rekursif list yang berisi outlet objects."""
    if depth > 6:
        return []
    if isinstance(obj, list) and len(obj) >= 2:
        first = obj[0]
        if isinstance(first, dict) and (
            first.get("uid") or
            (isinstance(first.get("core"), dict)) or
            str(first.get("key", "")).startswith("tenants/")
        ):
            return obj
    if isinstance(obj, dict):
        # Prioritaskan key yang kemungkinan berisi outlet
        priority = ["outlets", "restaurants", "items", "data",
                    "outletList", "restaurantList", "results"]
        keys = sorted(obj.keys(),
                      key=lambda k: 0 if k in priority else 1)
        for k in keys:
            result = _deep_find_outlets(obj[k], depth + 1)
            if result:
                return result
    return []


def _extract_next_token(data: dict) -> str:
    """Cari nextPageToken di response _next/data."""
    pp = data.get("pageProps", {})
    for key in ["nextPageToken", "pageToken", "cursor", "next_cursor"]:
        val = pp.get(key)
        if val and isinstance(val, str) and val.strip():
            return val.strip()
        # Cari satu level lebih dalam
        for v in pp.values():
            if isinstance(v, dict):
                val = v.get(key)
                if val and isinstance(val, str) and val.strip():
                    return val.strip()
    return ""


# ===========================================================================
# Parser outlet — confirmed dari debug_api.py
# ===========================================================================

def _parse_restaurant(raw: dict) -> Optional[dict]:
    """
    CONFIRMED dari debug_category_full.json:

    Outlet root level:
      uid        → restaurant_id
      ratings    → {"average": 4.8, "total": 0}  ← BUKAN di core
      priceLevel → 1  (integer 1-4)              ← BUKAN di core
      path       → /madura/restaurant/nama-uuid

    outlet.core:
      displayName → restaurant_name
      location    → {"latitude": ..., "longitude": ...}
      status      → 1=buka, 0=tutup
      openPeriods → [{day, startTime:{hours,min}, endTime:{hours,min}}]
      highlights  → [] (kategori, sering kosong)
    """
    try:
        rid = str(raw.get("uid", "")).strip()
        if not rid:
            key = str(raw.get("key", ""))
            rid = key.split("/")[-1] if "/" in key else key
        if not rid:
            return None

        core: dict = raw.get("core") or {}

        # Nama
        name = core.get("displayName") or raw.get("displayName") or ""

        # Kategori — dari highlights (sering kosong)
        highlights = core.get("highlights") or []
        category   = ", ".join(
            h.get("name") or "" for h in highlights if isinstance(h, dict)
        ).strip(", ")

        # Rating — CONFIRMED di root.ratings, BUKAN core
        ratings_obj  = raw.get("ratings") or {}
        rating       = _safe_float(ratings_obj.get("average"))
        review_count = _safe_int(ratings_obj.get("total"))

        # Lokasi — CONFIRMED: core.location
        loc = core.get("location") or {}
        lat = _safe_float(loc.get("latitude"))
        lon = _safe_float(loc.get("longitude"))

        # Alamat — core.address adalah None, tidak tersedia di endpoint ini
        address = ""

        # Status — CONFIRMED: core.status = 1 buka
        sv = core.get("status")
        is_open_status = ("open" if int(sv) == 1 else "closed") if sv is not None else None

        # Jam buka — CONFIRMED: core.openPeriods
        opening_hours = None
        ops = core.get("openPeriods") or []
        if ops:
            days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
            parts = []
            for p in ops:
                d  = days[max(0, p.get("day", 1) - 1)]
                st = p.get("startTime") or {}
                et = p.get("endTime")   or {}
                parts.append(
                    f"{d}:{st.get('hours',0):02d}:{st.get('minutes',0):02d}"
                    f"-{et.get('hours',0):02d}:{et.get('minutes',0):02d}"
                )
            opening_hours = " | ".join(parts)

        # Harga — CONFIRMED: root.priceLevel (integer 1-4)
        price_level = raw.get("priceLevel")
        price_range = f"Level {price_level}" if price_level else None

        # URL restoran GoFood — dari root.path
        resto_path = raw.get("path", "")

        return {
            "restaurant_id":   rid,
            "restaurant_name": name,
            "category":        category,
            "rating":          rating,
            "review_count":    review_count,
            "address":         address,
            "latitude":        lat,
            "longitude":       lon,
            "opening_hours":   opening_hours,
            "menu_count":      None,
            "price_range":     price_range,
            "is_open_status":  is_open_status,
            "resto_url":       f"https://gofood.co.id{resto_path}" if resto_path else "",
        }
    except Exception as exc:
        logger.debug("Parse error: %s", exc)
        return None


# ===========================================================================
# Main scraper
# ===========================================================================

def _search_by_keywords(
    session:      requests.Session,
    lat:          float,
    lon:          float,
    keywords:     List[str],
    page_size:    int   = DEFAULT_PAGE_SIZE,
    max_pages:    int   = 3,
    delay:        float = 0.5,
    store:        Optional[RestaurantStore] = None,
    timeout:      int   = 15,
) -> Tuple[List[dict], int]:
    """
    Fallback untuk kecamatan kecil yang tidak punya halaman GoFood sendiri.
    Search POST /api/outlets/search dengan beberapa keyword dari satu koordinat.

    Return: (all_raw_outlets_deduplicated, total_requests)
    """
    seen:       set       = set()
    all_outlets: List[dict] = []
    total_reqs: int        = 0

    for keyword in keywords:
        page_token = ""

        for _ in range(max_pages):
            body: dict = {
                "query":    keyword,
                "language": "en",
                "timezone": "Asia/Jakarta",
                "pageSize": page_size,
                "location": {"latitude": lat, "longitude": lon},
            }
            if page_token:
                body["pageToken"] = page_token

            try:
                r = session.post(SEARCH_URL, json=body, timeout=timeout)
                r.raise_for_status()
                data = r.json()
            except Exception as exc:
                logger.debug("Keyword search error kw=%r: %s", keyword, exc)
                total_reqs += 1
                break

            total_reqs += 1
            outlets    = data.get("outlets") or []
            next_token = str(data.get("nextPageToken", "")).strip()

            for o in outlets:
                uid = str(o.get("uid", "")).strip()
                if uid and uid not in seen:
                    seen.add(uid)
                    all_outlets.append(o)

            if not outlets or not next_token:
                break
            page_token = next_token
            time.sleep(delay)

        time.sleep(random.uniform(0.3, 0.6))

    return all_outlets, total_reqs


class GoFoodScraper:
    """
    Scrape GoFood Bangkalan via _next/data halaman kategori per kecamatan.

    Alur:
      Untuk setiap kecamatan:
        1. GET halaman utama → ekstrak semua path kategori
        2. Untuk setiap kategori → GET halaman kategori → ambil outlet
        3. Dedup by uid + filter spasial + simpan
    """

    def __init__(
        self,
        store:              RestaurantStore,
        headers_path:       str                                       = "config/headers.json",
        delay_min:          float                                     = 1.0,
        delay_max:          float                                     = 2.5,
        max_threads:        int                                       = 2,
        proxies:            Optional[Dict[str, str]]                  = None,
        fetch_details:      bool                                      = False,
        region                                                        = None,
        on_progress:        Optional[Callable[[int, int, int], None]] = None,
        on_log:             Optional[Callable[[str], None]]           = None,
        stop_event                                                    = None,
        search_radius_km:   float                                     = 9.0,   # compat
        max_pages:          int                                       = 10,
        page_size:          int                                       = DEFAULT_PAGE_SIZE,
        kecamatan_list:      Optional[List[str]]                       = None,
        service_area:        str                                       = "madura",
        kecamatan_polygons:  Optional[Dict[str, any]]                  = None,
    ):
        self.store              = store
        self.delay_min          = delay_min
        self.delay_max          = delay_max
        self.max_threads        = max_threads
        self.fetch_details      = fetch_details
        self.region             = region
        self.on_progress        = on_progress
        self.on_log             = on_log
        self.stop_event         = stop_event
        self.max_pages          = max_pages
        self.kecamatan_list     = kecamatan_list or BANGKALAN_KECAMATAN
        self.service_area       = service_area or "madura"
        self.kecamatan_polygons = kecamatan_polygons or {}

        headers, cookies      = load_headers(headers_path)
        self._session_tpl     = (headers, cookies, proxies)

        self._lock            = threading.Lock()
        self.total_requests   = 0
        self.failed_requests  = 0
        self.new_restaurants  = 0
        self._completed_kecamatan = 0   # counter untuk progress bar UI

    def run(
        self,
        coordinate_points = None,   # diabaikan, kept untuk compat UI
        save_csv:  str = "data/result.csv",
        save_json: str = "data/result.json",
        autosave_every: int = 3,
    ) -> int:
        targets = self.kecamatan_list
        total   = len(targets)
        self._log(
            f"Mulai scrape {total} kecamatan | serviceArea={self.service_area} | "
            f"threads={self.max_threads} | metode=_next/data kategori"
        )

        _tl = threading.local()

        def _session() -> requests.Session:
            if not hasattr(_tl, "s"):
                h, c, px = self._session_tpl
                hh = h.copy()
                if "user-agent" not in {k.lower() for k in hh}:
                    hh["user-agent"] = random.choice(_USER_AGENTS)
                _tl.s = _build_session(hh, c, px)
            return _tl.s

        def _scrape_kecamatan(args: Tuple[int, str]) -> int:
            idx, kecamatan = args
            if self.stop_event and self.stop_event.is_set():
                return 0

            sess      = _session()
            build_id  = get_build_id(sess, service_area=self.service_area)
            new_count = 0

            # ── Langkah 1: coba _next/data ────────────────────────────────
            cat_paths = get_category_paths(sess, kecamatan, build_id,
                                           service_area=self.service_area)
            with self._lock:
                self.total_requests += 1

            seen_in_kec: Set[str]   = set()
            kec_outlets: List[dict] = []

            if cat_paths:
                # ── Langkah 2a: scrape tiap halaman kategori ─────────────
                for cat_path in cat_paths:
                    if self.stop_event and self.stop_event.is_set():
                        break

                    outlets, reqs = scrape_category_all_pages(
                        sess, cat_path, build_id,
                        max_pages=self.max_pages,
                        delay=random.uniform(0.3, 0.7),
                    )
                    with self._lock:
                        self.total_requests += reqs

                    for raw in outlets:
                        uid = str(raw.get("uid", "")).strip()
                        if uid and uid not in seen_in_kec:
                            seen_in_kec.add(uid)
                            kec_outlets.append(raw)

                    self._log(
                        f"  [{idx+1}/{total}] {kecamatan} / "
                        f"{cat_path.split('/')[-1]} "
                        f"→ {len(outlets)} outlet | kec total: {len(kec_outlets)}"
                    )
                    time.sleep(random.uniform(0.4, 1.0))

            else:
                # ── Langkah 2b: fallback — polygon + keyword search ───────
                # Kecamatan kecil tidak punya halaman GoFood sendiri (403)
                # Gunakan polygon dari GeoJSON untuk generate titik koordinat,
                # lalu search dengan keyword dari tiap titik.

                from polygon_filter import get_kecamatan_polygon
                from grid_generator import generate_strategic_points

                poly = get_kecamatan_polygon(
                    self.kecamatan_polygons, kecamatan
                )

                if poly is None:
                    self._log(
                        f"  [{idx+1}/{total}] {kecamatan}: 403 & polygon "
                        f"tidak ada di GeoJSON → dilewati"
                    )
                    with self._lock:
                        self.failed_requests += 1
                    if self.on_progress:
                        self.on_progress(idx + 1, total, 0)
                    with self._lock:
                        self._completed_kecamatan += 1
                    return 0

                # Generate 2–3 titik strategis di dalam polygon kecamatan
                points = generate_strategic_points(poly, n_extra_interior=0)
                self._log(
                    f"  [{idx+1}/{total}] {kecamatan}: fallback ke "
                    f"keyword search | {len(points)} titik koordinat"
                )

                for lat, lon in points:
                    if self.stop_event and self.stop_event.is_set():
                        break

                    outlets, reqs = _search_by_keywords(
                        session  = sess,
                        lat      = lat,
                        lon      = lon,
                        keywords = SEARCH_KEYWORDS,
                        store    = self.store,
                    )
                    with self._lock:
                        self.total_requests += reqs

                    # Spatial filter: pastikan outlet ada dalam polygon kecamatan
                    for raw in outlets:
                        uid = str(raw.get("uid", "")).strip()
                        if not uid or uid in seen_in_kec:
                            continue
                        # Cek koordinat ada di dalam polygon kecamatan
                        core = raw.get("core") or {}
                        loc  = core.get("location") or {}
                        rlat = loc.get("latitude")
                        rlon = loc.get("longitude")
                        if rlat and rlon and not poly.contains(rlat, rlon):
                            continue
                        seen_in_kec.add(uid)
                        kec_outlets.append(raw)

                    self._log(
                        f"  [{idx+1}/{total}] {kecamatan} "
                        f"({lat:.4f},{lon:.4f}) → {len(outlets)} outlet | "
                        f"kec total: {len(kec_outlets)}"
                    )
                    time.sleep(random.uniform(self.delay_min, self.delay_max))

            # ── Langkah 3: parse + simpan ─────────────────────────────────
            for raw in kec_outlets:
                parsed = _parse_restaurant(raw)
                if parsed is None:
                    continue
                if self.store.add(parsed):
                    new_count += 1

            with self._lock:
                self.new_restaurants += new_count
                self._completed_kecamatan += 1

            time.sleep(random.uniform(self.delay_min, self.delay_max))
            if self.on_progress:
                self.on_progress(idx + 1, total, new_count)
            self._log(
                f"[{idx+1}/{total}] Kecamatan {kecamatan:15s} "
                f"→ +{new_count} baru | total {len(self.store):,}"
            )
            return new_count

        # Thread pool
        indexed   = list(enumerate(targets))
        completed = 0

        with ThreadPoolExecutor(max_workers=self.max_threads) as ex:
            futures = {ex.submit(_scrape_kecamatan, item): item for item in indexed}

            for future in as_completed(futures):
                if self.stop_event and self.stop_event.is_set():
                    for f in futures: f.cancel()
                    self._log("⚠ Dihentikan.")
                    break
                try:
                    future.result()
                except Exception as exc:
                    with self._lock:
                        self.failed_requests += 1
                    logger.error("Worker error: %s", exc)

                completed += 1
                if completed % autosave_every == 0:
                    self.store.save(save_csv, save_json)
                    self._log(
                        f"💾 Autosave — {len(self.store):,} restoran | "
                        f"{completed}/{total} kecamatan"
                    )

        self.store.save(save_csv, save_json)
        self._log(
            f"✅ Selesai | Kecamatan: {total} | "
            f"Requests: {self.total_requests} | "
            f"Baru: {self.new_restaurants} | Total: {len(self.store)}"
        )
        return self.new_restaurants

    def _log(self, msg: str) -> None:
        logger.info(msg)
        if self.on_log:
            self.on_log(msg)


# ===========================================================================
# Helpers
# ===========================================================================

def _safe_float(val) -> Optional[float]:
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


def _safe_int(val) -> Optional[int]:
    try:
        return int(val) if val is not None else None
    except (TypeError, ValueError):
        return None