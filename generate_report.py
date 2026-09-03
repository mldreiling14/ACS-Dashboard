"""
Orchestrates the whole pipeline: fetch real veteran-related ACS estimates (6
tables x 2015-2024 x all 100 NC counties) via acs_fetch/acs_metrics, build the
choropleth Plotly figure via geo_map, and render a single static index.html --
a statewide map that clicks through to a per-county executive-summary page
(counties/<slug>.html, one per county, generated in the same pass).

Usage:
    venv/Scripts/python.exe generate_report.py
    -> writes index.html + counties/*.html at the repo root, for GitHub Pages
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from acs_metrics import build_county_summary, build_store, county_metrics_for
from acs_tables import MAP_METRICS, YEARS
from geo_map import STATIC_MAP_CONFIG, build_choropleth, get_nc_counties_geojson, render_html
from vital_tables import COMPARE_METRICS, COMPARE_TABLES

BASE_DIR = Path(__file__).parent
OUTPUT_PATH = BASE_DIR / "index.html"
COUNTIES_DIR = BASE_DIR / "counties"

# One-line plain-language descriptions for the "What this map shows" reference card --
# MAP_METRICS itself carries no prose, just the id/label/table/color/format/direction needed
# to fetch and render each metric.
METRIC_DESCRIPTIONS: dict[str, str] = {
    "veteran_pct": "Share of the 18+ population who are Veterans.",
    "veteran_poverty_rate": "Share of Veterans living below the poverty line.",
    "disability_rating_pct": "Share of Veterans with a VA-recognized service-connected disability rating.",
    "va_healthcare_pct": "Share of the total population covered by VA health care (not Veterans-only).",
    "veteran_unemployment_rate": "Share of Veterans in the labor force who are unemployed.",
    "veteran_median_income": "Median personal income among Veterans.",
}


def _resolve_map_year(store) -> int:
    """Latest year where every map metric has data -- keeps every dropdown option non-empty."""
    per_metric_latest = [
        max((y for y in YEARS if county_metrics_for(store, m.key, y)), default=YEARS[-1]) for m in MAP_METRICS
    ]
    return min(per_metric_latest)


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
        map_metrics=MAP_METRICS,
        metric_descriptions=METRIC_DESCRIPTIONS,
        county_links=county_links,
        county_links_json=json.dumps(county_links),
    )

    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    build_report()
