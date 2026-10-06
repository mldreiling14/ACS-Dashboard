# NC Veteran ACS Dashboard

An interactive, county-level view of how Veterans in North Carolina compare with the rest of the population on economic, health, and housing measures, from 2015 to 2024.

**Live dashboard:** https://mldreiling14.github.io/ACS-Dashboard/

## What it shows

- **Statewide map and trends** — choropleth map of all 100 NC counties with a year slider, a trend chart, and a top-20 bar chart for any metric.
- **County reports** — one page per county with a Veteran executive summary: where the county outperforms or trails the NC county average, and how Veterans compare with civilians in the same county.
- **Veteran vs. civilian comparisons** — wherever the Census Bureau publishes a Veteran-status breakdown (age, sex, education, income, poverty, employment, disability rating), the dashboard puts Veterans and non-Veterans side by side.
- **Full data table** — every county, every metric, with margins of error and reliability flags.

Metrics are grouped by how they're measured:

| Scope | Examples |
|---|---|
| Veteran-specific | Poverty rate, unemployment rate, median income, disability rating share, age/sex/education/service-era profile |
| Veteran vs. civilian | Age 65+, male share, bachelor's-or-higher, less-than-high-school |
| Whole-population context | SNAP receipt, uninsured share, VA health care coverage, homeownership, cost burden, median rent, no-vehicle households |

Figures carry a **margin of error** (MOE) and a **reliability flag** (reliable / caution / unreliable) following Census Bureau guidance.

## Data sources

- **American Community Survey (ACS) 5-year estimates**, pulled from the Census Bureau's Data API for every NC county, 2015–2024.
- **Federal Reserve Economic Data (FRED), St. Louis Fed** — used only for the statewide comparison charts (poverty rate from Census SAIPE, unemployment rate from BLS LAUS). Optional.

## Important caveats

- **Five-year rolling averages.** ACS 5-year estimates describe a five-year window, not a single year. Year-to-year changes are smoothed and can lag real events.
- **Not every measure splits by Veteran status.** The Census Bureau publishes Veteran-status breakdowns for only a limited set of tables. Housing, vehicle access, SNAP, and health insurance are reported for the whole population only; these are labeled as such.
- **Veteran-share denominators differ.** "Veteran %" is the share of the *civilian population 18 and over*, while "VA health care coverage" is a share of *everyone*. They are shown together for context, not as a like-for-like comparison.
- **Small counties are noisy.** Where a margin of error is large relative to the estimate, the dashboard says so rather than presenting the number as precise.

## Repository layout

| Path | Purpose |
|---|---|
| `index.html` | Generated statewide dashboard (published to GitHub Pages) |
| `counties/` | Generated county reports (100 files) |
| `templates/` | Jinja2 templates for the generated pages |
| `acs_tables.py` | Registry of every ACS table pulled, with variable codes and derivations |
| `acs_fetch.py` | Census API fetch with disk cache and fallbacks |
| `acs_metrics.py` | Turns raw table values into metrics with margins of error |
| `acs_db.py` | Builds the local SQLite database (`data/acs.db`) |
| `geo_map.py`, `generate_report.py` | Build the Plotly map and the HTML pages |
| `cli.py`, `app.py` | Command-line tool and Streamlit explorer over the database |
| `export_acs_data.py` | Exports the data to CSV and Excel (`data/`) |
| `aim3_data/` | Working folder for the Aim 3 trend figures and tutorial notebook |

## Setup

Requires Python 3.12 or newer.

```powershell
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

Copy `.env.example` to `.env` and add your keys:

```
CENSUS_API_KEY=your_census_key        # free: https://api.census.gov/data/key_signup.html
FRED_API_KEY=your_fred_key            # optional: https://fred.stlouisfed.org/docs/api/api_key.html
```

`.env` is git-ignored; never commit real keys.

## Usage

Build the database (uses cached data where available):

```powershell
venv\Scripts\python acs_db.py
```

Regenerate the dashboard and county reports:

```powershell
venv\Scripts\python generate_report.py
```

Then open `index.html` in a browser, or run a local server from this folder:

```powershell
venv\Scripts\python -m http.server 8000
```

and visit `http://localhost:8000/index.html`.

Explore the data interactively:

```powershell
venv\Scripts\streamlit run app.py
```

Command-line queries:

```powershell
venv\Scripts\python cli.py metrics
venv\Scripts\python cli.py trend --metric veteran_poverty_rate --county Durham
venv\Scripts\python cli.py plot --metric veteran_unemployment_rate --type trend --out unemployment.png
```

Export the full dataset:

```powershell
venv\Scripts\python export_acs_data.py
```

## Status

Work in progress. Figures and reports are regenerated from the cached Census data; the live site reflects the most recent build.
