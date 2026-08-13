"""
Declarative registry of every ACS table this dashboard pulls: which variables
to request per year, and how to turn the raw cells into report metrics.

Centralizing this here means acs_fetch.py only needs to know how to fetch a
(table, year) pair generically, and acs_metrics.py only needs to call
`derive()` -- neither has to know the specifics of any one Census table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

NC_STATE_FIPS = "37"
YEARS = list(range(2015, 2025))  # 2011-2015 .. 2020-2024 ACS 5-year windows

# This project's focus counties (see vbh_rscripts/focus_counties.R).
FOCUS_COUNTY_FIPS = {
    "Alamance County": "001",
    "Cumberland County": "051",
    "Durham County": "063",
    "Edgecombe County": "065",
    "Halifax County": "083",
    "Jackson County": "099",
    "Nash County": "127",
}


def focus_fips() -> dict[str, str]:
    """county_name -> full 5-digit FIPS (state + county), matching GeoJSON `id` and CountyMetric.fips."""
    return {name: NC_STATE_FIPS + code for name, code in FOCUS_COUNTY_FIPS.items()}

# Census sentinel values for suppressed/inapplicable cells (e.g. -666666666).
# Real ACS counts/rates never approach this magnitude, so it's a safe cutoff.
_SENTINEL_CUTOFF = 10_000_000


def _cell(cells: dict[str, str], code: str) -> float:
    raw = cells.get(code)
    if raw is None or raw in ("", "null"):
        return 0.0
    try:
        value = float(raw)
    except ValueError:
        return 0.0
    return 0.0 if abs(value) >= _SENTINEL_CUTOFF else value


# --- MOE / reliability helpers (Census Bureau standard formulas) ---
# https://www.census.gov/programs-surveys/acs/guidance/statistical-testing.html


def coefficient_of_variation(value: float, moe: float) -> float | None:
    """CV%, per Census convention: (MOE / 1.645) / estimate * 100."""
    if not value:
        return None
    return abs((moe / 1.645) / value) * 100


def reliability_flag(cv: float | None) -> str:
    """Census Bureau convention: <15% reliable, 15-30% caution, >30% unreliable."""
    if cv is None:
        return "unreliable"
    if cv < 15:
        return "reliable"
    if cv < 30:
        return "caution"
    return "unreliable"


def moe_sum(*moes: float) -> float:
    """MOE of a sum of estimates: sqrt(sum of squares)."""
    return sum(m**2 for m in moes) ** 0.5


def moe_ratio(numerator: float, num_moe: float, denominator: float, denom_moe: float) -> float:
    """MOE of a derived proportion numerator/denominator, per Census formula."""
    if denominator == 0:
        return 0.0
    p = numerator / denominator
    under_root = num_moe**2 - (p**2 * denom_moe**2)
    if under_root < 0:
        under_root = num_moe**2 + (p**2 * denom_moe**2)  # Census's fallback when the term goes negative
    return (under_root**0.5) / denominator


# --- Table registry ---


@dataclass
class TableSpec:
    table_id: str
    dataset: str  # Census dataset path, e.g. "acs/acs5/profile" or "acs/acs5"
    variables_for_year: Callable[[int], list[str]]
    derive: Callable[[dict[str, str], int], dict[str, tuple[float, float]]]  # cells -> {metric_key: (value, moe)}


# DP02 -- veteran count/percent. Variable code shifts from _0069 to _0070 at 2019
# (confirmed live against the Census API for 2015 and 2023).
def _dp02_vars(year: int) -> list[str]:
    prefix = "DP02_0070" if year >= 2019 else "DP02_0069"
    return [f"{prefix}E", f"{prefix}M", f"{prefix}PE", f"{prefix}PM"]


def _dp02_derive(cells: dict[str, str], year: int) -> dict[str, tuple[float, float]]:
    prefix = "DP02_0070" if year >= 2019 else "DP02_0069"
    return {
        "veteran_count": (_cell(cells, f"{prefix}E"), _cell(cells, f"{prefix}M")),
        "veteran_pct": (_cell(cells, f"{prefix}PE"), _cell(cells, f"{prefix}PM")),
    }


# C21007 -- Age x Veteran Status x Poverty Status x Disability Status.
# Codes confirmed stable 2015-2023.
_C21007_VARS = [
    "C21007_003E", "C21007_003M", "C21007_004E", "C21007_004M",
    "C21007_018E", "C21007_018M", "C21007_019E", "C21007_019M",
]


def _c21007_derive(cells: dict[str, str], year: int) -> dict[str, tuple[float, float]]:
    vet_total = _cell(cells, "C21007_003E") + _cell(cells, "C21007_018E")
    vet_total_moe = moe_sum(_cell(cells, "C21007_003M"), _cell(cells, "C21007_018M"))
    vet_poverty = _cell(cells, "C21007_004E") + _cell(cells, "C21007_019E")
    vet_poverty_moe = moe_sum(_cell(cells, "C21007_004M"), _cell(cells, "C21007_019M"))
    if vet_total == 0:
        return {"veteran_poverty_rate": (0.0, 0.0)}
    rate = vet_poverty / vet_total * 100
    rate_moe = moe_ratio(vet_poverty, vet_poverty_moe, vet_total, vet_total_moe) * 100
    return {"veteran_poverty_rate": (rate, rate_moe)}


# B21100 -- service-connected disability rating (table is already veteran-only).
_B21100_VARS = ["B21100_001E", "B21100_001M", "B21100_003E", "B21100_003M"]


def _b21100_derive(cells: dict[str, str], year: int) -> dict[str, tuple[float, float]]:
    total, total_moe = _cell(cells, "B21100_001E"), _cell(cells, "B21100_001M")
    rated, rated_moe = _cell(cells, "B21100_003E"), _cell(cells, "B21100_003M")
    if total == 0:
        return {"disability_rating_pct": (0.0, 0.0)}
    rate = rated / total * 100
    rate_moe = moe_ratio(rated, rated_moe, total, total_moe) * 100
    return {"disability_rating_pct": (rate, rate_moe)}


# C27009 -- VA Health Care coverage by sex x age. Denominator is TOTAL population,
# not veterans only -- this is a coverage-proxy metric, labeled as such in MAP_METRICS.
_C27009_VA_E_CELLS = ["C27009_004E", "C27009_007E", "C27009_010E", "C27009_014E", "C27009_017E", "C27009_020E"]
_C27009_VARS = ["C27009_001E", "C27009_001M"] + [
    v for code in _C27009_VA_E_CELLS for v in (code, code.replace("E", "M"))
]


def _c27009_derive(cells: dict[str, str], year: int) -> dict[str, tuple[float, float]]:
    total, total_moe = _cell(cells, "C27009_001E"), _cell(cells, "C27009_001M")
    va_count = sum(_cell(cells, c) for c in _C27009_VA_E_CELLS)
    va_moe = moe_sum(*(_cell(cells, c.replace("E", "M")) for c in _C27009_VA_E_CELLS))
    if total == 0:
        return {"va_healthcare_pct": (0.0, 0.0)}
    rate = va_count / total * 100
    rate_moe = moe_ratio(va_count, va_moe, total, total_moe) * 100
    return {"va_healthcare_pct": (rate, rate_moe)}


# B21005 -- veteran labor force status, ages 18-64 (three age bands summed).
_B21005_LABOR_FORCE_E_CELLS = ["B21005_004E", "B21005_015E", "B21005_026E"]
_B21005_UNEMPLOYED_E_CELLS = ["B21005_006E", "B21005_017E", "B21005_028E"]
_B21005_VARS = [
    v for code in (_B21005_LABOR_FORCE_E_CELLS + _B21005_UNEMPLOYED_E_CELLS) for v in (code, code.replace("E", "M"))
]


def _b21005_derive(cells: dict[str, str], year: int) -> dict[str, tuple[float, float]]:
    labor_force = sum(_cell(cells, c) for c in _B21005_LABOR_FORCE_E_CELLS)
    labor_force_moe = moe_sum(*(_cell(cells, c.replace("E", "M")) for c in _B21005_LABOR_FORCE_E_CELLS))
    unemployed = sum(_cell(cells, c) for c in _B21005_UNEMPLOYED_E_CELLS)
    unemployed_moe = moe_sum(*(_cell(cells, c.replace("E", "M")) for c in _B21005_UNEMPLOYED_E_CELLS))
    if labor_force == 0:
        return {"veteran_unemployment_rate": (0.0, 0.0)}
    rate = unemployed / labor_force * 100
    rate_moe = moe_ratio(unemployed, unemployed_moe, labor_force, labor_force_moe) * 100
    return {"veteran_unemployment_rate": (rate, rate_moe)}


# B21004 -- median income by veteran status, all ages.
_B21004_VARS = ["B21004_002E", "B21004_002M"]


def _b21004_derive(cells: dict[str, str], year: int) -> dict[str, tuple[float, float]]:
    return {"veteran_median_income": (_cell(cells, "B21004_002E"), _cell(cells, "B21004_002M"))}


TABLES: dict[str, TableSpec] = {
    "DP02": TableSpec("DP02", "acs/acs5/profile", _dp02_vars, _dp02_derive),
    "C21007": TableSpec("C21007", "acs/acs5", lambda year: _C21007_VARS, _c21007_derive),
    "B21100": TableSpec("B21100", "acs/acs5", lambda year: _B21100_VARS, _b21100_derive),
    "C27009": TableSpec("C27009", "acs/acs5", lambda year: _C27009_VARS, _c27009_derive),
    "B21005": TableSpec("B21005", "acs/acs5", lambda year: _B21005_VARS, _b21005_derive),
    "B21004": TableSpec("B21004", "acs/acs5", lambda year: _B21004_VARS, _b21004_derive),
}


@dataclass
class MapMetricSpec:
    key: str
    label: str
    table_id: str
    colorscale: str
    value_format: str  # "percent" | "currency" | "count" | "minutes"
    condition: str = ""  # optional grouping key, e.g. a vital-conditions framework category


MAP_METRICS: list[MapMetricSpec] = [
    MapMetricSpec("veteran_pct", "Veteran % (18+ population)", "DP02", "Blues", "percent"),
    MapMetricSpec("veteran_poverty_rate", "Poverty rate among veterans", "C21007", "Oranges", "percent"),
    MapMetricSpec(
        "disability_rating_pct", "% veterans with service-connected disability rating", "B21100", "Purples", "percent"
    ),
    MapMetricSpec("va_healthcare_pct", "% population with VA health care coverage", "C27009", "Greens", "percent"),
    MapMetricSpec("veteran_unemployment_rate", "Veteran unemployment rate", "B21005", "Reds", "percent"),
    MapMetricSpec("veteran_median_income", "Median income, veterans", "B21004", "Tealgrn", "currency"),
]

# Full county-data-table metrics: veteran_count (raw estimate, not on the map/trend charts)
# plus every map metric -- feeds the "all counties" explorer table.
ALL_TABLE_METRICS: list[MapMetricSpec] = [
    MapMetricSpec("veteran_count", "Veteran count", "DP02", "Blues", "count"),
] + MAP_METRICS


if __name__ == "__main__":
    for year in (2015, 2018, 2019, 2023):
        print(year, "DP02 vars:", _dp02_vars(year))
    print("Tables registered:", list(TABLES))
    print("Map metrics:", [m.key for m in MAP_METRICS])
