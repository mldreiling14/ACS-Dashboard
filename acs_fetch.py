"""
Fetches every (table, year) pair declared in acs_tables.TABLES for all 100 NC
counties, with a fallback chain so the report always renders real data:

    disk cache -> live Census API -> most-recent cached year for that table
    -> bundled seed_data/ snapshot -> unavailable

Nothing here ever fabricates a number -- every fallback tier is real ACS data,
just older, cached, or bundled instead of live.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import requests
from dotenv import load_dotenv

from acs_tables import NC_STATE_FIPS, TABLES, YEARS, TableSpec

load_dotenv()

BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache" / "acs"
SEED_DIR = BASE_DIR / "seed_data"

Source = Literal["cache", "live", "stale_cache", "seed", "unavailable"]


@dataclass
class TableFetchResult:
    table_id: str
    year: int
    county_rows: list[dict[str, str]]  # one dict per county: NAME/variable codes/state/county -> value
    source: Source
    as_of_year: int | None  # the year the data actually reflects (differs from `year` for stale_cache/seed)


def _cache_path(table_id: str, year: int) -> Path:
    return CACHE_DIR / f"{table_id}_{year}.json"


def _seed_path(table_id: str) -> Path:
    return SEED_DIR / f"{table_id}.json"


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _write_cache(table_id: str, year: int, county_rows: list[dict[str, str]]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"fetched_at": datetime.now(timezone.utc).isoformat(), "county_rows": county_rows}
    _cache_path(table_id, year).write_text(json.dumps(payload), encoding="utf-8")


def _parse_rows(raw: list[list[str]]) -> list[dict[str, str]]:
    header, *data_rows = raw
    return [dict(zip(header, row)) for row in data_rows]


def _fetch_live(spec: TableSpec, year: int, retries: int = 3) -> list[dict[str, str]]:
    """GETs one table/year for all NC counties, retrying transient failures.

    Same retry-with-backoff idiom as scrape_acs_resources.fetch_page -- real
    networks (and an occasionally overloaded Census API) drop connections.
    """
    api_key = os.environ.get("CENSUS_API_KEY")
    variables = spec.variables_for_year(year)
    url = f"https://api.census.gov/data/{year}/{spec.dataset}"
    params = {
        "get": ",".join(["NAME", *variables]),
        "for": "county:*",
        "in": f"state:{NC_STATE_FIPS}",
    }
    if api_key:
        params["key"] = api_key

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, params=params, timeout=20)
            response.raise_for_status()
            raw = response.json()  # raises ValueError if the API returned an HTML error page
            return _parse_rows(raw)
        except (requests.exceptions.RequestException, ValueError) as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * attempt)
    raise last_error  # type: ignore[misc]


def fetch_table_year(
    table_id: str, year: int, force_refresh: bool = False, tables: dict[str, TableSpec] = TABLES
) -> TableFetchResult:
    spec = tables[table_id]

    if not force_refresh:
        cached = _read_json(_cache_path(table_id, year))
        if cached is not None:
            return TableFetchResult(table_id, year, cached["county_rows"], "cache", year)

    try:
        county_rows = _fetch_live(spec, year)
        _write_cache(table_id, year, county_rows)
        return TableFetchResult(table_id, year, county_rows, "live", year)
    except Exception as exc:  # network/key/rate-limit issues -- fall back, don't crash the report
        print(f"[acs_fetch] Live fetch failed for {table_id} {year} ({exc}); trying fallbacks.")

    if force_refresh:  # the initial cache read was skipped above -- the same-year cache still beats a stale one
        cached = _read_json(_cache_path(table_id, year))
        if cached is not None:
            return TableFetchResult(table_id, year, cached["county_rows"], "cache", year)

    for fallback_year in sorted((y for y in YEARS if y != year), reverse=True):
        cached = _read_json(_cache_path(table_id, fallback_year))
        if cached is not None:
            return TableFetchResult(table_id, year, cached["county_rows"], "stale_cache", fallback_year)

    seed = _read_json(_seed_path(table_id))
    if seed is not None:
        return TableFetchResult(table_id, year, seed["county_rows"], "seed", seed.get("year"))

    return TableFetchResult(table_id, year, [], "unavailable", None)


def get_all(
    force_refresh: bool = False, tables: dict[str, TableSpec] = TABLES
) -> dict[tuple[str, int], TableFetchResult]:
    results: dict[tuple[str, int], TableFetchResult] = {}
    for table_id in tables:
        for year in YEARS:
            result = fetch_table_year(table_id, year, force_refresh=force_refresh, tables=tables)
            results[(table_id, year)] = result
    return results


if __name__ == "__main__":
    for year in (2015, 2023):
        result = fetch_table_year("DP02", year, force_refresh=True)
        print(f"DP02 {year}: source={result.source} as_of={result.as_of_year} counties={len(result.county_rows)}")
        if result.county_rows:
            print("  sample:", result.county_rows[0])
