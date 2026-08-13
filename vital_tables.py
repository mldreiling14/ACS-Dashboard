"""
Declarative registry for the Vital Conditions framework (rippel.org/vital-conditions),
mirroring acs_tables.py's pattern but for general-population ACS Data Profile metrics
instead of the veteran-specific detailed tables.

Every variable code below was checked against the live Census API's variables.json
for every year 2015-2024 before being used here -- all are stable across that range
except the broadband-subscription code, which shifts (handled in `_broadband_code`).

Six of the seven vital conditions have a reasonable ACS Data Profile proxy. Thriving
Natural World (clean air/water, natural hazards, ecosystems) has no ACS equivalent --
that's EPA/EJScreen/FEMA territory, not a Census household survey -- so it's listed
here for framework completeness but carries no metric. Nothing is fabricated to fill
that gap.
"""

from __future__ import annotations

from dataclasses import dataclass

from acs_tables import _cell, MapMetricSpec, TableSpec


@dataclass
class VitalCondition:
    key: str
    label: str
    short_definition: str
    components: str
    has_acs_metric: bool = True


VITAL_CONDITIONS: list[VitalCondition] = [
    VitalCondition(
        "basic_needs",
        "Basic Needs for Health + Safety",
        "Basic requirements for health and safety",
        "Nutritious food, safe drinking water, fresh air, sufficient sleep, routine physical activity, safe "
        "sexuality and reproduction, freedom from trauma/violence/addiction/crime, routine physical and "
        "behavioral health care",
    ),
    VitalCondition(
        "humane_housing",
        "Humane Housing",
        "Humane, consistent housing",
        "Adequate space per person; safe structures; affordable costs; diverse neighborhoods (without "
        "gentrification, segregation, concentrated poverty); close to work, school, food, recreation, and nature",
    ),
    VitalCondition(
        "work_wealth",
        "Meaningful Work + Wealth",
        "Rewarding work, careers, and standards of living",
        "Job training/retraining; good-paying and fulfilling jobs; family and community wealth; savings and "
        "limited debt",
    ),
    VitalCondition(
        "lifelong_learning",
        "Lifelong Learning",
        "Continuous learning, education, and literacy",
        "Continuous development of cognitive, social, emotional abilities; early childhood experiences; "
        "elementary, high school, and higher education; career and adult education",
    ),
    VitalCondition(
        "transportation",
        "Reliable Transportation",
        "Reliable, safe, and accessible transportation",
        "Close to work, school, food, leisure; safe transport; active transport; efficient energy use; few "
        "environmental hazards",
    ),
    VitalCondition(
        "belonging",
        "Belonging + Civic Muscle",
        "Sense of belonging and power to shape a common world",
        "Social support; freedom from stigma, discrimination, oppression; arts, culture, and spiritual life; "
        "organizing, advocacy, leadership development; civic agency; equitable access to information, "
        "communications, and technology; welcoming, inclusive places; opportunities for civic engagement",
    ),
    VitalCondition(
        "natural_world",
        "Thriving Natural World",
        "Sustainable resources, contact with nature, freedom from hazards",
        "Clean air, water, soil; healthy ecosystems; accessible natural spaces; freedom from extreme heat, "
        "flooding, wind, radiation, earthquakes, pathogens",
        has_acs_metric=False,
    ),
]


# --- DP03 (Economic Characteristics) -- unemployment, poverty, income, health insurance, commute.
# All codes below confirmed stable 2015-2024 against the live Census API.
_DP03V_VARS = [
    "DP03_0009PE", "DP03_0009PM",  # Unemployment rate
    "DP03_0099PE", "DP03_0099PM",  # No health insurance coverage
    "DP03_0128PE", "DP03_0128PM",  # Poverty rate, all people
    "DP03_0062E", "DP03_0062M",    # Median household income
    "DP03_0025E", "DP03_0025M",    # Mean travel time to work (minutes)
]


def _dp03v_derive(cells: dict[str, str], year: int) -> dict[str, tuple[float, float]]:
    return {
        "unemployment_rate": (_cell(cells, "DP03_0009PE"), _cell(cells, "DP03_0009PM")),
        "no_health_insurance_pct": (_cell(cells, "DP03_0099PE"), _cell(cells, "DP03_0099PM")),
        "poverty_rate": (_cell(cells, "DP03_0128PE"), _cell(cells, "DP03_0128PM")),
        "median_household_income": (_cell(cells, "DP03_0062E"), _cell(cells, "DP03_0062M")),
        "mean_commute_minutes": (_cell(cells, "DP03_0025E"), _cell(cells, "DP03_0025M")),
    }


