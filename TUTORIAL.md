# Tutorial: NC Veteran Vital Conditions dashboard

This walks through everything in this folder: scraping the ACS "Data" landing
page for context/links, pulling real veteran-related numbers from the Census
Data API for all 100 NC counties across 2015-2024, and rendering all of it —
a county choropleth map, a multi-year trend chart, and per-focus-county detail
cards — into a single static `index.html`.

```
census_dashboard/
├── scrape_acs_resources.py   # scrapes census.gov/programs-surveys/acs/data.html
├── acs_tables.py              # table registry: which ACS variables per table/year, MOE math
├── acs_fetch.py                 # fetches + caches each (table, year), with a real-data fallback chain
├── acs_metrics.py                 # shapes fetch results into map/trend/card view models
├── geo_map.py                       # NC county GeoJSON + the two Plotly figures (map, trend)
├── generate_report.py                 # orchestrates everything above into index.html
├── templates/
│   └── report_template.html            # Jinja2 template for the report
├── index.html                            # generated output, committed at repo root for GitHub Pages
├── cache/                                  # gitignored -- disk cache of every live API pull
├── seed_data/                                # committed -- one real snapshot per table, for a zero-setup fallback
├── requirements.txt
└── .env.example                                # where your free Census API key goes
```

`index.html` lives at the repo root (not in a build-output folder) so GitHub Pages can serve
it directly with Pages set to deploy from `/ (root)`. Regenerate and commit it whenever the
underlying data changes -- it isn't rebuilt automatically on push.

## 1. Why this isn't a single scrape

The URL `census.gov/programs-surveys/acs/data.html` is a **navigation hub, not
a data page** — no ACS numbers live in its HTML. It has three sections ("Get
Started Accessing ACS Data", "View Popular ACS Tables", "Discover Popular ACS
Data Resources") that link out to `data.census.gov` and the Bureau's own
access points (the API, FTP, summary files).

So the pipeline does two different things and combines them:

1. **Scrape the landing page** (`scrape_acs_resources.py`) for the "Popular
   ACS Tables" and resource links — legitimate, useful, exactly what a human
   visiting the page would read.
2. **Pull the actual numbers from the Census Data API** (everything else in
   this folder) — the Bureau's own sanctioned, structured, ToS-compliant way
   to get real estimates.

## 2. Set up the environment

```powershell
python -m venv venv
venv\Scripts\pip install -r requirements.txt
copy .env.example .env
# edit .env and paste your key: CENSUS_API_KEY=your_key_here
```

Get a free key (instant signup): <https://api.census.gov/data/key_signup.html>.
County-level queries no longer work without one — the API returns a "Missing
Key" error page, not a rate-limit warning.

## 3. The scraper — `scrape_acs_resources.py`

Unchanged from the original build. Two CSS selectors do the work; see the
module docstring for the identify-yourself / retry-with-backoff details.

## 4. The data layer — `acs_tables.py` + `acs_fetch.py` + `acs_metrics.py`

Six ACS tables feed six report metrics, all queried for **all 100 NC
counties in one API call per (table, year)** via `for=county:*&in=state:37`:

| Metric | Table | Notes |
|---|---|---|
| Veteran % (18+) | `DP02` | variable code shifts from `_0069` to `_0070` at 2019 |
| Poverty rate among veterans | `C21007` | age x veteran x poverty x disability cross-tab |
| % veterans with service-connected disability rating | `B21100` | table is already veteran-only |
| % population with VA health care coverage | `C27009` | denominator is total population, not just veterans |
| Veteran unemployment rate | `B21005` | ages 18-64 only (three age bands summed) |
| Median income, veterans | `B21004` | direct estimate, no rate calc |

**`acs_tables.py`** declares this as data: `YEARS` (2015-2024), a `TableSpec`
per table (which variables to request for a given year, and a `derive()`
function turning raw cells into `{metric_key: (value, moe)}`), and
`MAP_METRICS` (the six metrics, in map/dropdown order). Every `derive()` also
carries the Census Bureau's standard MOE formulas (`coefficient_of_variation`,
`reliability_flag`, `moe_ratio`) so every number in the report ships with a
reliability read, not just a point estimate — important once you're looking
at deep cross-tabs for small counties, where the margin of error can rival
the estimate itself.

