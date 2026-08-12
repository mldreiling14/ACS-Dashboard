"""
Builds the two Plotly figures in the report: an NC county choropleth (one
metric visible at a time, switched via dropdown) and a multi-year trend line
chart for the 7 focus counties (same dropdown pattern).

Colors follow the dataviz skill's validated default palette (references/palette.md):
- Choropleth uses the documented single-hue BLUE sequential ramp for every metric,
  since only one metric is visible on screen at a time (the skill's rule for a
  second simultaneous hue doesn't apply here).
- Trend lines use the first 7 categorical slots in their fixed, CVD-validated order
  (validated via scripts/validate_palette.js -- all hard gates pass in both modes).

Plotly's static HTML embed can't react to prefers-color-scheme at render time, so
both figures pin an explicit light surface/background rather than trying to fake
theme-reactivity -- they render as a small fixed-chrome "widget" regardless of the
surrounding page's light/dark mode, same as an embedded map on any dashboard.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import plotly.graph_objects as go
import requests

from acs_metrics import CountyMetric
from acs_tables import MapMetricSpec

GEOJSON_URL = "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json"
BASE_DIR = Path(__file__).parent
GEOJSON_CACHE_PATH = BASE_DIR / "cache" / "geo" / "nc_counties.geojson"
NC_STATE_FIPS = "37"

SURFACE = "#fcfcfb"  # matches --surface-1 in the report's light palette (references/palette.md)

# Sequential blue ramp, 100->700, from references/palette.md.
BLUE_SEQUENTIAL = [
    (0 / 12, "#cde2fb"), (1 / 12, "#b7d3f6"), (2 / 12, "#9ec5f4"), (3 / 12, "#86b6ef"),
    (4 / 12, "#6da7ec"), (5 / 12, "#5598e7"), (6 / 12, "#3987e5"), (7 / 12, "#2a78d6"),
    (8 / 12, "#256abf"), (9 / 12, "#1c5cab"), (10 / 12, "#184f95"), (11 / 12, "#104281"),
    (12 / 12, "#0d366b"),
]  # fmt: skip

# First 7 categorical slots, fixed order, light mode -- validated all-hard-gates-pass
# for a 7-series line chart (adjacent pairlist; "lines" per the skill's classification).
FOCUS_COUNTY_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7"]

FOCUS_BORDER_WIDTH = 2.2
DEFAULT_BORDER_WIDTH = 0.4
FOCUS_BORDER_COLOR = "#0b0b0b"
DEFAULT_BORDER_COLOR = "#c3c2b7"


def get_nc_counties_geojson(force_refresh: bool = False) -> dict:
    if not force_refresh and GEOJSON_CACHE_PATH.exists():
        return json.loads(GEOJSON_CACHE_PATH.read_text(encoding="utf-8"))

    last_error: Exception | None = None
    for attempt in range(1, 4):
        try:
            response = requests.get(GEOJSON_URL, timeout=30)
            response.raise_for_status()
            full = response.json()
            break
        except requests.exceptions.RequestException as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(1.5 * attempt)
    else:
        raise last_error  # type: ignore[misc]

    nc_features = [f for f in full["features"] if f["id"].startswith(NC_STATE_FIPS)]
    nc_geojson = {"type": "FeatureCollection", "features": nc_features}

    GEOJSON_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    GEOJSON_CACHE_PATH.write_text(json.dumps(nc_geojson), encoding="utf-8")
    return nc_geojson


def _geojson_bounds(geojson: dict) -> tuple[float, float, float, float]:
    """min_lon, max_lon, min_lat, max_lat across every ring of every feature."""
    min_lon, max_lon, min_lat, max_lat = 180.0, -180.0, 90.0, -90.0

    def walk(coords):
        nonlocal min_lon, max_lon, min_lat, max_lat
        if isinstance(coords[0], (int, float)):
            lon, lat = coords[0], coords[1]
            min_lon, max_lon = min(min_lon, lon), max(max_lon, lon)
            min_lat, max_lat = min(min_lat, lat), max(max_lat, lat)
        else:
            for c in coords:
                walk(c)

    for feature in geojson["features"]:
        walk(feature["geometry"]["coordinates"])
    return min_lon, max_lon, min_lat, max_lat


def _format_value(value: float, value_format: str) -> str:
    if value_format == "currency":
        return f"${value:,.0f}"
    return f"{value:.1f}%"


def build_choropleth(
    geojson: dict,
    metrics: list[MapMetricSpec],
    data_by_metric_year: dict[str, dict[int, list[CountyMetric]]],  # metric_key -> year -> [CountyMetric]
    focus_fips: set[str],
    years: list[int],
    initial_year: int,
) -> go.Figure:
    # Fixed location order shared by every trace/frame -- lets frames update just z/text/border
    # per year without re-sending locations, and keeps every year's data aligned to the same counties.
    canonical_fips = [f["id"] for f in geojson["features"]]

    def _aligned(metric: MapMetricSpec, year: int) -> tuple[list, list, list, list]:
        by_fips = {r.fips: r for r in data_by_metric_year.get(metric.key, {}).get(year, [])}
        z, text, widths, colors = [], [], [], []
        for fips in canonical_fips:
            row = by_fips.get(fips)
            z.append(row.value if row else None)
            text.append(
                f"<b>{row.county_name}</b><br>{metric.label}: {_format_value(row.value, metric.value_format)}"
                f"<br>MOE: ±{_format_value(row.moe, metric.value_format)} ({row.reliability})"
                if row
                else ""
            )
            widths.append(FOCUS_BORDER_WIDTH if fips in focus_fips else DEFAULT_BORDER_WIDTH)
            colors.append(FOCUS_BORDER_COLOR if fips in focus_fips else DEFAULT_BORDER_COLOR)
        return z, text, widths, colors

    fig = go.Figure()
    colorscale = [[t, hex_] for t, hex_ in BLUE_SEQUENTIAL]

    for i, metric in enumerate(metrics):
        z, text, widths, colors = _aligned(metric, initial_year)
        fig.add_trace(
            go.Choropleth(
                geojson=geojson,
                locations=canonical_fips,
                z=z,
                featureidkey="id",
                colorscale=colorscale,
                marker_line_width=widths,
                marker_line_color=colors,
                text=text,
                hoverinfo="text",
                colorbar=dict(title=metric.value_format, len=0.75),
                visible=(i == 0),
            )
        )

    # One frame per year; each frame updates every metric trace's z/text/border so the year
    # slider works no matter which metric is currently visible.
    frames = []
    for year in years:
        frame_traces = []
        for metric in metrics:
            z, text, widths, colors = _aligned(metric, year)
            frame_traces.append(go.Choropleth(z=z, text=text, marker=dict(line=dict(width=widths, color=colors))))
        frames.append(go.Frame(name=str(year), data=frame_traces))
    fig.frames = frames

    metric_buttons = [
        dict(
            label=metric.label,
            method="update",
            args=[{"visible": [j == i for j in range(len(metrics))]}, {"title.text": metric.label}],
        )
        for i, metric in enumerate(metrics)
    ]

    year_slider = dict(
        active=years.index(initial_year),
        currentvalue={"prefix": "Year: ", "font": {"size": 13}},
        pad={"t": 30},
        x=0.02,
        len=0.94,
        steps=[
            dict(
                method="animate",
                args=[
                    [str(year)],
                    {"mode": "immediate", "frame": {"duration": 0, "redraw": True}, "transition": {"duration": 0}},
                ],
                label=str(year),
            )
            for year in years
        ],
    )

    # scope="usa" locks the projection to Albers USA, which tilts a single state's shape to
    # keep the whole country compact -- mercator is the standard north-up projection instead.
    #
    # fitbounds="locations" is unreliable here: it computes its fit against the figure's
    # *default* initial size, and since this figure has no explicit width (it's meant to
    # stretch to fill the card), the real rendered width is only known in-browser after
    # layout -- so the fit ends up tiny and centered in a lot of dead space. Setting the
    # lon/lat range explicitly (with a little padding) sizes off the geometry itself instead,
    # independent of viewport width, and lets the geo subplot's own autoscale fill the card.
    min_lon, max_lon, min_lat, max_lat = _geojson_bounds(geojson)
    lon_pad = (max_lon - min_lon) * 0.03
    lat_pad = (max_lat - min_lat) * 0.03
    fig.update_geos(
        projection_type="mercator",
        lonaxis_range=[min_lon - lon_pad, max_lon + lon_pad],
        lataxis_range=[min_lat - lat_pad, max_lat + lat_pad],
        visible=False,
        bgcolor=SURFACE,
    )
    fig.update_layout(
        title=dict(text=metrics[0].label, x=0.02, xanchor="left"),
        updatemenus=[dict(buttons=metric_buttons, direction="down", x=0.02, y=1.16, xanchor="left")],
        sliders=[year_slider],
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        margin=dict(l=0, r=0, t=70, b=0),
        height=560,
        autosize=True,
        font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif", color="#0b0b0b"),
    )
    return fig


def build_trend_chart(
    metrics: list[MapMetricSpec],
    series_by_metric: dict[str, dict[str, list[CountyMetric]]],  # metric_key -> fips -> [CountyMetric per year]
    county_order: list[tuple[str, str]],  # [(county_name, fips), ...] in display order
) -> go.Figure:
    fig = go.Figure()

    for i, metric in enumerate(metrics):
        fips_series = series_by_metric.get(metric.key, {})
        for j, (county_name, fips) in enumerate(county_order):
            points = fips_series.get(fips, [])
            fig.add_trace(
                go.Scatter(
                    x=[p.year for p in points],
                    y=[p.value for p in points],
                    mode="lines+markers",
                    name=county_name,
                    line=dict(color=FOCUS_COUNTY_COLORS[j % len(FOCUS_COUNTY_COLORS)], width=2),
                    marker=dict(size=6),
                    visible=(i == 0),
                    hovertemplate=f"<b>{county_name}</b><br>%{{x}}: {{y}}<extra></extra>".replace(
                        "{y}", "%{y:.1f}" if metric.value_format == "percent" else "%{y:$,.0f}"
                    ),
                    legendgroup=county_name,
                )
            )

    n_counties = len(county_order)
    buttons = [
        dict(
            label=metric.label,
            method="update",
            args=[
                {"visible": [i == m for m in range(len(metrics)) for _ in range(n_counties)]},
                {"title.text": metric.label, "yaxis.ticksuffix": "%" if metric.value_format == "percent" else ""},
            ],
        )
        for i, metric in enumerate(metrics)
    ]

    fig.update_layout(
        title=dict(text=metrics[0].label, x=0.02, xanchor="left"),
        updatemenus=[dict(buttons=buttons, direction="down", x=0.02, y=1.15, xanchor="left")],
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        margin=dict(l=50, r=20, t=70, b=60),
        height=480,
        legend=dict(orientation="h", y=-0.25),
        xaxis=dict(
            dtick=1,
            gridcolor="#e1e0d9",
            rangeslider=dict(visible=True, thickness=0.08, bgcolor=SURFACE, bordercolor="#e1e0d9", borderwidth=1),
        ),
        yaxis=dict(gridcolor="#e1e0d9", ticksuffix="%" if metrics[0].value_format == "percent" else ""),
        font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif", color="#0b0b0b"),
    )
    return fig


def render_html(fig: go.Figure, include_plotlyjs: str = "cdn") -> str:
    return fig.to_html(full_html=False, include_plotlyjs=include_plotlyjs)


if __name__ == "__main__":
    geojson = get_nc_counties_geojson()
    print(f"NC counties in geojson: {len(geojson['features'])}")
    print("Sample id:", geojson["features"][0]["id"], geojson["features"][0]["properties"]["NAME"])
