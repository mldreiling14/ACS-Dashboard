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


# DP03 (Economic Characteristics) -- SNAP/food-stamp receipt and health insurance coverage.
# Both are household/total-population measures: ACS has no table cross-tabulating either one by
# Veteran status (confirmed against the live Census API's groups.json -- the only tables that
# cross-tab by Veteran status at all are B21001/B21002/B21003/B21004/B21005/B21100/C21007, none
# of which touch SNAP or general health insurance). Distinct table id "DP03G" (G for "general
# population") since vital_tables.py's DP03V already pulls a different variable subset from this
# same underlying DP03 table -- separate ids keep their disk caches from colliding. Both codes
# confirmed stable 2015-2024 against the live Census API.
_DP03G_VARS = [
    "DP03_0074PE", "DP03_0074PM",  # Households with SNAP/food stamp benefits in the past 12 months
    "DP03_0099PE", "DP03_0099PM",  # No health insurance coverage
]


def _dp03g_derive(cells: dict[str, str], year: int) -> dict[str, tuple[float, float]]:
    return {
        "snap_pct": (_cell(cells, "DP03_0074PE"), _cell(cells, "DP03_0074PM")),
        "no_health_insurance_pct": (_cell(cells, "DP03_0099PE"), _cell(cells, "DP03_0099PM")),
    }


# B21001 -- Sex by Age by Veteran Status (civilian population 18+). One of the few tables
# that cross-tabs by veteran status, so these are true veteran-vs-civilian comparisons,
# not just veteran-only or whole-population figures. Codes confirmed live 2015-2023.
_B21001_VARS = [
    "B21001_002E", "B21001_002M",  # veteran, total
    "B21001_003E", "B21001_003M",  # nonveteran (civilian), total
    "B21001_005E", "B21001_005M",  # veteran, male
    "B21001_006E", "B21001_006M",  # nonveteran, male
    "B21001_017E", "B21001_017M",  # veteran, male, 65-74
    "B21001_018E", "B21001_018M",  # nonveteran, male, 65-74
    "B21001_020E", "B21001_020M",  # veteran, male, 75+
    "B21001_021E", "B21001_021M",  # nonveteran, male, 75+
    "B21001_035E", "B21001_035M",  # veteran, female, 65-74
    "B21001_036E", "B21001_036M",  # nonveteran, female, 65-74
    "B21001_038E", "B21001_038M",  # veteran, female, 75+
    "B21001_039E", "B21001_039M",  # nonveteran, female, 75+
]


def _b21001_rate(cells: dict[str, str], num_codes: list[str], total_code: str, total_moe_code: str) -> tuple[float, float]:
    total, total_moe = _cell(cells, total_code), _cell(cells, total_moe_code)
    num = sum(_cell(cells, c) for c in num_codes if c.endswith("E"))
    num_moe = moe_sum(*(_cell(cells, c) for c in num_codes if c.endswith("M")))
    if total == 0:
        return (0.0, 0.0)
    return (num / total * 100, moe_ratio(num, num_moe, total, total_moe) * 100)


def _b21001_derive(cells: dict[str, str], year: int) -> dict[str, tuple[float, float]]:
    return {
        "veteran_pct_male": _b21001_rate(cells, ["B21001_005E", "B21001_005M"], "B21001_002E", "B21001_002M"),
        "civilian_pct_male": _b21001_rate(cells, ["B21001_006E", "B21001_006M"], "B21001_003E", "B21001_003M"),
        "veteran_pct_65plus": _b21001_rate(
            cells,
            ["B21001_017E", "B21001_017M", "B21001_020E", "B21001_020M", "B21001_035E", "B21001_035M", "B21001_038E", "B21001_038M"],
            "B21001_002E", "B21001_002M",
        ),
        "civilian_pct_65plus": _b21001_rate(
            cells,
            ["B21001_018E", "B21001_018M", "B21001_021E", "B21001_021M", "B21001_036E", "B21001_036M", "B21001_039E", "B21001_039M"],
            "B21001_003E", "B21001_003M",
        ),
    }


