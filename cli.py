"""
Command-line interface to the ACS database: build/refresh it, list what's in it,
run ad-hoc queries, and generate quick figures -- without opening a notebook.

    venv\\Scripts\\python cli.py build [--refresh]
    venv\\Scripts\\python cli.py metrics
    venv\\Scripts\\python cli.py counties
    venv\\Scripts\\python cli.py query --metric veteran_pct [--year 2023] [--county Durham] [--csv out.csv]
    venv\\Scripts\\python cli.py trend --metric veteran_pct --county Durham
    venv\\Scripts\\python cli.py sql "SELECT * FROM metrics WHERE reliability = 'unreliable'"
    venv\\Scripts\\python cli.py plot --metric veteran_pct --type bar --year 2023 [--out out.png]
    venv\\Scripts\\python cli.py plot --metric veteran_pct --type trend [--counties Durham,Nash] [--out out.png]
    venv\\Scripts\\python cli.py plot --metric veteran_pct --type map --year 2023 [--out out.png]
"""

from __future__ import annotations

import argparse

import acs_db
import acs_figures


def cmd_build(args: argparse.Namespace) -> None:
    store = acs_db.build_database(force_refresh=args.refresh)
    print(f"Database built at {acs_db.DB_PATH}")
    print(f"{len(store.by_metric)} metrics, {len(store.county_names)} counties")


def cmd_metrics(args: argparse.Namespace) -> None:
    print(acs_db.list_metrics().to_string(index=False))


def cmd_counties(args: argparse.Namespace) -> None:
    print(acs_db.list_counties().to_string(index=False))


def cmd_query(args: argparse.Namespace) -> None:
    df = acs_db.query_metric(args.metric, year=args.year, county=args.county)
    if args.csv:
        df.to_csv(args.csv, index=False)
        print(f"Wrote {len(df)} rows to {args.csv}")
    else:
        print(df.to_string(index=False))


def cmd_trend(args: argparse.Namespace) -> None:
    df = acs_db.trend(args.metric, args.county)
    print(df[["year", "county_name", "value", "moe", "reliability"]].to_string(index=False))


def cmd_sql(args: argparse.Namespace) -> None:
    print(acs_db.sql(args.query).to_string(index=False))


def cmd_plot(args: argparse.Namespace) -> None:
    counties = args.counties.split(",") if args.counties else None

    if args.type in ("bar", "map") and args.year is None:
        raise SystemExit(f"--year is required for --type {args.type}")

    if args.type == "bar":
        acs_figures.bar_chart(args.metric, args.year, save=args.out)
    elif args.type == "trend":
        acs_figures.trend_chart(args.metric, counties=counties, save=args.out)
    elif args.type == "map":
        acs_figures.map_figure(args.metric, args.year, save=args.out)

    if args.out:
        print(f"Saved {args.out}")
    else:
        import matplotlib.pyplot as plt

        plt.show()


def main() -> None:
    parser = argparse.ArgumentParser(description="Query and visualize the NC veteran ACS database.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("build", help="Fetch from the Census API/cache and (re)build the SQLite database.").add_argument(
        "--refresh", action="store_true", help="Force a live re-fetch instead of using the disk cache."
    )
    sub.add_parser("metrics", help="List available metrics.")
    sub.add_parser("counties", help="List counties in the database.")

    p_query = sub.add_parser("query", help="Query one metric as a table.")
    p_query.add_argument("--metric", required=True)
    p_query.add_argument("--year", type=int)
    p_query.add_argument("--county", help="Substring match on county name, e.g. Durham")
    p_query.add_argument("--csv", help="Write result to this CSV path instead of printing.")

    p_trend = sub.add_parser("trend", help="Print one metric's year-by-year values for one county.")
    p_trend.add_argument("--metric", required=True)
    p_trend.add_argument("--county", required=True)

    p_sql = sub.add_parser("sql", help="Run a raw SQL query against the database.")
    p_sql.add_argument("query")

    p_plot = sub.add_parser("plot", help="Generate a quick figure (bar, trend, or map).")
    p_plot.add_argument("--metric", required=True)
    p_plot.add_argument("--type", choices=["bar", "trend", "map"], required=True)
    p_plot.add_argument("--year", type=int, help="Required for --type bar/map.")
    p_plot.add_argument(
        "--counties", help="Comma-separated county names (trend only); defaults to the 7 focus counties."
    )
    p_plot.add_argument("--out", help="Save to this file instead of opening a window.")

    args = parser.parse_args()
    handlers = {
        "build": cmd_build,
        "metrics": cmd_metrics,
        "counties": cmd_counties,
        "query": cmd_query,
        "trend": cmd_trend,
        "sql": cmd_sql,
        "plot": cmd_plot,
    }
    handlers[args.command](args)


if __name__ == "__main__":
    main()
