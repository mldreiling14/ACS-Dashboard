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
from vital_tables import COMPARE_METRICS, COMPARE_TABLES, VITAL_CONDITIONS, VITAL_MAP_METRICS, VITAL_TABLES

BASE_DIR = Path(__file__).parent
OUTPUT_PATH = BASE_DIR / "vital_conditions.html"

# Plain-language reliability explanations, shown as hover text next to every reliability dot --
# the CV-based reliable/caution/unreliable flags mean nothing to a reader who isn't a statistician.
RELIABILITY_EXPLAINER = {
    "reliable": "Reliable: the margin of error is small relative to the estimate, so this number is fairly precise.",
    "caution": "Use with caution: the margin of error is fairly large relative to the estimate. Treat this as an approximate figure, not an exact one.",
    "unreliable": "Unreliable: the margin of error is very large relative to the estimate. Treat this as a rough signal only, not a precise number.",
}


def _resolve_map_year(store) -> int:
    """Latest year where every map metric has data -- keeps every dropdown option non-empty."""
    per_metric_latest = [
        max((y for y in YEARS if county_metrics_for(store, m.key, y)), default=YEARS[-1]) for m in VITAL_MAP_METRICS
    ]
    return min(per_metric_latest)


def _compare_focus_blocks(compare_store, county_order: list[tuple[str, str]], year: int) -> list[dict]:
    """One block per comparison metric: focus-county veteran/civilian pairs, scaled to a
    shared max so both bars in a block are drawn to the same axis."""
    blocks = []
    for cm in COMPARE_METRICS:
        rows = []
        values = []
        for name, fips in county_order:
            vet = compare_store.by_metric.get(cm.veteran_key, {}).get(year, {}).get(fips)
            civ = compare_store.by_metric.get(cm.civilian_key, {}).get(year, {}).get(fips) if cm.civilian_key else None
            rows.append({"name": name, "veteran": vet, "civilian": civ})
            if vet:
                values.append(vet.value)
            if civ:
                values.append(civ.value)
        max_val = max(values, default=1) or 1
        for row in rows:
            row["veteran_pct"] = round(row["veteran"].value / max_val * 100, 1) if row["veteran"] else 0
            row["civilian_pct"] = round(row["civilian"].value / max_val * 100, 1) if row["civilian"] else 0
        blocks.append({"metric": cm, "rows": rows})
    return blocks


def _compare_all_counties(compare_store, year: int) -> list[dict]:
    """Every county's veteran/civilian pair for every comparison metric -- feeds the
    second full-data table."""
    fips_set: set[str] = set()
    for cm in COMPARE_METRICS:
        fips_set.update(compare_store.by_metric.get(cm.veteran_key, {}).get(year, {}).keys())
        if cm.civilian_key:
            fips_set.update(compare_store.by_metric.get(cm.civilian_key, {}).get(year, {}).keys())
    rows = [
        {
            "name": compare_store.county_names.get(fips, fips),
            "fips": fips,
            "metrics": {
                cm.key: {
                    "veteran": compare_store.by_metric.get(cm.veteran_key, {}).get(year, {}).get(fips),
                    "civilian": (
                        compare_store.by_metric.get(cm.civilian_key, {}).get(year, {}).get(fips)
                        if cm.civilian_key
                        else None
                    ),
                }
                for cm in COMPARE_METRICS
            },
        }
        for fips in fips_set
    ]
    return sorted(rows, key=lambda r: r["name"])


def build_report() -> None:
    scraped = scrape()
    store = build_store(tables=VITAL_TABLES)
    compare_store = build_store(tables=COMPARE_TABLES)

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

    compare_focus = _compare_focus_blocks(compare_store, county_order, map_year)
    compare_all_counties = _compare_all_counties(compare_store, map_year)

    table_labels: dict[str, list[str]] = {}
    for m in VITAL_MAP_METRICS:
        table_labels.setdefault(m.table_id, []).append(m.label)

    compare_table_labels: dict[str, list[str]] = {
        "C21007_CMP": ["Poverty rate (Veteran/civilian)"],
        "B21005_CMP": ["Unemployment rate (Veteran/civilian)"],
        "B21004_CMP": ["Median personal income (Veteran/civilian)"],
        "B21003_CMP": ["Educational attainment (Veteran/civilian)"],
        "B21100_CMP": ["Service-connected disability rating"],
    }
    data_status = [
        {
            "table_id": d["table_id"],
            "metric_labels": ", ".join(table_labels.get(d["table_id"], [d["table_id"]])),
            "source": d["source"],
            "as_of_year": d["as_of_year"],
        }
        for d in store.data_status
        if d["year"] == map_year
    ] + [
        {
            "table_id": d["table_id"],
            "metric_labels": ", ".join(compare_table_labels.get(d["table_id"], [d["table_id"]])),
            "source": d["source"],
            "as_of_year": d["as_of_year"],
        }
        for d in compare_store.data_status
        if d["year"] == map_year
    ]

    env = Environment(loader=FileSystemLoader(BASE_DIR / "templates"))
    env.filters["fmt"] = lambda value, value_format: (
        (f"-${abs(value):,.0f}" if value < 0 else f"${value:,.0f}")
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
        compare_focus=compare_focus,
        compare_all_counties=compare_all_counties,
        compare_metrics=COMPARE_METRICS,
        reliability_explainer=RELIABILITY_EXPLAINER,
        data_status=data_status,
        popular_tables=scraped["popular_tables"],
        resource_links=scraped["resource_links"],
    )

    OUTPUT_PATH.write_text(html, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    build_report()