# B21003 -- Veteran Status by Educational Attainment (civilian population 25+).
# Codes confirmed live 2015-2023.
_B21003_VARS = [
    "B21003_002E", "B21003_002M",  # veteran, total
    "B21003_003E", "B21003_003M",  # veteran, less than HS
    "B21003_006E", "B21003_006M",  # veteran, bachelor's or higher
    "B21003_007E", "B21003_007M",  # nonveteran (civilian), total
    "B21003_008E", "B21003_008M",  # nonveteran, less than HS
    "B21003_011E", "B21003_011M",  # nonveteran, bachelor's or higher
]


def _b21003_derive(cells: dict[str, str], year: int) -> dict[str, tuple[float, float]]:
    return {
        "veteran_pct_less_than_hs": _b21001_rate(cells, ["B21003_003E", "B21003_003M"], "B21003_002E", "B21003_002M"),
        "civilian_pct_less_than_hs": _b21001_rate(cells, ["B21003_008E", "B21003_008M"], "B21003_007E", "B21003_007M"),
        "veteran_pct_bachelors_plus": _b21001_rate(cells, ["B21003_006E", "B21003_006M"], "B21003_002E", "B21003_002M"),
        "civilian_pct_bachelors_plus": _b21001_rate(cells, ["B21003_011E", "B21003_011M"], "B21003_007E", "B21003_007M"),
    }


# B21002 -- Period of Military Service for Civilian Veterans 18+. Veteran-only table (no
# civilian population to compare against) -- shows which service eras a county's veteran
# population belongs to. Codes confirmed live 2015-2023.
_B21002_POST911 = ["B21002_002E", "B21002_002M", "B21002_003E", "B21002_003M", "B21002_004E", "B21002_004M"]
_B21002_VIETNAM = ["B21002_006E", "B21002_006M", "B21002_007E", "B21002_007M", "B21002_008E", "B21002_008M", "B21002_009E", "B21002_009M"]
_B21002_VARS = ["B21002_001E", "B21002_001M"] + _B21002_POST911 + _B21002_VIETNAM


def _b21002_derive(cells: dict[str, str], year: int) -> dict[str, tuple[float, float]]:
    return {
        "veteran_pct_post911": _b21001_rate(cells, _B21002_POST911, "B21002_001E", "B21002_001M"),
        "veteran_pct_vietnam_era": _b21001_rate(cells, _B21002_VIETNAM, "B21002_001E", "B21002_001M"),
    }


# C21001<A/B/I> -- Sex by Age by Veteran Status, iterated by race/ethnicity (same shape as
# B21001 but split by race). Self-contained within each table: veteran share of that racial
# group's 18+ population, so comparable across race groups (White/Black/Hispanic here) and to
# the overall veteran_pct. Not a veteran-vs-civilian split -- see the scope field. Codes
# confirmed live 2015-2023.
_RACE_ITER_VET_CODES = ["_004E", "_004M", "_007E", "_007M", "_011E", "_011M", "_014E", "_014M"]


def _race_iter_vars(prefix: str) -> list[str]:
    return [f"{prefix}_001E", f"{prefix}_001M"] + [f"{prefix}{c}" for c in _RACE_ITER_VET_CODES]


def _race_iter_derive(metric_key: str, prefix: str) -> Callable[[dict[str, str], int], dict[str, tuple[float, float]]]:
    def derive(cells: dict[str, str], year: int) -> dict[str, tuple[float, float]]:
        vet_codes = [f"{prefix}{c}" for c in _RACE_ITER_VET_CODES]
        return {metric_key: _b21001_rate(cells, vet_codes, f"{prefix}_001E", f"{prefix}_001M")}

    return derive