# --- DP04 (Housing Characteristics) -- vehicle access, rent cost burden.
_DP04V_VARS = [
    "DP04_0058PE", "DP04_0058PM",  # No vehicles available
    "DP04_0142PE", "DP04_0142PM",  # Renter-occupied units spending 35%+ of income on gross rent
]


def _dp04v_derive(cells: dict[str, str], year: int) -> dict[str, tuple[float, float]]:
    return {
        "no_vehicle_pct": (_cell(cells, "DP04_0058PE"), _cell(cells, "DP04_0058PM")),
        "housing_cost_burden_pct": (_cell(cells, "DP04_0142PE"), _cell(cells, "DP04_0142PM")),
    }


# --- DP02 (Social Characteristics) -- educational attainment, broadband access.
# Broadband-subscription variable shifts within DP02 as the Census Bureau adds rows to the
# table: _0152 through 2018, _0153 in 2019, _0154 from 2020 on (confirmed live per-year).
def _broadband_code(year: int) -> str:
    if year <= 2018:
        return "DP02_0152"
    if year == 2019:
        return "DP02_0153"
    return "DP02_0154"


def _dp02v_vars(year: int) -> list[str]:
    bb = _broadband_code(year)
    return ["DP02_0066PE", "DP02_0066PM", "DP02_0067PE", "DP02_0067PM", f"{bb}PE", f"{bb}PM"]


def _dp02v_derive(cells: dict[str, str], year: int) -> dict[str, tuple[float, float]]:
    bb = _broadband_code(year)
    return {
        "hs_grad_or_higher_pct": (_cell(cells, "DP02_0066PE"), _cell(cells, "DP02_0066PM")),
        "bachelors_or_higher_pct": (_cell(cells, "DP02_0067PE"), _cell(cells, "DP02_0067PM")),
        "broadband_access_pct": (_cell(cells, f"{bb}PE"), _cell(cells, f"{bb}PM")),
    }


# Internal table ids (DP02V/DP03V/DP04V) are distinct from acs_tables.TABLES' "DP02" etc. --
# both registries request different variable subsets from the same underlying Census Data
# Profile tables, so distinct ids keep their disk caches and fetches from colliding.
VITAL_TABLES: dict[str, TableSpec] = {
    "DP03V": TableSpec("DP03V", "acs/acs5/profile", lambda year: _DP03V_VARS, _dp03v_derive),
    "DP04V": TableSpec("DP04V", "acs/acs5/profile", lambda year: _DP04V_VARS, _dp04v_derive),
    "DP02V": TableSpec("DP02V", "acs/acs5/profile", _dp02v_vars, _dp02v_derive),
}


VITAL_MAP_METRICS: list[MapMetricSpec] = [
    MapMetricSpec("no_health_insurance_pct", "No health insurance coverage", "DP03V", "Oranges", "percent", "basic_needs"),
    MapMetricSpec("poverty_rate", "Poverty rate (all people)", "DP03V", "Oranges", "percent", "basic_needs"),
    MapMetricSpec("housing_cost_burden_pct", "Renters spending 35%+ of income on rent", "DP04V", "Purples", "percent", "humane_housing"),
    MapMetricSpec("unemployment_rate", "Unemployment rate", "DP03V", "Greens", "percent", "work_wealth"),
    MapMetricSpec("median_household_income", "Median household income", "DP03V", "Greens", "currency", "work_wealth"),
    MapMetricSpec("hs_grad_or_higher_pct", "High school graduate or higher", "DP02V", "Blues", "percent", "lifelong_learning"),
    MapMetricSpec("bachelors_or_higher_pct", "Bachelor's degree or higher", "DP02V", "Blues", "percent", "lifelong_learning"),
    MapMetricSpec("no_vehicle_pct", "Households with no vehicle available", "DP04V", "Reds", "percent", "transportation"),
    MapMetricSpec("mean_commute_minutes", "Mean commute time (minutes)", "DP03V", "Reds", "minutes", "transportation"),
    MapMetricSpec("broadband_access_pct", "Households with broadband internet", "DP02V", "Tealgrn", "percent", "belonging"),
]


if __name__ == "__main__":
    for year in (2015, 2018, 2019, 2020, 2023, 2024):
        print(year, "broadband code:", _broadband_code(year))
    print("Vital table ids:", list(VITAL_TABLES))
    print("Vital metrics:", [m.key for m in VITAL_MAP_METRICS])
