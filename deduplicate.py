"""
deduplicate.py
--------------
Manages the in-memory and on-disk store of unique restaurants.

Responsibilities
----------------
* Track which restaurant_ids have already been seen (fast O(1) lookup).
* Merge new batches of raw restaurant dicts into the master store.
* Persist the store to disk (CSV + JSON) on demand or periodically.
* Load a previous run's results so the scraper can resume without
  re-saving duplicates.

Data contract
-------------
Each restaurant dict must contain at minimum:
    {
        "restaurant_id": str,
        "restaurant_name": str,
        "category": str,
        "rating": float | None,
        "review_count": int | None,
        "address": str,
        "latitude": float | None,
        "longitude": float | None,
    }

Optional detail fields (added during detail scraping):
    opening_hours, menu_count, price_range, is_open_status
"""

import csv
import json
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Column order for the CSV output
CSV_FIELDNAMES = [
    "restaurant_id",
    "restaurant_name",
    "category",
    "rating",
    "review_count",
    "address",
    "latitude",
    "longitude",
    "opening_hours",
    "menu_count",
    "price_range",
    "is_open_status",
    "resto_url",
]


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------

class RestaurantStore:
    """
    Thread-safe dictionary keyed by restaurant_id.

    Usage
    -----
        store = RestaurantStore.load_or_create(csv_path="data/result.csv")
        store.add_many(list_of_dicts)
        store.save(csv_path="data/result.csv", json_path="data/result.json")
        len(store)        # total unique restaurants
        store.seen_ids    # frozenset of known restaurant_ids
    """

    def __init__(self):
        self._data: Dict[str, dict] = {}   # restaurant_id → record
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Constructors / loaders
    # ------------------------------------------------------------------

    @classmethod
    def load_or_create(cls, csv_path: str) -> "RestaurantStore":
        """
        If *csv_path* exists, load it and return a pre-populated store.
        Otherwise return an empty store.  This enables resume-scraping.
        """
        store = cls()
        path = Path(csv_path)
        if path.exists():
            try:
                with path.open("r", encoding="utf-8", newline="") as f:
                    reader = csv.DictReader(f)
                    count = 0
                    for row in reader:
                        rid = row.get("restaurant_id", "").strip()
                        if rid:
                            store._data[rid] = row
                            count += 1
                logger.info("Resumed: loaded %d existing restaurants from %s", count, csv_path)
            except Exception as exc:
                logger.warning("Could not load existing CSV (%s) — starting fresh.", exc)
        return store

    # ------------------------------------------------------------------
    # Core mutations
    # ------------------------------------------------------------------

    def add(self, record: dict) -> bool:
        """
        Add a single restaurant record.  Returns True if it was new,
        False if it was already known (duplicate).
        """
        rid = str(record.get("restaurant_id", "")).strip()
        if not rid:
            logger.debug("Skipping record with empty restaurant_id: %s", record)
            return False

        with self._lock:
            if rid in self._data:
                # Merge: fill in any None fields from the new record
                existing = self._data[rid]
                updated = False
                for key, val in record.items():
                    if (val is not None and val != "") and (
                        existing.get(key) is None or existing.get(key) == ""
                    ):
                        existing[key] = val
                        updated = True
                if updated:
                    logger.debug("Updated existing restaurant %s with new fields.", rid)
                return False
            else:
                # Normalise record to have all expected fields
                normalised = {f: record.get(f, None) for f in CSV_FIELDNAMES}
                self._data[rid] = normalised
                return True

    def add_many(self, records: List[dict]) -> int:
        """
        Add a list of restaurant dicts.  Returns the count of NEW additions.
        """
        new_count = 0
        for rec in records:
            if self.add(rec):
                new_count += 1
        return new_count

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_csv(self, path: str) -> None:
        """Write all records to a CSV file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            records = list(self._data.values())

        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=CSV_FIELDNAMES,
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(records)

        logger.info("Saved %d restaurants → %s", len(records), path)

    def save_json(self, path: str) -> None:
        """Write all records to a JSON file (pretty-printed list)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            records = list(self._data.values())

        with path.open("w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

        logger.info("Saved %d restaurants → %s", len(records), path)

    def save(self, csv_path: str, json_path: str) -> None:
        """Convenience: save both formats in one call."""
        self.save_csv(csv_path)
        self.save_json(json_path)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        return len(self._data)

    @property
    def seen_ids(self) -> frozenset:
        with self._lock:
            return frozenset(self._data.keys())

    def all_records(self) -> List[dict]:
        with self._lock:
            return list(self._data.values())

    def contains(self, restaurant_id: str) -> bool:
        return restaurant_id in self._data


# ---------------------------------------------------------------------------
# Utility: merge multiple result files from different runs
# ---------------------------------------------------------------------------

def merge_result_files(
    *csv_paths: str,
    output_csv: str = "data/result_merged.csv",
    output_json: str = "data/result_merged.json",
) -> RestaurantStore:
    """
    Merge two or more result CSV files into one de-duplicated store.

    Example
    -------
        merge_result_files(
            "data/run1/result.csv",
            "data/run2/result.csv",
            output_csv="data/result_merged.csv",
            output_json="data/result_merged.json",
        )
    """
    store = RestaurantStore()
    for path in csv_paths:
        p = Path(path)
        if not p.exists():
            logger.warning("Merge: file not found %s — skipping.", path)
            continue
        with p.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            added = store.add_many(list(reader))
        logger.info("Merged %s → %d new entries", path, added)

    store.save(output_csv, output_json)
    logger.info("Merged total: %d unique restaurants", len(store))
    return store