# DP04 -- Selected Housing Characteristics. Whole-population measures: ACS has no table
# cross-tabulating homeownership/tenure, housing costs, cost burden, or vehicle access by
# veteran status (same live-API check as the DP03G note above -- only B21001/B21002/B21003/
# B21004/B21005/B21100/C21007 cross-tab by veteran status, none of which are housing-related).
# Included as total-population context, labeled accordingly via `scope`. Codes confirmed live
# 2015-2024.
_DP04_VARS = [
    "DP04_0046PE", "DP04_0046PM",  # owner-occupied, % of occupied units
    "DP04_0047PE", "DP04_0047PM",  # renter-occupied, % of occupied units
    "DP04_0058PE", "DP04_0058PM",  # no vehicles available, % of occupied units
    "DP04_0101E", "DP04_0101M",    # median monthly owner costs, units with a mortgage
    "DP04_0134E", "DP04_0134M",    # median gross rent
    "DP04_0110E", "DP04_0110M",    # SMOCAPI computable, with mortgage
    "DP04_0114E", "DP04_0114M",    # SMOCAPI 30.0-34.9%, with mortgage
    "DP04_0115E", "DP04_0115M",    # SMOCAPI 35.0%+, with mortgage
    "DP04_0117E", "DP04_0117M",    # SMOCAPI computable, without mortgage
    "DP04_0123E", "DP04_0123M",    # SMOCAPI 30.0-34.9%, without mortgage
    "DP04_0124E", "DP04_0124M",    # SMOCAPI 35.0%+, without mortgage
    "DP04_0136E", "DP04_0136M",    # GRAPI computable
    "DP04_0141E", "DP04_0141M",    # GRAPI 30.0-34.9%
    "DP04_0142E", "DP04_0142M",    # GRAPI 35.0%+
]


def _dp04_derive(cells: dict[str, str], year: int) -> dict[str, tuple[float, float]]:
    # Owner cost burden spans two SMOCAPI subpopulations (with/without a mortgage), so it
    # can't use _b21001_rate's single-total shape -- combine both totals and both burdened
    # brackets by hand instead.
    owner_computable = _cell(cells, "DP04_0110E") + _cell(cells, "DP04_0117E")
    owner_computable_moe = moe_sum(_cell(cells, "DP04_0110M"), _cell(cells, "DP04_0117M"))
    owner_burdened = sum(_cell(cells, c) for c in ("DP04_0114E", "DP04_0115E", "DP04_0123E", "DP04_0124E"))
    owner_burdened_moe = moe_sum(*(_cell(cells, c) for c in ("DP04_0114M", "DP04_0115M", "DP04_0123M", "DP04_0124M")))
    if owner_computable:
        owner_burdened_pct = (
            owner_burdened / owner_computable * 100,
            moe_ratio(owner_burdened, owner_burdened_moe, owner_computable, owner_computable_moe) * 100,
        )
    else:
        owner_burdened_pct = (0.0, 0.0)

    renter_computable, renter_computable_moe = _cell(cells, "DP04_0136E"), _cell(cells, "DP04_0136M")
    renter_burdened = sum(_cell(cells, c) for c in ("DP04_0141E", "DP04_0142E"))
    renter_burdened_moe = moe_sum(*(_cell(cells, c) for c in ("DP04_0141M", "DP04_0142M")))
    if renter_computable:
        renter_burdened_pct = (
            renter_burdened / renter_computable * 100,
            moe_ratio(renter_burdened, renter_burdened_moe, renter_computable, renter_computable_moe) * 100,
        )
    else:
        renter_burdened_pct = (0.0, 0.0)

    return {
        "homeownership_rate": (_cell(cells, "DP04_0046PE"), _cell(cells, "DP04_0046PM")),
        "renter_occupied_pct": (_cell(cells, "DP04_0047PE"), _cell(cells, "DP04_0047PM")),
        "no_vehicle_pct": (_cell(cells, "DP04_0058PE"), _cell(cells, "DP04_0058PM")),
        "median_owner_cost_mortgaged": (_cell(cells, "DP04_0101E"), _cell(cells, "DP04_0101M")),
        "median_gross_rent": (_cell(cells, "DP04_0134E"), _cell(cells, "DP04_0134M")),
        "cost_burdened_owner_pct": owner_burdened_pct,
        "cost_burdened_renter_pct": renter_burdened_pct,
    }


