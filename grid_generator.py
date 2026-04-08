"""
grid_generator.py
-----------------
Two coordinate-generation strategies for GoFood scraping:

1. generate_strategic_points()  ← NEW (optimised mode)
   Produces 10–20 carefully chosen points (centroid, cardinal extremes,
   quadrant sub-centroids) instead of a dense grid.  Combined with a
   large search radius (8–10 km) and full API pagination, this covers the
   entire Bangkalan region with fewer than 200 HTTP requests.

2. generate_grid()              ← LEGACY (kept for backward compatibility)
   Dense grid walk — still usable when very fine spatial coverage is needed.

Coordinate strategy diagram (Bangkalan ~1,260 km²):

    NW──────N──────NE
    │   Q1     Q2   │
    W───── C ───────E      C  = polygon centroid
    │   Q3     Q4   │      Q1…Q4 = quadrant sub-centroids
    SW──────S──────SE

  With radius=9 km each point covers ~254 km²  →  full overlap.
"""

import logging
from typing import Generator, List, Tuple

from shapely.geometry import Point, MultiPolygon
from shapely.ops import unary_union

from polygon_filter import RegionPolygon

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NEW — Strategic point generation
# ---------------------------------------------------------------------------

def generate_strategic_points(
    region: RegionPolygon,
    n_extra_interior: int = 8,
) -> List[Tuple[float, float]]:
    """
    Generate a small, high-value set of coordinate points guaranteed to
    lie inside *region*.

    The set always includes:
      • polygon centroid
      • 4 cardinal edge mid-points  (N, S, E, W)
      • 4 bounding-box corners nudged inward (if inside the polygon)
      • up to *n_extra_interior* quadrant sub-centroids

    Total: typically 10–20 points.

    Parameters
    ----------
    region           : RegionPolygon from polygon_filter.py
    n_extra_interior : how many additional interior points to add via
                       2×2 (or 3×3) bbox sub-division.  Default 8 gives
                       a 3×3 grid of sub-regions minus the centre (already
                       covered by the centroid).

    Returns
    -------
    Deduplicated list of (lat, lon) tuples, all inside the polygon.
    """
    geom = region.geometry
    min_lat, max_lat, min_lon, max_lon = region.bounding_box()

    candidates: List[Tuple[float, float]] = []

    # ------------------------------------------------------------------
    # 1. Polygon centroid
    # ------------------------------------------------------------------
    cx, cy = geom.centroid.x, geom.centroid.y   # x=lon, y=lat in Shapely
    candidates.append((round(cy, 7), round(cx, 7)))

    # ------------------------------------------------------------------
    # 2. Cardinal edge mid-points  (on bbox edge, shifted inward by 10%)
    # ------------------------------------------------------------------
    lat_span = max_lat - min_lat
    lon_span = max_lon - min_lon
    inset_lat = lat_span * 0.10
    inset_lon = lon_span * 0.10
    mid_lat = (min_lat + max_lat) / 2
    mid_lon = (min_lon + max_lon) / 2

    cardinal = [
        (max_lat - inset_lat, mid_lon),   # North
        (min_lat + inset_lat, mid_lon),   # South
        (mid_lat, max_lon - inset_lon),   # East
        (mid_lat, min_lon + inset_lon),   # West
    ]
    candidates.extend(cardinal)

    # ------------------------------------------------------------------
    # 3. Bounding-box corners nudged 15% inward
    # ------------------------------------------------------------------
    corners = [
        (max_lat - inset_lat, min_lon + inset_lon),   # NW
        (max_lat - inset_lat, max_lon - inset_lon),   # NE
        (min_lat + inset_lat, min_lon + inset_lon),   # SW
        (min_lat + inset_lat, max_lon - inset_lon),   # SE
    ]
    candidates.extend(corners)

    # ------------------------------------------------------------------
    # 4. Interior sub-region centroids
    #    Divide the bounding box into a k×k grid and use the centroid of
    #    the polygon clipped to each cell.  This discovers deep interior
    #    zones that may be missed by simple cardinal points.
    # ------------------------------------------------------------------
    k = _grid_k_for(n_extra_interior)   # e.g. n=8 → k=3 (3×3 = 9 cells)
    lat_step = lat_span / k
    lon_step = lon_span / k

    from shapely.geometry import box as shapely_box

    for row in range(k):
        for col in range(k):
            cell_min_lat = min_lat + row * lat_step
            cell_max_lat = cell_min_lat + lat_step
            cell_min_lon = min_lon + col * lon_step
            cell_max_lon = cell_min_lon + lon_step

            # Build a rectangular cell and intersect with the region
            cell = shapely_box(cell_min_lon, cell_min_lat, cell_max_lon, cell_max_lat)
            intersection = geom.intersection(cell)

            if intersection.is_empty:
                continue

            # Use the centroid of the intersection (always inside the polygon)
            ic = intersection.centroid
            candidates.append((round(ic.y, 7), round(ic.x, 7)))

    # ------------------------------------------------------------------
    # 5. Filter: keep only points confirmed inside the polygon,
    #    then deduplicate by rounding to 4 decimal places.
    # ------------------------------------------------------------------
    seen: set = set()
    accepted: List[Tuple[float, float]] = []

    for lat, lon in candidates:
        if not region.contains(lat, lon):
            # Try a tiny nudge toward the centroid
            lat, lon = _nudge_to_centroid(lat, lon, cy, cx)
            if not region.contains(lat, lon):
                continue

        key = (round(lat, 4), round(lon, 4))
        if key not in seen:
            seen.add(key)
            accepted.append((lat, lon))

    logger.info(
        "Strategic points: %d candidates → %d accepted (all inside polygon)",
        len(candidates),
        len(accepted),
    )
    return accepted


