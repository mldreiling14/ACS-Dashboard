"""
Streamlit GUI over the ACS veteran database (data/acs.db) -- dropdowns for
metric/year/county in place of the CLI's --metric/--year/--county flags.
Reuses acs_db.py for every query and acs_figures.py for the static map figure;
adds no new data logic of its own.

    venv\\Scripts\\streamlit run app.py

Separate from generate_report.py's polished, committed index.html -- this is
a live local tool for poking at the data, not something you publish/share.
"""

from __future__ import annotations

import streamlit as st

import acs_db
import acs_figures

st.set_page_config(page_title="NC Veteran ACS Explorer", layout="wide")
st.title("NC Veteran ACS Explorer")
st.caption("Local database of veteran-related ACS 5-year estimates, all 100 NC counties, 2015-2024.")

if not acs_db.DB_PATH.exists():
    st.warning("No database found yet -- it needs to be built once before you can query it.")
    if st.button("Build database now"):
        with st.spinner("Fetching from Census API/cache..."):
            acs_db.build_database()
        st.rerun()
    st.stop()

metrics_df = acs_db.list_metrics()
metric_options = dict(zip(metrics_df["label"], metrics_df["metric_key"]))
counties_df = acs_db.list_counties()
county_names = counties_df["county_name"].tolist()

with st.sidebar:
    st.header("Filters")
    metric_label = st.selectbox("Metric", list(metric_options))
    metric_key = metric_options[metric_label]
    view = st.radio(
        "View",
        ["Table (one year, all counties)", "Bar chart (one year)", "Map (one year)", "Trend (one county, all years)"],
    )

    years = acs_db.sql("SELECT DISTINCT year FROM metrics WHERE metric_key = ? ORDER BY year", (metric_key,))[
        "year"
    ].tolist()

    year = None
    county = None
    if view != "Trend (one county, all years)":
        year = st.select_slider("Year", options=years, value=years[-1])
    else:
        default_idx = county_names.index("Durham County") if "Durham County" in county_names else 0
        county = st.selectbox("County", county_names, index=default_idx)

    st.divider()
    if st.button("Rebuild database (refresh from Census API)"):
        with st.spinner("Fetching..."):
            acs_db.build_database(force_refresh=True)
        st.rerun()

table_id = metrics_df.loc[metrics_df.metric_key == metric_key, "table_id"].iloc[0]
st.caption(f"Source table: ACS 5-year estimates, table {table_id}")

if view == "Table (one year, all counties)":
    df = acs_db.query_metric(metric_key, year=year)
    st.subheader(f"{metric_label} — {year}")
    display = df[["county_name", "value", "moe", "reliability"]].sort_values("value", ascending=False)
    st.dataframe(display, width="stretch", hide_index=True)
    st.download_button("Download CSV", df.to_csv(index=False), file_name=f"{metric_key}_{year}.csv")

elif view == "Bar chart (one year)":
    st.subheader(f"{metric_label} — {year}")
    top_n = st.slider("Counties shown", min_value=5, max_value=100, value=15)
    fig = acs_figures.bar_chart(metric_key, year, top_n=top_n)
    st.pyplot(fig)

elif view == "Map (one year)":
    st.subheader(f"{metric_label} — {year}")
    fig = acs_figures.map_figure(metric_key, year)
    st.pyplot(fig)

elif view == "Trend (one county, all years)":
    df = acs_db.trend(metric_key, county)
    st.subheader(f"{metric_label} — {county}")
    st.line_chart(df.set_index("year")["value"])
    st.dataframe(df[["year", "value", "moe", "reliability"]], width="stretch", hide_index=True)
    st.download_button("Download CSV", df.to_csv(index=False), file_name=f"{metric_key}_{county}.csv")
