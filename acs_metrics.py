"""
Shapes raw acs_fetch results into report-ready view models: per-county metrics
with MOE/reliability, multi-year trend series, per-focus-county cards, and a
data-provenance summary for the "where did this come from" section.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from acs_fetch import get_all
from acs_tables import (
    FOCUS_COUNTY_FIPS,
    MAP_METRICS,
    NC_STATE_FIPS,
    TABLES,
    YEARS,
    MapMetricSpec,
    TableSpec,
    coefficient_of_variation,
    moe_sum,
    reliability_flag,
)

VETERAN_COUNT_LABEL = "Civilian Veterans"

# Plain-language reliability explanations, shown as hover/detail text next to every reliability
# badge -- the CV-based reliable/caution/unreliable flags mean nothing to a reader who isn't a
# statistician. Shared by both report generators.
RELIABILITY_EXPLAINER = {
    "reliable": "Reliable: the margin of error is small relative to the estimate, so this number is fairly precise.",
    "caution": "Use with caution: the margin of error is fairly large relative to the estimate. Treat this as an approximate figure, not an exact one.",
    "unreliable": "Unreliable: the margin of error is very large relative to the estimate. Treat this as a rough signal only, not a precise number.",
}


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


def build_store(force_refresh: bool = False, tables: dict[str, TableSpec] = TABLES) -> MetricsStore:
    fetch_results = get_all(force_refresh=force_refresh, tables=tables)

    by_metric: dict[str, dict[int, dict[str, CountyMetric]]] = {}
    county_names: dict[str, str] = {}
    data_status: list[dict] = []

    for (table_id, year), result in fetch_results.items():
        spec = tables[table_id]
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


def focus_county_card(
    store: MetricsStore, fips: str, year: int, metrics: list[MapMetricSpec] = MAP_METRICS
) -> dict[str, CountyMetric | None]:
    return {metric.key: store.by_metric.get(metric.key, {}).get(year, {}).get(fips) for metric in metrics}


def focus_county_bar_rows(store: MetricsStore, year: int) -> list[CountyMetric]:
    """Veteran-count rows for the 7 focus counties, sorted descending -- feeds the existing bar chart."""
    rows = []
    for county_fips in FOCUS_COUNTY_FIPS.values():
        metric = store.by_metric.get("veteran_count", {}).get(year, {}).get(NC_STATE_FIPS + county_fips)
        if metric is not None:
            rows.append(metric)
    return sorted(rows, key=lambda r: r.value, reverse=True)


def all_counties_table(store: MetricsStore, year: int, metrics: list) -> list[dict]:
    """Every county with a row for the given year, across all `metrics` -- feeds the full data table."""
    fips_set: set[str] = set()
    for m in metrics:
        fips_set.update(store.by_metric.get(m.key, {}).get(year, {}).keys())
    rows = [
        {
            "name": store.county_names.get(fips, fips),
            "fips": fips,
            "metrics": {m.key: store.by_metric.get(m.key, {}).get(year, {}).get(fips) for m in metrics},
        }
        for fips in fips_set
    ]
    return sorted(rows, key=lambda r: r["name"])


def county_slug(county_name: str) -> str:
    """"Alamance County" -> "alamance"; "New Hanover County" -> "new-hanover". Filename-safe id
    for a county's generated executive-summary page."""
    base = county_name[: -len(" County")] if county_name.endswith(" County") else county_name
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")


def peer_average(store: MetricsStore, metric_key: str, year: int) -> tuple[float, float] | None:
    """Unweighted mean and stdev of one metric across every NC county with data for that year --
    a "typical NC county" baseline for flagging outliers. This is a mean-of-county-estimates, not
    the Census Bureau's own published statewide figure -- callers/templates should label it as
    such rather than implying more precision than it has."""
    values = [m.value for m in county_metrics_for(store, metric_key, year)]
    if not values:
        return None
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    return mean, variance**0.5


def _fmt_value(value: float, value_format: str) -> str:
    if value_format == "currency":
        return f"${value:,.0f}"
    if value_format == "count":
        return f"{value:,.0f}"
    if value_format == "minutes":
        return f"{value:.0f} min"
    return f"{value:.1f}%"


