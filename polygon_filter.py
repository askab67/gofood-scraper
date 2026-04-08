"""
polygon_filter.py
-----------------
Loads a GeoJSON file and provides utilities to:
  - Extract all polygon geometries from a FeatureCollection
  - Test whether a (lat, lon) point lies inside the union of those polygons
  - Return the bounding box of the entire region
  - Extract per-kecamatan polygons by grouping desa features (WADMKC field)

Dependencies: shapely
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from shapely.geometry import shape, Point, MultiPolygon
from shapely.ops import unary_union

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class RegionPolygon:
    """
    Wraps one or more Shapely geometries extracted from a GeoJSON file.

    Usage
    -----
        region = RegionPolygon.from_file("data/bangkalan.geojson")
        region.contains(lat=-7.05, lon=112.73)   # → True / False
        region.bounding_box()                    # → (min_lat, max_lat, min_lon, max_lon)
    """

    def __init__(self, union_geometry):
        self._geom = union_geometry

    @classmethod
    def from_file(cls, path: str) -> "RegionPolygon":
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"GeoJSON file not found: {path}")
        with path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        return cls._from_geojson_dict(raw)

    @classmethod
    def from_dict(cls, geojson_dict: dict) -> "RegionPolygon":
        return cls._from_geojson_dict(geojson_dict)

    @classmethod
    def _from_geojson_dict(cls, geojson_dict: dict) -> "RegionPolygon":
        geometries = _extract_geometries(geojson_dict)
        if not geometries:
            raise ValueError("No valid polygon geometries found in GeoJSON.")
        union = unary_union(geometries)
        logger.info(
            "Loaded %d polygon(s) from GeoJSON → union area ≈ %.6f sq°",
            len(geometries), union.area,
        )
        return cls(union)

    def contains(self, lat: float, lon: float) -> bool:
        return self._geom.contains(Point(lon, lat))

    def bounding_box(self) -> Tuple[float, float, float, float]:
        min_lon, min_lat, max_lon, max_lat = self._geom.bounds
        return min_lat, max_lat, min_lon, max_lon

    def area_sq_degrees(self) -> float:
        return self._geom.area

    @property
    def geometry(self):
        return self._geom


# ---------------------------------------------------------------------------
# Per-kecamatan polygon extraction
# ---------------------------------------------------------------------------

def extract_kecamatan_polygons(
    geojson_dict: dict,
) -> Dict[str, "RegionPolygon"]:
    """
    Dari GeoJSON berisi batas desa (Bangkalan), kelompokkan desa-desa berdasarkan
    field WADMKC (nama kecamatan), union semua polygon desa dalam satu kecamatan,
    dan return dict {nama_kecamatan_lowercase: RegionPolygon}.

    Contoh output:
        {
          "bangkalan": RegionPolygon(...),  # union semua desa di kec. Bangkalan
          "kwanyar":   RegionPolygon(...),
          "kokop":     RegionPolygon(...),
          ...
        }

    Field yang digunakan dari properties:
        WADMKC → nama kecamatan (contoh: "Kwanyar", "Bangkalan", "Kokop")

    Key di output dict adalah lowercase tanpa spasi, cocok dengan format
    GoFood URL (bangkalan, tanah-merah, tanjung-bumi).
    """
    # Kumpulkan geometri per kecamatan
    kec_geoms: Dict[str, List] = {}

    for feature in geojson_dict.get("features", []):
        props    = feature.get("properties") or {}
        geom_raw = feature.get("geometry")
        wadmkc   = props.get("WADMKC", "").strip()

        if not wadmkc or not geom_raw:
            continue

        try:
            geom = shape(geom_raw)
        except Exception:
            continue

        kec_geoms.setdefault(wadmkc, []).append(geom)

    # Build RegionPolygon per kecamatan
    result: Dict[str, RegionPolygon] = {}
    for wadmkc, geoms in kec_geoms.items():
        union     = unary_union(geoms)
        # Normalise key: lowercase, spasi → tanda hubung
        key       = wadmkc.lower().replace(" ", "-")
        result[key] = RegionPolygon(union)

    logger.info(
        "extract_kecamatan_polygons: %d kecamatan diekstrak dari GeoJSON",
        len(result),
    )
    return result


def get_kecamatan_polygon(
    kecamatan_polygons: Dict[str, "RegionPolygon"],
    kecamatan_name: str,
) -> Optional["RegionPolygon"]:
    """
    Cari polygon untuk nama kecamatan tertentu.
    Coba exact match dulu, lalu partial match kalau tidak ketemu.

    Contoh: "tanah-merah" → cocok dengan key "tanah-merah"
            "tanjung-bumi" → cocok dengan key "tanjung-bumi"
    """
    key = kecamatan_name.lower().replace(" ", "-")

    # Exact match
    if key in kecamatan_polygons:
        return kecamatan_polygons[key]

    # Partial match — cocok kalau key mengandung nama atau sebaliknya
    for k, poly in kecamatan_polygons.items():
        if key in k or k in key:
            logger.debug(
                "Kecamatan '%s' matched ke polygon key '%s'", kecamatan_name, k
            )
            return poly

    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_geometries(geojson_dict: dict) -> List:
    geom_type  = geojson_dict.get("type", "")
    geometries: List = []

    if geom_type == "FeatureCollection":
        for feature in geojson_dict.get("features", []):
            geometries.extend(_extract_geometries(feature))

    elif geom_type == "Feature":
        geom = geojson_dict.get("geometry")
        if geom:
            geometries.extend(_extract_geometries(geom))

    elif geom_type in ("Polygon", "MultiPolygon"):
        try:
            geometries.append(shape(geojson_dict))
        except Exception as exc:
            logger.warning("Could not parse geometry: %s", exc)

    elif geom_type == "GeometryCollection":
        for sub in geojson_dict.get("geometries", []):
            geometries.extend(_extract_geometries(sub))

    return geometries
