"""
Exports data/acs.db to a tidy longitudinal CSV and a multi-sheet Excel workbook,
suitable for trend analysis outside this project (Excel, Sheets, etc).

    python export_acs_data.py

Writes:
    data/veteran_acs_longitudinal.csv    long format, one row per metric/county/year
    data/veteran_acs_longitudinal.xlsx   Legend (with each metric's `scope`: veteran / civilian /
                                          total_population -- see acs_tables.MapMetricSpec.scope),
                                          a "Veteran vs Civilian" sheet pairing every veteran_*
                                          metric with its civilian_* counterpart, the full long
                                          format, and one county-by-year pivot sheet per metric
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from acs_db import DB_PATH, sql

BASE_DIR = Path(__file__).parent
CSV_PATH = BASE_DIR / "data" / "veteran_acs_longitudinal.csv"
XLSX_PATH = BASE_DIR / "data" / "veteran_acs_longitudinal.xlsx"


_INVALID_SHEET_CHARS = set('[]:*?/\\')


def _sheet_name(metric_key: str) -> str:
    cleaned = "".join(c for c in metric_key if c not in _INVALID_SHEET_CHARS)
    return cleaned[:31]


def build_long_format() -> pd.DataFrame:
    df = sql(
        """
        SELECT
            m.metric_key,
            c.label       AS metric_label,
            c.value_format,
            c.scope,
            m.county_name,
            m.fips,
            m.year,
            m.value,
            m.moe,
            m.cv,
            m.reliability,
            m.source,
            m.as_of_year
        FROM metrics m
        JOIN metric_catalog c ON c.metric_key = m.metric_key
        ORDER BY c.label, m.county_name, m.year
        """
    )
    return df


def build_veteran_vs_civilian(long_df: pd.DataFrame) -> pd.DataFrame:
    """One row per (county, year, comparison): veteran value next to its civilian
    counterpart, for every metric_key pair that differs only by a veteran_/civilian_
    prefix (e.g. veteran_pct_male / civilian_pct_male)."""
    metric_keys = set(long_df["metric_key"].unique())
    pairs = []
    for key in sorted(metric_keys):
        if key.startswith("veteran_"):
            civilian_key = "civilian_" + key[len("veteran_"):]
            if civilian_key in metric_keys:
                pairs.append((key, civilian_key))

    rows = []
    for veteran_key, civilian_key in pairs:
        vet = long_df[long_df["metric_key"] == veteran_key].set_index(["county_name", "year"])
        civ = long_df[long_df["metric_key"] == civilian_key].set_index(["county_name", "year"])
        comparison_label = vet["metric_label"].iloc[0].replace(", Veterans", "")
        joined = vet[["value"]].join(civ[["value"]], lsuffix="_veteran", rsuffix="_civilian", how="inner")
        joined["comparison"] = comparison_label
        joined["gap_veteran_minus_civilian"] = joined["value_veteran"] - joined["value_civilian"]
        rows.append(joined.reset_index())

    return pd.concat(rows, ignore_index=True)[
        ["comparison", "county_name", "year", "value_veteran", "value_civilian", "gap_veteran_minus_civilian"]
    ].sort_values(["comparison", "county_name", "year"])


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"{DB_PATH} not found -- run `python acs_db.py` first to build it.")

    long_df = build_long_format()
    catalog = sql("SELECT * FROM metric_catalog ORDER BY label")
    vet_vs_civ = build_veteran_vs_civilian(long_df)

    long_df.to_csv(CSV_PATH, index=False)

    with pd.ExcelWriter(XLSX_PATH, engine="openpyxl") as writer:
        catalog.to_excel(writer, sheet_name="Legend", index=False)
        vet_vs_civ.to_excel(writer, sheet_name="Veteran vs Civilian", index=False)
        long_df.to_excel(writer, sheet_name="Long Format (All Data)", index=False)

        for _, row in catalog.iterrows():
            metric_key, label = row["metric_key"], row["label"]
            metric_df = long_df[long_df["metric_key"] == metric_key]
            pivot = metric_df.pivot(index="county_name", columns="year", values="value").sort_index()
            pivot.to_excel(writer, sheet_name=_sheet_name(metric_key))

    print(f"Wrote {CSV_PATH} ({len(long_df)} rows)")
    print(
        f"Wrote {XLSX_PATH} ({len(catalog)} metric sheets + Legend + "
        f"Veteran vs Civilian [{len(vet_vs_civ)} rows] + Long Format)"
    )


if __name__ == "__main__":
    main()
