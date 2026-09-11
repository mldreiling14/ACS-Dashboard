"""
Orchestrates the whole pipeline: fetch real veteran-related ACS estimates (6
tables x 2015-2024 x all 100 NC counties) via acs_fetch/acs_metrics, build the
choropleth/trend/bar Plotly figures via geo_map, and render a single static
index.html -- a statewide map (Map tab) plus statewide trend/ranked-bar charts
and a full data table (Chart/Table tabs) that clicks through to a per-county
executive-summary page (counties/<slug>.html, one per county, generated in the
same pass).

Usage:
    venv/Scripts/python.exe generate_report.py
    -> writes index.html + counties/*.html at the repo root, for GitHub Pages
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from acs_metrics import all_counties_table, build_county_summary, build_store, county_metrics_for, peer_average, trend_series
from acs_tables import ALL_TABLE_METRICS, MAP_METRICS, YEARS
from geo_map import (
    STATIC_MAP_CONFIG,
    build_bar_chart,
    build_choropleth,
    build_statewide_trend_chart,
    get_nc_counties_geojson,
    render_html,
)
from vital_tables import COMPARE_METRICS, COMPARE_TABLES

BASE_DIR = Path(__file__).parent
OUTPUT_PATH = BASE_DIR / "index.html"
COUNTIES_DIR = BASE_DIR / "counties"

# One-line plain-language descriptions for the "What this map shows" reference card --
# MAP_METRICS itself carries no prose, just the id/label/table/color/format/direction/scope
# needed to fetch and render each metric. Every description is explicit about *whose*
# population the percentage is measured against (Veterans vs. everyone) -- ACS only
# cross-tabulates a handful of things by Veteran status at all (poverty, unemployment, income,
# education, disability rating); SNAP receipt and general health insurance coverage have no
# Veteran-specific breakdown anywhere in ACS, so those two are necessarily total-population
# context metrics, not Veteran-specific ones.
METRIC_DESCRIPTIONS: dict[str, str] = {
    "veteran_pct": "Share of the county's 18+ population who are Veterans.",
    "veteran_poverty_rate": "Share of Veterans (not the general population) living below the poverty line.",
    "disability_rating_pct": "Share of Veterans (not the general population) with a VA-recognized service-connected disability rating.",
    "va_healthcare_pct": "Share of the TOTAL population (Veteran and non-Veteran) covered by VA health care -- a coverage-reach proxy, not a Veteran-only rate.",
    "veteran_unemployment_rate": "Share of Veterans in the labor force (not the general population) who are unemployed.",
    "veteran_median_income": "Median personal income among Veterans specifically, not the general population.",
    "snap_pct": "Share of ALL households (not Veteran-specific -- ACS doesn't cross-tabulate SNAP receipt by Veteran status) receiving SNAP/food-stamp benefits.",
    "no_health_insurance_pct": "Share of the TOTAL population (not Veteran-specific -- ACS doesn't cross-tabulate general health insurance coverage by Veteran status) with no health insurance coverage.",
}


def _resolve_map_year(store) -> int:
    """Latest year where every map metric has data -- keeps every dropdown option non-empty."""
    per_metric_latest = [
        max((y for y in YEARS if county_metrics_for(store, m.key, y)), default=YEARS[-1]) for m in MAP_METRICS
    ]
    return min(per_metric_latest)


def _top_counties(store, metric_key: str, year: int, n: int = 7) -> list[tuple[str, str]]:
    """That metric's own top-`n` counties by value at `year`, descending -- not a fixed
    cross-metric subset, so a metric like poverty rate surfaces its own highest counties rather
    than whichever 7 happen to be "focus counties" for an unrelated reason."""
    rows = sorted(county_metrics_for(store, metric_key, year), key=lambda r: r.value, reverse=True)[:n]
    return [(r.county_name, r.fips) for r in rows]


def build_report() -> None:
    store = build_store()
    compare_store = build_store(tables=COMPARE_TABLES)

    map_year = _resolve_map_year(store)

    geojson = get_nc_counties_geojson()
    data_by_metric_year = {m.key: {y: county_metrics_for(store, m.key, y) for y in YEARS} for m in MAP_METRICS}
    # No focus-county subset any more -- every county gets the same map styling and the same
    # executive-summary page, so there's nothing to outline differently.
    map_fig = build_choropleth(geojson, MAP_METRICS, data_by_metric_year, set(), YEARS, map_year)
    map_html = render_html(map_fig, include_plotlyjs="cdn", config=STATIC_MAP_CONFIG)

    # Chart tab: every county's trend line is available for every metric, but the page's own JS
    # controls -- not a Python-baked dropdown/rangeslider -- decide which ones actually show (see
    # build_statewide_trend_chart's docstring). A handful of counties are pre-selected here just
    # so the chart isn't empty on first paint; the reader picks their own from there.
    county_order = sorted(store.county_names.items(), key=lambda kv: kv[1])  # [(fips, name), ...] alphabetical
    all_series_by_metric_fips = {
        m.key: {fips: trend_series(store, m.key, fips) for fips, _ in county_order} for m in MAP_METRICS
    }
    nc_average_by_metric = {
        m.key: [(y, avg[0]) for y in YEARS if (avg := peer_average(store, m.key, y)) is not None] for m in MAP_METRICS
    }
    default_fips = [fips for _, fips in _top_counties(store, MAP_METRICS[0].key, map_year, n=5)]
    trend_fig, trend_trace_meta = build_statewide_trend_chart(
        MAP_METRICS,
        all_series_by_metric_fips,
        [(name, fips) for fips, name in county_order],
        nc_average_by_metric,
        default_metric_key=MAP_METRICS[0].key,
        default_fips=default_fips,
    )
    trend_html = render_html(trend_fig, include_plotlyjs=False)  # plotly.js already loaded by the map

    # Top-20 ranked bar view for the map's snapshot year.
    bar_fig = build_bar_chart(MAP_METRICS, {m.key: data_by_metric_year[m.key][map_year] for m in MAP_METRICS}, top_n=20)
    bar_html = render_html(bar_fig, include_plotlyjs=False)

    # Table tab: every county, every metric, snapshot at map_year.
    all_counties = all_counties_table(store, map_year, ALL_TABLE_METRICS)

    env = Environment(loader=FileSystemLoader(BASE_DIR / "templates"))
    env.filters["fmt"] = lambda value, value_format: (
        f"${value:,.0f}" if value_format == "currency" else f"{value:,.0f}" if value_format == "count" else f"{value:.1f}%"
    )
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")

    # One executive-summary page per county, built off the same `store`/`compare_store`
    # already fetched above, so this adds no extra API calls.
    county_template = env.get_template("county_report_template.html")
    COUNTIES_DIR.mkdir(exist_ok=True)
    county_links: dict[str, dict[str, str]] = {}
    for fips, name in store.county_names.items():
        summary = build_county_summary(store, compare_store, fips, map_year, metrics=MAP_METRICS, compare_metrics=COMPARE_METRICS)
        county_links[fips] = {"name": name, "slug": summary["slug"]}
        county_html = county_template.render(
            generated_at=generated_at,
            map_year=map_year,
            summary=summary,
        )
        (COUNTIES_DIR / f"{summary['slug']}.html").write_text(county_html, encoding="utf-8")
    print(f"Wrote {len(county_links)} county pages to {COUNTIES_DIR}")

    template = env.get_template("report_template.html")
    html = template.render(
        generated_at=generated_at,
        map_year=map_year,
        years=YEARS,
        map_html=map_html,
        trend_html=trend_html,
        bar_html=bar_html,
        map_metrics=MAP_METRICS,
        metric_descriptions=METRIC_DESCRIPTIONS,
        all_counties=all_counties,
        all_metrics=ALL_TABLE_METRICS,
        trend_county_order=[(fips, name) for fips, name in county_order],
        default_trend_fips=set(default_fips),
        default_trend_metric=MAP_METRICS[0].key,
        trend_trace_meta_json=json.dumps(trend_trace_meta),
        county_links=county_links,
        county_links_json=json.dumps(county_links),
    )

    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    build_report()
