"""
SQLite persistence layer for ACS metrics: turns acs_metrics.build_store() into a
queryable local database (data/acs.db) so tables/figures can be pulled with plain
SQL or small pandas helpers instead of re-fetching from the Census API every time.

    from acs_db import build_database, query_metric, trend, sql
    build_database()                                    # populate/refresh the db
    query_metric("veteran_poverty_rate", year=2023)      # -> DataFrame, one row per county
    trend("veteran_poverty_rate", county="Durham")       # -> DataFrame, one row per year
    sql("SELECT * FROM metrics WHERE reliability = 'unreliable'")

Rebuilding is always safe (INSERT OR REPLACE keyed on metric_key/fips/year), and
since acs_fetch already falls back to cache/seed_data when the API is unreachable,
build_database() never fails outright -- worst case it fills the db with seed data.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from acs_metrics import MetricsStore, build_store
from acs_tables import ALL_TABLE_METRICS

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "data" / "acs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS metrics (
    metric_key   TEXT NOT NULL,
    table_id     TEXT NOT NULL,
    fips         TEXT NOT NULL,
    county_name  TEXT NOT NULL,
    year         INTEGER NOT NULL,
    value        REAL,
    moe          REAL,
    cv           REAL,
    reliability  TEXT,
    source       TEXT,
    as_of_year   INTEGER,
    PRIMARY KEY (metric_key, fips, year)
);

CREATE TABLE IF NOT EXISTS metric_catalog (
    metric_key    TEXT PRIMARY KEY,
    label         TEXT NOT NULL,
    table_id      TEXT NOT NULL,
    value_format  TEXT NOT NULL,
    scope         TEXT NOT NULL DEFAULT 'veteran'
);

CREATE INDEX IF NOT EXISTS idx_metrics_year ON metrics(year);
CREATE INDEX IF NOT EXISTS idx_metrics_fips ON metrics(fips);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def build_database(force_refresh: bool = False, db_path: Path = DB_PATH) -> MetricsStore:
    """Fetches every (table, year) via acs_metrics.build_store() and writes it to SQLite."""
    store = build_store(force_refresh=force_refresh)
    spec_by_key = {m.key: m for m in ALL_TABLE_METRICS}

    conn = connect(db_path)
    try:
        metric_rows = [
            (
                metric_key,
                spec_by_key[metric_key].table_id if metric_key in spec_by_key else "",
                fips,
                county_metric.county_name,
                year,
                county_metric.value,
                county_metric.moe,
                county_metric.cv,
                county_metric.reliability,
                county_metric.source,
                county_metric.as_of_year,
            )
            for metric_key, by_year in store.by_metric.items()
            for year, by_fips in by_year.items()
            for fips, county_metric in by_fips.items()
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO metrics VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            metric_rows,
        )
        catalog_rows = [(m.key, m.label, m.table_id, m.value_format, m.scope) for m in ALL_TABLE_METRICS]
        conn.executemany("INSERT OR REPLACE INTO metric_catalog VALUES (?,?,?,?,?)", catalog_rows)
        conn.commit()
    finally:
        conn.close()
    return store


def list_metrics(db_path: Path = DB_PATH) -> pd.DataFrame:
    conn = connect(db_path)
    try:
        return pd.read_sql_query("SELECT * FROM metric_catalog ORDER BY metric_key", conn)
    finally:
        conn.close()


def list_counties(db_path: Path = DB_PATH) -> pd.DataFrame:
    conn = connect(db_path)
    try:
        return pd.read_sql_query("SELECT DISTINCT fips, county_name FROM metrics ORDER BY county_name", conn)
    finally:
        conn.close()


def query_metric(
    metric_key: str, year: int | None = None, county: str | None = None, db_path: Path = DB_PATH
) -> pd.DataFrame:
    """One row per (county, year) matching metric_key, optionally filtered to one year
    and/or counties whose name contains `county` (case-insensitive substring)."""
    conn = connect(db_path)
    try:
        query = "SELECT * FROM metrics WHERE metric_key = ?"
        params: list = [metric_key]
        if year is not None:
            query += " AND year = ?"
            params.append(year)
        if county is not None:
            query += " AND county_name LIKE ?"
            params.append(f"%{county}%")
        query += " ORDER BY year, county_name"
        return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()


def trend(metric_key: str, county: str, db_path: Path = DB_PATH) -> pd.DataFrame:
    """Year-by-year values for one metric in one county (name substring match)."""
    return query_metric(metric_key, county=county, db_path=db_path)


def sql(query: str, params: tuple = (), db_path: Path = DB_PATH) -> pd.DataFrame:
    """Raw SQL escape hatch against the `metrics` / `metric_catalog` tables."""
    conn = connect(db_path)
    try:
        return pd.read_sql_query(query, conn, params=params)
    finally:
        conn.close()


if __name__ == "__main__":
    store = build_database()
    print(f"Database built at {DB_PATH}")
    print(f"{len(store.by_metric)} metrics, {len(store.county_names)} counties")
    print(list_metrics(db_path=DB_PATH).to_string(index=False))
