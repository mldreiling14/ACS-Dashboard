"""
Orchestrates the whole pipeline: scrape the ACS landing page for context links,
fetch real veteran-related ACS estimates (6 tables x 2015-2024 x all 100 NC
counties) via acs_fetch/acs_metrics, build the choropleth + trend Plotly
figures via geo_map, and render everything into a single static HTML report.

Usage:
    venv/Scripts/python.exe generate_report.py
    -> writes index.html at the repo root, for GitHub Pages to serve directly
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from acs_metrics import (
    VETERAN_COUNT_LABEL,
    all_counties_table,
    build_store,
    county_metrics_for,
    focus_county_bar_rows,
    focus_county_card,
    trend_series,
)
from acs_tables import ALL_TABLE_METRICS, MAP_METRICS, YEARS, focus_fips
from geo_map import build_choropleth, build_trend_chart, get_nc_counties_geojson, render_html
from scrape_acs_resources import scrape

BASE_DIR = Path(__file__).parent
OUTPUT_PATH = BASE_DIR / "index.html"


def _resolve_map_year(store) -> int:
    """Latest year where every map metric has data -- keeps every dropdown option non-empty."""
    per_metric_latest = [
        max((y for y in YEARS if county_metrics_for(store, m.key, y)), default=YEARS[-1]) for m in MAP_METRICS
    ]
    return min(per_metric_latest)


def build_report() -> None:
    scraped = scrape()
    store = build_store()

    county_order = list(focus_fips().items())  # [(county_name, fips), ...] in FOCUS_COUNTY_FIPS order
    focus_fips_set = {fips for _, fips in county_order}

    map_year = _resolve_map_year(store)

    geojson = get_nc_counties_geojson()
    data_by_metric_year = {m.key: {y: county_metrics_for(store, m.key, y) for y in YEARS} for m in MAP_METRICS}
    map_fig = build_choropleth(geojson, MAP_METRICS, data_by_metric_year, focus_fips_set, YEARS, map_year)
    map_html = render_html(map_fig, include_plotlyjs="cdn")

    trend_data = {m.key: {fips: trend_series(store, m.key, fips) for _, fips in county_order} for m in MAP_METRICS}
    trend_fig = build_trend_chart(MAP_METRICS, trend_data, county_order)
    trend_html = render_html(trend_fig, include_plotlyjs=False)  # plotly.js already loaded by the map

    focus_cards = [
        {"name": name, "fips": fips, "metrics": focus_county_card(store, fips, map_year)} for name, fips in county_order
    ]

    all_counties = all_counties_table(store, map_year, ALL_TABLE_METRICS)

    bar_rows = focus_county_bar_rows(store, map_year)
    max_count = max((r.value for r in bar_rows), default=1)
    estimate_rows = [
        {"name": r.county_name, "value": r.value, "bar_pct": round(r.value / max_count * 100, 1)} for r in bar_rows
    ]

    table_labels: dict[str, list[str]] = {}
    for m in MAP_METRICS:
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
        f"${value:,.0f}" if value_format == "currency" else f"{value:,.0f}" if value_format == "count" else f"{value:.1f}%"
    )
    template = env.get_template("report_template.html")
    html = template.render(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        map_year=map_year,
        years=YEARS,
        map_html=map_html,
        trend_html=trend_html,
        map_metrics=MAP_METRICS,
        focus_cards=focus_cards,
        estimates=estimate_rows,
        variable_label=VETERAN_COUNT_LABEL,
        all_counties=all_counties,
        all_metrics=ALL_TABLE_METRICS,
        data_status=data_status,
        popular_tables=scraped["popular_tables"],
        resource_links=scraped["resource_links"],
    )

    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    build_report()