def describe_strategic_points(
    points: List[Tuple[float, float]],
) -> List[dict]:
    """
    Return a list of dicts with 'lat', 'lon', 'label' for UI display.
    Labels are inferred from index order that matches generate_strategic_points().
    """
    labels = ["Centroid", "North", "South", "East", "West",
              "NW", "NE", "SW", "SE"] + [f"Interior-{i}" for i in range(1, 50)]
    return [
        {"lat": lat, "lon": lon, "label": labels[i] if i < len(labels) else f"P{i}"}
        for i, (lat, lon) in enumerate(points)
    ]


# ---------------------------------------------------------------------------
# LEGACY — Dense grid (kept for backward compatibility)
# ---------------------------------------------------------------------------

def generate_grid(
    region: RegionPolygon,
    grid_density: float = 0.01,
) -> List[Tuple[float, float]]:
    """
    [LEGACY] Return a list of (lat, lon) tuples spaced grid_density° apart
    that lie inside *region*.

    Still available for cases where very fine spatial resolution is needed.
    For normal use prefer generate_strategic_points().
    """
    min_lat, max_lat, min_lon, max_lon = region.bounding_box()

    min_lat = _snap(min_lat, grid_density)
    min_lon = _snap(min_lon, grid_density)

    total_candidates = 0
    accepted: List[Tuple[float, float]] = []

    lat = min_lat
    while lat <= max_lat + grid_density:
        lon = min_lon
        while lon <= max_lon + grid_density:
            total_candidates += 1
            if region.contains(lat, lon):
                accepted.append((round(lat, 7), round(lon, 7)))
            lon = round(lon + grid_density, 8)
        lat = round(lat + grid_density, 8)

    logger.info(
        "Grid generation complete: density=%.4f | candidates=%d | inside=%d (%.1f%%)",
        grid_density,
        total_candidates,
        len(accepted),
        100 * len(accepted) / max(total_candidates, 1),
    )
    return accepted


def generate_grid_iter(
    region: RegionPolygon,
    grid_density: float = 0.01,
) -> Generator[Tuple[float, float], None, None]:
    """[LEGACY] Generator version of generate_grid."""
    min_lat, max_lat, min_lon, max_lon = region.bounding_box()
    min_lat = _snap(min_lat, grid_density)
    min_lon = _snap(min_lon, grid_density)

    lat = min_lat
    while lat <= max_lat + grid_density:
        lon = min_lon
        while lon <= max_lon + grid_density:
            if region.contains(lat, lon):
                yield round(lat, 7), round(lon, 7)
            lon = round(lon + grid_density, 8)
        lat = round(lat + grid_density, 8)


def estimate_grid_size(
    region: RegionPolygon,
    grid_density: float = 0.01,
) -> int:
    """[LEGACY] Estimate number of grid points inside the region."""
    min_lat, max_lat, min_lon, max_lon = region.bounding_box()
    bbox_area = (max_lat - min_lat) * (max_lon - min_lon)
    poly_area = region.area_sq_degrees()
    fill_ratio = poly_area / max(bbox_area, 1e-9)
    n_bbox = int(bbox_area / (grid_density ** 2))
    return int(n_bbox * fill_ratio)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _snap(value: float, step: float) -> float:
    """Snap a value down to the nearest multiple of step."""
    return round((value // step) * step, 8)


def _grid_k_for(n_extra: int) -> int:
    """
    Choose the square-grid dimension k such that k*k ≈ n_extra.
    k=2 → 4 cells, k=3 → 9, k=4 → 16.
    Minimum k=2.
    """
    import math
    k = max(2, round(math.sqrt(n_extra)))
    return k


def _nudge_to_centroid(
    lat: float, lon: float,
    clat: float, clon: float,
    step: float = 0.005,
) -> Tuple[float, float]:
    """
    Nudge (lat, lon) one small step in the direction of the centroid.
    Used to rescue corner/edge points that fell just outside the polygon.
    """
    dlat = clat - lat
    dlon = clon - lon
    dist = max((dlat**2 + dlon**2) ** 0.5, 1e-9)
    return (
        round(lat + step * dlat / dist, 7),
        round(lon + step * dlon / dist, 7),
    )
