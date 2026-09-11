"""
Builds the Plotly figures used across both reports: an NC county choropleth
(one metric visible at a time, switched via dropdown), a multi-year trend
line chart, and a ranked horizontal bar chart (same dropdown pattern). The
trend/bar builders come in two flavors: a fixed-county-subset version
(build_trend_chart, used by the Vital Conditions report's 7 focus counties)
and a statewide version (build_statewide_trend_chart/build_bar_chart, used
by the veteran report: each metric's own top counties, not a fixed subset).

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

# Sequential blue ramp, 100->700, from references/palette.md. Fallback for any metric with no
# vital-conditions category (e.g. the veteran-metrics report's map, which has none).
BLUE_SEQUENTIAL = [
    (0 / 12, "#cde2fb"), (1 / 12, "#b7d3f6"), (2 / 12, "#9ec5f4"), (3 / 12, "#86b6ef"),
    (4 / 12, "#6da7ec"), (5 / 12, "#5598e7"), (6 / 12, "#3987e5"), (7 / 12, "#2a78d6"),
    (8 / 12, "#256abf"), (9 / 12, "#1c5cab"), (10 / 12, "#184f95"), (11 / 12, "#104281"),
    (12 / 12, "#0d366b"),
]  # fmt: skip

# Per-category sequential ramps for the Vital Conditions choropleth (HVC "Color Templates Based
# on Aim 3 Hex Codes"), keyed by VitalCondition.key -- one hue family per framework category
# instead of blue for everything. Only one metric/ramp is ever visible on the map at once (the
# dropdown swaps traces), so this still respects the single-simultaneous-hue dataviz rule; it
# just lets *which* hue family varies by category. Metrics with no category (condition="") fall
# back to BLUE_SEQUENTIAL.
CONDITION_RAMPS: dict[str, list[tuple[float, str]]] = {
    "basic_needs":       [(0/4, "#bde1f9"), (1/4, "#a0d1f3"), (2/4, "#56b4e9"), (3/4, "#459fd3"), (4/4, "#338abd")],
    "humane_housing":    [(0/4, "#f9f4bc"), (1/4, "#f0e442"), (2/4, "#d9c733"), (3/4, "#b8a022"), (4/4, "#967811")],
    "work_wealth":       [(0/4, "#f2c1b4"), (1/4, "#e7a28c"), (2/4, "#d55e00"), (3/4, "#bd4a0a"), (4/4, "#a6350f")],
    "lifelong_learning": [(0/4, "#fdebd5"), (1/4, "#f2c56b"), (2/4, "#e69f00"), (3/4, "#c47901"), (4/4, "#b86a00")],
    "transportation":    [(0/4, "#badbdb"), (1/4, "#67b1b3"), (2/4, "#007e82"), (3/4, "#005a5e"), (4/4, "#003f43")],
    "belonging":         [(0/4, "#ffe9f8"), (1/4, "#e6b1d0"), (2/4, "#cc79a7"), (3/4, "#a75684"), (4/4, "#813361")],
}  # fmt: skip

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


def _county_boundary_lines(geojson: dict) -> tuple[list, list]:
    """Every county polygon's ring coordinates, flattened into one lon/lat pair of lists with
    `None` breaks between rings -- the standard way to draw a whole GeoJSON layer's borders as a
    single Scattergeo line trace. Choropleth's own `marker.line` can only do solid borders (no
    `dash` attribute), so this overlay trace is what actually draws the dashed county lines."""
    lons: list = []
    lats: list = []
    for feature in geojson["features"]:
        geometry = feature["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        for polygon in polygons:
            for ring in polygon:
                for lon, lat in ring:
                    lons.append(lon)
                    lats.append(lat)
                lons.append(None)
                lats.append(None)
    return lons, lats


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

    for i, metric in enumerate(metrics):
        z, text, widths, colors = _aligned(metric, initial_year)
        ramp = CONDITION_RAMPS.get(metric.condition, BLUE_SEQUENTIAL)
        colorscale = [[t, hex_] for t, hex_ in ramp]
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

    # County-border overlay, drawn once on top of every choropleth trace and always visible --
    # borders don't change per metric/year, so this one trace never needs updating. Added after
    # the metric traces (a fixed extra index beyond them), so it's important that nothing below
    # restyles/animates "all traces" without naming indices, or this would get swept up in a
    # metric switch or year-frame update.
    boundary_lons, boundary_lats = _county_boundary_lines(geojson)
    fig.add_trace(
        go.Scattergeo(
            lon=boundary_lons,
            lat=boundary_lats,
            mode="lines",
            line=dict(width=0.9, color="#9c9a93"),
            hoverinfo="skip",
            showlegend=False,
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

    # No in-figure title: the dropdown button itself already names the current metric, and a
    # second title layered above it fought the dropdown for the same slice of top margin and
    # ended up overlapping/obscured by it. One label instead of two overlapping ones.
    # Trace indices are explicit here (not left to Plotly's "all traces" default) so this
    # restyle only ever touches the metric choropleths -- the boundary overlay trace added
    # above stays visible and untouched regardless of which metric is selected.
    metric_buttons = [
        dict(
            label=metric.label,
            method="update",
            args=[{"visible": [j == i for j in range(len(metrics))]}, list(range(len(metrics)))],
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
        updatemenus=[dict(buttons=metric_buttons, direction="down", x=0.02, y=1.18, xanchor="left")],
        sliders=[year_slider],
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        margin=dict(l=20, r=20, t=70, b=20),
        height=560,
        autosize=True,
        dragmode=False,  # map stays fixed -- no click-drag panning (paired with STATIC_MAP_CONFIG's scrollZoom=False)
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
    # No in-figure title -- see the matching note in build_choropleth; the dropdown already
    # names the current metric.
    buttons = [
        dict(
            label=metric.label,
            method="update",
            args=[
                {"visible": [i == m for m in range(len(metrics)) for _ in range(n_counties)]},
                {"yaxis.ticksuffix": "%" if metric.value_format == "percent" else ""},
            ],
        )
        for i, metric in enumerate(metrics)
    ]

    fig.update_layout(
        updatemenus=[dict(buttons=buttons, direction="down", x=0.02, y=1.18, xanchor="left")],
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        margin=dict(l=60, r=40, t=70, b=210),
        height=480,
        # 7 series wrap to 2 legend rows -- pushed well below the rangeslider (not just below
        # the x-axis) so the wrapped second row doesn't land on top of the slider's mini-chart.
        legend=dict(orientation="h", y=-0.62),
        xaxis=dict(
            dtick=1,
            gridcolor="#e1e0d9",
            rangeslider=dict(visible=True, thickness=0.06, bgcolor=SURFACE, bordercolor="#e1e0d9", borderwidth=1),
        ),
        yaxis=dict(gridcolor="#e1e0d9", ticksuffix="%" if metrics[0].value_format == "percent" else ""),
        font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif", color="#0b0b0b"),
    )
    return fig


NC_AVERAGE_COLOR = "#9c9a93"  # same light-gray family as the map's county borders


def build_statewide_trend_chart(
    metrics: list[MapMetricSpec],
    all_series_by_metric_fips: dict[str, dict[str, list[CountyMetric]]],  # metric_key -> fips -> [CountyMetric/year]
    county_order: list[tuple[str, str]],  # [(county_name, fips), ...], every county, alphabetical
    nc_average_by_metric: dict[str, list[tuple[int, float]]],  # metric_key -> [(year, avg_value), ...]
    default_metric_key: str,
    default_fips: list[str],  # counties visible on first paint, before any user interaction
) -> tuple[go.Figure, list[dict]]:
    """Multi-year trend with every county's own line available (metric x county), driven entirely
    by the page's own JS rather than a baked-in Plotly dropdown: there's no fixed "top N" or fixed
    subset here, so a Python-side updatemenu can't pre-compute the visibility states the way
    build_trend_chart's fixed-7-focus-county version does. No rangeslider either -- it read as a
    second, confusing zoom control sitting right under an already-interactive chart.

    Returns (figure, trace_meta): trace_meta is a JSON-able list, one entry per trace in the same
    order as fig.data, e.g. {"metric": "veteran_poverty_rate", "fips": "37001", "is_average":
    False} -- the page's JS uses it to compute which traces a given (metric, county-selection)
    combination should show via Plotly.restyle, and to recolor the currently-selected counties by
    selection order (see report_template.html)."""
    fig = go.Figure()
    trace_meta: list[dict] = []

    for metric in metrics:
        fips_series = all_series_by_metric_fips.get(metric.key, {})
        is_default_metric = metric.key == default_metric_key
        for county_name, fips in county_order:
            points = fips_series.get(fips, [])
            selected = is_default_metric and fips in default_fips
            fig.add_trace(
                go.Scatter(
                    x=[p.year for p in points],
                    y=[p.value for p in points],
                    mode="lines+markers",
                    name=county_name,
                    line=dict(color=FOCUS_COUNTY_COLORS[0], width=2),  # JS recolors by selection order
                    marker=dict(size=6),
                    visible=selected,
                    hovertemplate=f"<b>{county_name}</b><br>%{{x}}: {{y}}<extra></extra>".replace(
                        "{y}", "%{y:.1f}" if metric.value_format == "percent" else "%{y:$,.0f}"
                    ),
                )
            )
            trace_meta.append({"metric": metric.key, "fips": fips, "is_average": False})

        avg_series = nc_average_by_metric.get(metric.key, [])
        fig.add_trace(
            go.Scatter(
                x=[year for year, _ in avg_series],
                y=[value for _, value in avg_series],
                mode="lines",
                name="NC county average",
                line=dict(color=NC_AVERAGE_COLOR, width=2, dash="dash"),
                visible=is_default_metric,
                hovertemplate="<b>NC county average</b><br>%{x}: %{y:.1f}<extra></extra>"
                if metric.value_format == "percent"
                else "<b>NC county average</b><br>%{x}: %{y:$,.0f}<extra></extra>",
            )
        )
        trace_meta.append({"metric": metric.key, "fips": None, "is_average": True})

    default_format = next((m.value_format for m in metrics if m.key == default_metric_key), "percent")
    fig.update_layout(
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        margin=dict(l=60, r=40, t=20, b=90),
        height=460,
        legend=dict(orientation="h", y=-0.22),
        xaxis=dict(dtick=1, gridcolor="#e1e0d9"),
        yaxis=dict(gridcolor="#e1e0d9", ticksuffix="%" if default_format == "percent" else ""),
        font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif", color="#0b0b0b"),
    )
    return fig, trace_meta


def build_bar_chart(
    metrics: list[MapMetricSpec],
    data_by_metric: dict[str, list[CountyMetric]],  # metric_key -> [CountyMetric] for one snapshot year
    top_n: int = 20,
) -> go.Figure:
    """Horizontal ranked-bar view, one dropdown-switched view per metric: the top `top_n`
    counties by that metric's value, for the map's snapshot year."""
    fig = go.Figure()

    for i, metric in enumerate(metrics):
        rows = sorted(data_by_metric.get(metric.key, []), key=lambda r: r.value, reverse=True)[:top_n]
        rows.reverse()  # horizontal bars read top-to-bottom, so reverse puts the highest value on top
        fig.add_trace(
            go.Bar(
                x=[r.value for r in rows],
                y=[r.county_name for r in rows],
                orientation="h",
                marker_color="#2a78d6",
                text=[_format_value(r.value, metric.value_format) for r in rows],
                textposition="outside",
                hovertext=[
                    f"<b>{r.county_name}</b><br>{_format_value(r.value, metric.value_format)} "
                    f"± {_format_value(r.moe, metric.value_format)} ({r.reliability})"
                    for r in rows
                ],
                hoverinfo="text",
                visible=(i == 0),
            )
        )

    buttons = [
        dict(
            label=metric.label,
            method="update",
            args=[
                {"visible": [j == i for j in range(len(metrics))]},
                {"xaxis.ticksuffix": "%" if metric.value_format == "percent" else ""},
            ],
        )
        for i, metric in enumerate(metrics)
    ]

    fig.update_layout(
        updatemenus=[dict(buttons=buttons, direction="down", x=0.02, y=1.1, xanchor="left")],
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        margin=dict(l=140, r=60, t=60, b=40),
        height=max(420, 26 * top_n + 100),
        xaxis=dict(gridcolor="#e1e0d9", ticksuffix="%" if metrics[0].value_format == "percent" else ""),
        yaxis=dict(automargin=True),
        font=dict(family="system-ui, -apple-system, 'Segoe UI', sans-serif", color="#0b0b0b"),
    )
    return fig


# Pairs with build_choropleth's dragmode=False: scrollZoom/doubleClick are separate interaction
# paths Plotly's geo subplots handle outside of dragmode, so both need disabling to make the
# map fully fixed. displayModeBar=False also drops the (otherwise still-visible) zoom/pan
# toolbar so there's no UI left suggesting the map can be moved.
STATIC_MAP_CONFIG = {"scrollZoom": False, "displayModeBar": False, "doubleClick": False}


def render_html(fig: go.Figure, include_plotlyjs: str = "cdn", config: dict | None = None) -> str:
    return fig.to_html(full_html=False, include_plotlyjs=include_plotlyjs, config=config)


if __name__ == "__main__":
    geojson = get_nc_counties_geojson()
    print(f"NC counties in geojson: {len(geojson['features'])}")
    print("Sample id:", geojson["features"][0]["id"], geojson["features"][0]["properties"]["NAME"])
