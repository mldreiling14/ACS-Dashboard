"""
Shapes raw acs_fetch results into report-ready view models: per-county metrics
with MOE/reliability, multi-year trend series, per-focus-county cards, and a
data-provenance summary for the "where did this come from" section.
"""

from __future__ import annotations

from dataclasses import dataclass

from acs_fetch import get_all
from acs_tables import (
    FOCUS_COUNTY_FIPS,
    MAP_METRICS,
    NC_STATE_FIPS,
    TABLES,
    YEARS,
    coefficient_of_variation,
    reliability_flag,
)

VETERAN_COUNT_LABEL = "Civilian veterans"


@dataclass
class CountyMetric:
    fips: str
    county_name: str
    year: int
    metric_key: str
    value: float
    moe: float
    cv: float | None
    reliability: str
    source: str
    as_of_year: int | None


@dataclass
class MetricsStore:
    by_metric: dict[str, dict[int, dict[str, CountyMetric]]]  # metric_key -> year -> fips -> CountyMetric
    county_names: dict[str, str]  # fips -> "Alamance County"
    data_status: list[dict]


def _fips(row: dict[str, str]) -> str:
    return row["state"] + row["county"]


def _county_name(row: dict[str, str]) -> str:
    return row["NAME"].split(",")[0]


def build_store(force_refresh: bool = False) -> MetricsStore:
    fetch_results = get_all(force_refresh=force_refresh)

    by_metric: dict[str, dict[int, dict[str, CountyMetric]]] = {}
    county_names: dict[str, str] = {}
    data_status: list[dict] = []

    for (table_id, year), result in fetch_results.items():
        spec = TABLES[table_id]
        data_status.append(
            {"table_id": table_id, "year": year, "source": result.source, "as_of_year": result.as_of_year}
        )
        for row in result.county_rows:
            fips = _fips(row)
            county_names.setdefault(fips, _county_name(row))
            derived = spec.derive(row, result.as_of_year or year)
            for metric_key, (value, moe) in derived.items():
                cv = coefficient_of_variation(value, moe)
                metric = CountyMetric(
                    fips=fips,
                    county_name=county_names[fips],
                    year=year,
                    metric_key=metric_key,
                    value=value,
                    moe=moe,
                    cv=cv,
                    reliability=reliability_flag(cv),
                    source=result.source,
                    as_of_year=result.as_of_year,
                )
                by_metric.setdefault(metric_key, {}).setdefault(year, {})[fips] = metric

    return MetricsStore(by_metric=by_metric, county_names=county_names, data_status=data_status)


def county_metrics_for(store: MetricsStore, metric_key: str, year: int) -> list[CountyMetric]:
    return list(store.by_metric.get(metric_key, {}).get(year, {}).values())


def trend_series(store: MetricsStore, metric_key: str, fips: str) -> list[CountyMetric]:
    series = []
    for year in YEARS:
        metric = store.by_metric.get(metric_key, {}).get(year, {}).get(fips)
        if metric is not None:
            series.append(metric)
    return series


def focus_county_card(store: MetricsStore, fips: str, year: int) -> dict[str, CountyMetric | None]:
    return {metric.key: store.by_metric.get(metric.key, {}).get(year, {}).get(fips) for metric in MAP_METRICS}


def focus_county_bar_rows(store: MetricsStore, year: int) -> list[CountyMetric]:
    """Veteran-count rows for the 7 focus counties, sorted descending -- feeds the existing bar chart."""
    rows = []
    for county_fips in FOCUS_COUNTY_FIPS.values():
        metric = store.by_metric.get("veteran_count", {}).get(year, {}).get(NC_STATE_FIPS + county_fips)
        if metric is not None:
            rows.append(metric)
    return sorted(rows, key=lambda r: r.value, reverse=True)


def latest_year_with_data(store: MetricsStore, metric_key: str) -> int:
    for year in sorted(YEARS, reverse=True):
        if store.by_metric.get(metric_key, {}).get(year):
            return year
    return YEARS[-1]


if __name__ == "__main__":
    store = build_store()
    year = latest_year_with_data(store, "veteran_poverty_rate")
    rows = county_metrics_for(store, "veteran_poverty_rate", year)
    top10 = sorted(rows, key=lambda r: r.value, reverse=True)[:10]
    print(f"Top 10 counties by veteran poverty rate ({year}):")
    for r in top10:
        print(f"  {r.county_name:20s} {r.value:5.1f}%  MOE ±{r.moe:.1f}  [{r.reliability}]")