def build_county_summary(
    store: MetricsStore,
    compare_store: MetricsStore,
    fips: str,
    year: int,
    metrics: list[MapMetricSpec] = MAP_METRICS,
    compare_metrics: list = (),
) -> dict:
    """Executive-summary view model for one county's page: every veteran metric against its NC
    county-average baseline, every veteran-vs-civilian comparison, and the flagged highlights /
    concerns / reliability notes that fall out of both.

    A metric is flagged (highlight if favorable, concern if not) only when: it has a real
    "better/worse" direction (`direction != "neutral"`), its own estimate isn't already too noisy
    to trust (`reliability != "unreliable"`), and the gap clears that estimate's own margin of
    error -- so a flag reflects a real difference, not sampling noise.
    """
    county_name = store.county_names.get(fips, fips)

    veteran_metrics = []
    key_points: list[str] = []
    concerns: list[str] = []

    for m in metrics:
        metric = store.by_metric.get(m.key, {}).get(year, {}).get(fips)
        avg = peer_average(store, m.key, year)
        peer_avg = avg[0] if avg else None
        flag = None
        if (
            metric is not None
            and peer_avg is not None
            and m.direction != "neutral"
            and metric.reliability != "unreliable"
            and abs(metric.value - peer_avg) > metric.moe
        ):
            better = metric.value < peer_avg if m.direction == "lower_better" else metric.value > peer_avg
            flag = "highlight" if better else "concern"
        veteran_metrics.append({"metric": m, "county": metric, "peer_avg": peer_avg, "flag": flag})
        if flag:
            text = (
                f"{m.label}: {_fmt_value(metric.value, m.value_format)} "
                f"(NC county average: {_fmt_value(peer_avg, m.value_format)})"
            )
            (key_points if flag == "highlight" else concerns).append(text)

    compare_rows = []
    for cm in compare_metrics:
        vet = compare_store.by_metric.get(cm.veteran_key, {}).get(year, {}).get(fips)
        civ = compare_store.by_metric.get(cm.civilian_key, {}).get(year, {}).get(fips) if cm.civilian_key else None
        max_val = max((x.value for x in (vet, civ) if x is not None), default=1) or 1
        gap_flag = None
        if (
            vet is not None
            and civ is not None
            and cm.direction != "neutral"
            and vet.reliability != "unreliable"
            and civ.reliability != "unreliable"
            and abs(vet.value - civ.value) > moe_sum(vet.moe, civ.moe)
        ):
            better = vet.value < civ.value if cm.direction == "lower_better" else vet.value > civ.value
            gap_flag = "highlight" if better else "concern"
        compare_rows.append(
            {
                "metric": cm,
                "veteran": vet,
                "civilian": civ,
                "veteran_pct": round(vet.value / max_val * 100, 1) if vet else 0,
                "civilian_pct": round(civ.value / max_val * 100, 1) if civ else 0,
                "gap_flag": gap_flag,
            }
        )
        if gap_flag:
            text = (
                f"{cm.label}: {_fmt_value(vet.value, cm.value_format)} for Veterans vs "
                f"{_fmt_value(civ.value, cm.value_format)} for civilians in {county_name}"
            )
            (key_points if gap_flag == "highlight" else concerns).append(text)

    reliability_notes = [
        {
            "label": row["metric"].label,
            "reliability": row["county"].reliability,
            "explainer": RELIABILITY_EXPLAINER[row["county"].reliability],
        }
        for row in veteran_metrics
        if row["county"] is not None and row["county"].reliability != "reliable"
    ]
    for row in compare_rows:
        if row["veteran"] is not None and row["veteran"].reliability != "reliable":
            reliability_notes.append(
                {
                    "label": f"{row['metric'].label} (Veteran)",
                    "reliability": row["veteran"].reliability,
                    "explainer": RELIABILITY_EXPLAINER[row["veteran"].reliability],
                }
            )
        if row["civilian"] is not None and row["civilian"].reliability != "reliable":
            reliability_notes.append(
                {
                    "label": f"{row['metric'].label} (Civilian)",
                    "reliability": row["civilian"].reliability,
                    "explainer": RELIABILITY_EXPLAINER[row["civilian"].reliability],
                }
            )

    return {
        "fips": fips,
        "name": county_name,
        "slug": county_slug(county_name),
        "veteran_metrics": veteran_metrics,
        "compare_metrics": compare_rows,
        "key_points": key_points,
        "concerns": concerns,
        "reliability_notes": reliability_notes,
    }


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
