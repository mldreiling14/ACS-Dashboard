"""
Quick ad-hoc matplotlib figures over the acs_db SQLite database, for exploring
data day-to-day (notebooks, one-off scripts, the CLI). Separate from the polished
interactive Plotly report built by geo_map.py/generate_report.py -- these are meant
to be a five-second `acs_figures.bar_chart(...)` call, not a report page.

Colors reuse the project's validated palette (see geo_map.py's module docstring):
a single blue sequential ramp for single-metric figures, and the 7-color focus-
county categorical order for multi-county trend lines.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.collections import PatchCollection
from matplotlib.patches import Polygon

import acs_db
from acs_tables import FOCUS_COUNTY_FIPS

BLUE = "#2a78d6"
FOCUS_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"]


def _metric_label(metric_key: str) -> str:
    catalog = acs_db.list_metrics()
    row = catalog[catalog.metric_key == metric_key]
    return row.iloc[0]["label"] if not row.empty else metric_key


def bar_chart(metric_key: str, year: int, top_n: int = 15, ascending: bool = False, save: str | Path | None = None):
    """Horizontal bar chart of the top/bottom N counties for one metric/year, with MOE error bars."""
    df = acs_db.query_metric(metric_key, year=year)
    if df.empty:
        raise ValueError(f"No data for metric={metric_key!r} year={year}. Run acs_db.build_database() first.")
    df = df.sort_values("value", ascending=ascending).head(top_n)

    fig, ax = plt.subplots(figsize=(9, max(4, 0.35 * len(df))))
    ax.barh(df["county_name"], df["value"], xerr=df["moe"], color=BLUE, ecolor="#555555", capsize=3)
    ax.invert_yaxis()
    ax.set_xlabel(_metric_label(metric_key))
    ax.set_title(f"{_metric_label(metric_key)} by county, {year}")
    fig.tight_layout()
    if save:
        fig.savefig(save, dpi=150)
    return fig


def trend_chart(metric_key: str, counties: list[str] | None = None, save: str | Path | None = None):
    """Line chart of one metric over all available years, one line per county.
    Defaults to the project's 7 focus counties."""
    counties = counties or list(FOCUS_COUNTY_FIPS)

    fig, ax = plt.subplots(figsize=(9, 6))
    plotted = 0
    for i, county_name in enumerate(counties):
        df = acs_db.trend(metric_key, county_name)
        if df.empty:
            continue
        ax.plot(df["year"], df["value"], marker="o", label=county_name, color=FOCUS_COLORS[i % len(FOCUS_COLORS)])
        plotted += 1
    if plotted == 0:
        raise ValueError(f"No data for metric={metric_key!r} in counties={counties}. Run acs_db.build_database() first.")

    ax.set_ylabel(_metric_label(metric_key))
    ax.set_title(f"{_metric_label(metric_key)} over time")
    ax.legend(fontsize=8)
    ax.grid(axis="y", color="#e1e0d9")
    fig.tight_layout()
    if save:
        fig.savefig(save, dpi=150)
    return fig


def map_figure(metric_key: str, year: int, save: str | Path | None = None):
    """Static NC county choropleth for one metric/year. For the interactive,
    year-slider version used in the full report, see geo_map.build_choropleth."""
    import geo_map  # imported lazily -- pulls in plotly/requests, only needed for maps

    geojson = geo_map.get_nc_counties_geojson()
    df = acs_db.query_metric(metric_key, year=year)
    if df.empty:
        raise ValueError(f"No data for metric={metric_key!r} year={year}. Run acs_db.build_database() first.")
    values = dict(zip(df["fips"], df["value"]))

    vals = [v for v in values.values() if v is not None]
    norm = plt.Normalize(vmin=min(vals), vmax=max(vals)) if vals else plt.Normalize(0, 1)
    cmap = plt.colormaps["Blues"]

    patches, colors = [], []
    for feature in geojson["features"]:
        value = values.get(feature["id"])
        geom = feature["geometry"]
        polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
        for poly in polys:
            patches.append(Polygon(poly[0]))  # outer ring only -- holes not rendered
            colors.append(cmap(norm(value)) if value is not None else "#dddddd")

    fig, ax = plt.subplots(figsize=(8, 6))
    ax.add_collection(PatchCollection(patches, facecolor=colors, edgecolor="white", linewidth=0.3))
    ax.autoscale_view()
    ax.set_aspect(1.3)
    ax.axis("off")
    ax.set_title(f"{_metric_label(metric_key)}, {year}")
    fig.colorbar(plt.cm.ScalarMappable(norm=norm, cmap=cmap), ax=ax, shrink=0.7)
    fig.tight_layout()
    if save:
        fig.savefig(save, dpi=150)
    return fig


if __name__ == "__main__":
    bar_chart("veteran_poverty_rate", 2023, save="figures_preview_bar.png")
    trend_chart("veteran_poverty_rate", save="figures_preview_trend.png")
    print("Wrote figures_preview_bar.png and figures_preview_trend.png")