TABLES: dict[str, TableSpec] = {
    "DP02": TableSpec("DP02", "acs/acs5/profile", _dp02_vars, _dp02_derive),
    "C21007": TableSpec("C21007", "acs/acs5", lambda year: _C21007_VARS, _c21007_derive),
    "B21100": TableSpec("B21100", "acs/acs5", lambda year: _B21100_VARS, _b21100_derive),
    "C27009": TableSpec("C27009", "acs/acs5", lambda year: _C27009_VARS, _c27009_derive),
    "B21005": TableSpec("B21005", "acs/acs5", lambda year: _B21005_VARS, _b21005_derive),
    "B21004": TableSpec("B21004", "acs/acs5", lambda year: _B21004_VARS, _b21004_derive),
    "DP03G": TableSpec("DP03G", "acs/acs5/profile", lambda year: _DP03G_VARS, _dp03g_derive),
    "B21001": TableSpec("B21001", "acs/acs5", lambda year: _B21001_VARS, _b21001_derive),
    "B21003": TableSpec("B21003", "acs/acs5", lambda year: _B21003_VARS, _b21003_derive),
    "B21002": TableSpec("B21002", "acs/acs5", lambda year: _B21002_VARS, _b21002_derive),
    "C21001A": TableSpec("C21001A", "acs/acs5", lambda year: _race_iter_vars("C21001A"), _race_iter_derive("veteran_pct_white", "C21001A")),
    "C21001B": TableSpec("C21001B", "acs/acs5", lambda year: _race_iter_vars("C21001B"), _race_iter_derive("veteran_pct_black", "C21001B")),
    "C21001I": TableSpec("C21001I", "acs/acs5", lambda year: _race_iter_vars("C21001I"), _race_iter_derive("veteran_pct_hispanic", "C21001I")),
    "DP04": TableSpec("DP04", "acs/acs5/profile", lambda year: _DP04_VARS, _dp04_derive),
}


@dataclass
class MapMetricSpec:
    key: str
    label: str
    table_id: str
    colorscale: str
    value_format: str  # "percent" | "currency" | "count" | "minutes"
    condition: str = ""  # optional grouping key, e.g. a vital-conditions framework category
    direction: str = "neutral"  # "higher_better" | "lower_better" | "neutral" -- for concern/highlight flagging
    # "veteran" | "civilian" | "total_population" -- who the value is a percentage/measure OF.
    # "civilian" = the nonveteran population (true veteran-vs-civilian pairs use this against a
    # "veteran"-scoped counterpart); "total_population" = everyone, veterans included.
    scope: str = "veteran"


