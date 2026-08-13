"""
Same pipeline as generate_report.py, but for the Vital Conditions framework
(rippel.org/vital-conditions) instead of veteran-specific metrics: general-population
ACS estimates (3 Data Profile tables x 2015-2024 x all 100 NC counties), rendered as a
choropleth + trend chart + full county data table.

Usage:
    venv/Scripts/python.exe generate_vital_report.py
    -> writes vital_conditions.html at the repo root
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from acs_metrics import all_counties_table, build_store, county_metrics_for, focus_county_card, trend_series
from acs_tables import YEARS, focus_fips
from geo_map import build_choropleth, build_trend_chart, get_nc_counties_geojson, render_html
from scrape_acs_resources import scrape
from vital_tables import VITAL_CONDITIONS, VITAL_MAP_METRICS, VITAL_TABLES

BASE_DIR = Path(__file__).parent
OUTPUT_PATH = BASE_DIR / "vital_conditions.html"


def _resolve_map_year(store) -> int:
    """Latest year where every map metric has data -- keeps every dropdown option non-empty."""
    per_metric_latest = [
        max((y for y in YEARS if county_metrics_for(store, m.key, y)), default=YEARS[-1]) for m in VITAL_MAP_METRICS
    ]
    return min(per_metric_latest)


def build_report() -> None:
    scraped = scrape()
    store = build_store(tables=VITAL_TABLES)

    county_order = list(focus_fips().items())  # [(county_name, fips), ...]
    focus_fips_set = {fips for _, fips in county_order}

    map_year = _resolve_map_year(store)

    geojson = get_nc_counties_geojson()
    data_by_metric_year = {
        m.key: {y: county_metrics_for(store, m.key, y) for y in YEARS} for m in VITAL_MAP_METRICS
    }
    map_fig = build_choropleth(geojson, VITAL_MAP_METRICS, data_by_metric_year, focus_fips_set, YEARS, map_year)
    map_html = render_html(map_fig, include_plotlyjs="cdn")

    trend_data = {
        m.key: {fips: trend_series(store, m.key, fips) for _, fips in county_order} for m in VITAL_MAP_METRICS
    }
    trend_fig = build_trend_chart(VITAL_MAP_METRICS, trend_data, county_order)
    trend_html = render_html(trend_fig, include_plotlyjs=False)  # plotly.js already loaded by the map

    focus_cards = [
        {"name": name, "fips": fips, "metrics": focus_county_card(store, fips, map_year, metrics=VITAL_MAP_METRICS)}
        for name, fips in county_order
    ]

    all_counties = all_counties_table(store, map_year, VITAL_MAP_METRICS)

    table_labels: dict[str, list[str]] = {}
    for m in VITAL_MAP_METRICS:
        table_labels.setdefault(m.table_id, []).append(m.label)
    data_status = [
        {
            "table_id": d["table_id"],
            "metric_labels": ", ".join(table_labels.get(d["table_id"], [d["table_id"]])),
            "source": d["source"],
            "as_of_year": d["as_of_year"],
        }
        for d in store.data_status
        if d["year"] == map_year
    ]

    env = Environment(loader=FileSystemLoader(BASE_DIR / "templates"))
    env.filters["fmt"] = lambda value, value_format: (
        f"${value:,.0f}"
        if value_format == "currency"
        else f"{value:,.0f}"
        if value_format == "count"
        else f"{value:.0f} min"
        if value_format == "minutes"
        else f"{value:.1f}%"
    )
    template = env.get_template("vital_report_template.html")
    html = template.render(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        map_year=map_year,
        years=YEARS,
        map_html=map_html,
        trend_html=trend_html,
        map_metrics=VITAL_MAP_METRICS,
        vital_conditions=VITAL_CONDITIONS,
        focus_cards=focus_cards,
        all_counties=all_counties,
        all_metrics=VITAL_MAP_METRICS,
        data_status=data_status,
        popular_tables=scraped["popular_tables"],
        resource_links=scraped["resource_links"],
    )

    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    build_report()