**`acs_fetch.py`** fetches one `(table, year)` pair with a fallback chain that
never fabricates a number:

```
disk cache -> live Census API -> same-year cache (if force_refresh skipped it)
-> most-recent cached year for that table -> bundled seed_data/ -> unavailable
```

`get_all()` loops every table x year. Run it standalone to see the source of
every pull:

```powershell
venv\Scripts\python acs_fetch.py
```

**`acs_metrics.py`** turns those raw rows into `CountyMetric` records and the
view-shaping functions `generate_report.py` calls: `county_metrics_for` (one
metric, one year, all counties — feeds the map), `trend_series` (one metric,
one county, all years — feeds the trend chart), `focus_county_card` (all
metrics, one county, one year — feeds the detail cards).

## 5. The geospatial layer — `geo_map.py`

NC county boundaries come from Plotly's public county GeoJSON
(`plotly/datasets/geojson-counties-fips.json`, keyed by 5-digit FIPS),
fetched once and cached at `cache/geo/nc_counties.geojson` filtered down to
NC's 100 features.

Both Plotly figures — the choropleth and the trend line chart — are built
here and embedded into the report via `fig.to_html(full_html=False,
include_plotlyjs="cdn")`. A dropdown (`updatemenus`) switches which of the
six metrics is visible, without a backend. Colors follow the project's
[dataviz skill](../.claude if you have it, or ask your assistant) validated
default palette: a single blue sequential ramp for the map (only one metric
is ever visible at once, so no second hue is needed), and the first seven
categorical slots — in their fixed, colorblind-validated order — for the
seven focus-county trend lines. Because a static Plotly embed can't react to
the page's light/dark mode, both figures pin an explicit light background
rather than trying to fake theme-reactivity.

## 6. The report — `generate_report.py` + `templates/report_template.html`

`generate_report.py` calls the scraper, builds the `MetricsStore` via
`acs_metrics.build_store()`, resolves one `map_year` (the latest year where
*every* metric has data, so no dropdown option is ever empty), builds both
Plotly figures, and renders the template with: the map, the trend chart, one
card per focus county (all six metrics, each with its MOE and a
reliability badge), the original veteran-count bar chart (now sourced from
the new data layer), the scraped resource sections, and a data-provenance
table showing exactly where each metric's numbers came from.

```powershell
venv\Scripts\python generate_report.py
start index.html
```

## 7. Adding another metric or another year

- **Another table/metric**: add a `TableSpec` to `acs_tables.TABLES` (look up
  the exact variable codes at
  `https://api.census.gov/data/{year}/acs/acs5/groups/{TABLE}.json` —
  **check at least two years apart**, since variable codes can shift between
  vintages the way DP02's veteran code did at 2019), write its `derive()`,
  and add a `MapMetricSpec` to `acs_tables.MAP_METRICS`. Everything downstream
  (fetch, cache, map dropdown, trend dropdown, focus cards, data-status table)
  picks it up automatically.
- **More years**: extend `acs_tables.YEARS`. ACS 5-year data profiles go back
  to the 2005-2009 vintage; detail tables (B/C-prefixed) are generally
  available just as far back, but codes aren't guaranteed stable — spot-check
  before trusting a big year range.
- **Different geography**: `acs_fetch._fetch_live` hardcodes
  `for=county:*&in=state:{NC_STATE_FIPS}`. Swapping states means changing
  `NC_STATE_FIPS` in `acs_tables.py` and re-running `geo_map.get_nc_counties_geojson`
  with a new state filter (currently hardcoded to `"37"` too).

## 8. Troubleshooting

**`ConnectionResetError [WinError 10054]` or SSL handshake failures.** Almost
always local network interference (AV doing HTTPS inspection, a corporate
proxy, a VPN), not a problem with the script or census.gov.

**Report shows a "seed" or "stale_cache" source in the data-status table.**
That's the fallback chain working as designed — check `.env` has a real
`CENSUS_API_KEY`, and that `cache/acs/` has recent files for the table in
question. Force a fresh pull for everything with:

```powershell
venv\Scripts\python -c "from acs_metrics import build_store; build_store(force_refresh=True)"
```

**The scraper returns an empty list.** Census.gov changed its markup —
re-download the page, open devtools, update the CSS selectors in
`scrape_acs_resources.get_popular_tables()` / `get_resource_links()`.