MAP_METRICS: list[MapMetricSpec] = [
    MapMetricSpec("veteran_pct", "Veteran % (18+ population)", "DP02", "Blues", "percent"),
    MapMetricSpec(
        "veteran_poverty_rate", "Poverty rate among Veterans", "C21007", "Oranges", "percent", direction="lower_better"
    ),
    MapMetricSpec(
        "disability_rating_pct", "% Veterans with service-connected disability rating", "B21100", "Purples", "percent"
    ),
    MapMetricSpec(
        "va_healthcare_pct",
        "VA health care coverage (% of total population)",
        "C27009",
        "Greens",
        "percent",
        direction="higher_better",
        scope="total_population",
    ),
    MapMetricSpec(
        "veteran_unemployment_rate", "Veteran unemployment rate", "B21005", "Reds", "percent", direction="lower_better"
    ),
    MapMetricSpec(
        "veteran_median_income", "Median income, Veterans", "B21004", "Tealgrn", "currency", direction="higher_better"
    ),
    MapMetricSpec(
        "snap_pct",
        "SNAP/food assistance receipt (% of households, total population)",
        "DP03G",
        "Purples",
        "percent",
        direction="lower_better",
        scope="total_population",
    ),
    MapMetricSpec(
        "no_health_insurance_pct",
        "No health insurance coverage (% of total population)",
        "DP03G",
        "Oranges",
        "percent",
        direction="lower_better",
        scope="total_population",
    ),
    # --- Demographics: age/sex, education, service era, race -- true veteran-vs-civilian
    # pairs where Census publishes the cross-tab (B21001/B21003), veteran-only where it
    # doesn't (B21002), and veteran representation within race groups (C21001A/B/I).
    MapMetricSpec("veteran_pct_male", "% Male, Veterans", "B21001", "Blues", "percent", scope="veteran"),
    MapMetricSpec("civilian_pct_male", "% Male, Civilians (non-veterans)", "B21001", "Bluyl", "percent", scope="civilian"),
    MapMetricSpec("veteran_pct_65plus", "% Age 65+, Veterans", "B21001", "Purples", "percent", scope="veteran"),
    MapMetricSpec("civilian_pct_65plus", "% Age 65+, Civilians (non-veterans)", "B21001", "Purpor", "percent", scope="civilian"),
    MapMetricSpec(
        "veteran_pct_less_than_hs", "% Less than high school, Veterans", "B21003", "Reds", "percent",
        direction="lower_better", scope="veteran",
    ),
    MapMetricSpec(
        "civilian_pct_less_than_hs", "% Less than high school, Civilians (non-veterans)", "B21003", "Burg", "percent",
        direction="lower_better", scope="civilian",
    ),
    MapMetricSpec(
        "veteran_pct_bachelors_plus", "% Bachelor's degree or higher, Veterans", "B21003", "Greens", "percent",
        direction="higher_better", scope="veteran",
    ),
    MapMetricSpec(
        "civilian_pct_bachelors_plus", "% Bachelor's degree or higher, Civilians (non-veterans)", "B21003", "Tealgrn",
        "percent", direction="higher_better", scope="civilian",
    ),
    MapMetricSpec("veteran_pct_post911", "% Post-9/11 era, Veterans", "B21002", "Sunset", "percent", scope="veteran"),
    MapMetricSpec("veteran_pct_vietnam_era", "% Vietnam era, Veterans", "B21002", "Agsunset", "percent", scope="veteran"),
    MapMetricSpec(
        "veteran_pct_white", "Veteran share of White population (18+)", "C21001A", "Blues", "percent",
        scope="total_population",
    ),
    MapMetricSpec(
        "veteran_pct_black", "Veteran share of Black or African American population (18+)", "C21001B", "Purples",
        "percent", scope="total_population",
    ),
    MapMetricSpec(
        "veteran_pct_hispanic", "Veteran share of Hispanic or Latino population (18+)", "C21001I", "Oranges",
        "percent", scope="total_population",
    ),
    # --- Housing & vehicle access: whole-population only (see DP04 note above), included as
    # context alongside the veteran-specific metrics.
    MapMetricSpec(
        "homeownership_rate", "Homeownership rate (% of occupied units, total population)", "DP04", "Tealgrn",
        "percent", direction="higher_better", scope="total_population",
    ),
    MapMetricSpec(
        "renter_occupied_pct", "Renter-occupied housing units (% of occupied units, total population)", "DP04",
        "Purples", "percent", scope="total_population",
    ),
    MapMetricSpec(
        "no_vehicle_pct", "No vehicle available (% of occupied units, total population)", "DP04", "Reds", "percent",
        direction="lower_better", scope="total_population",
    ),
    MapMetricSpec(
        "median_owner_cost_mortgaged", "Median monthly owner costs, mortgaged homes (total population)", "DP04",
        "Greens", "currency", scope="total_population",
    ),
    MapMetricSpec(
        "median_gross_rent", "Median gross rent (total population)", "DP04", "Bluyl", "currency",
        scope="total_population",
    ),
    MapMetricSpec(
        "cost_burdened_owner_pct", "Cost-burdened homeowners, 30%+ of income (total population)", "DP04", "Oranges",
        "percent", direction="lower_better", scope="total_population",
    ),
    MapMetricSpec(
        "cost_burdened_renter_pct", "Cost-burdened renters, 30%+ of income (total population)", "DP04", "Burgyl",
        "percent", direction="lower_better", scope="total_population",
    ),
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
