"""
Scrapes the ACS "Data" landing page for a curated list of resource links.

The page at census.gov/programs-surveys/acs/data.html is a navigation hub, not
a data table -- it does not contain any Census numbers itself. What it *does*
contain is useful: a short list of the most-requested Data Profile tables
(DP02-DP05) and links to the official access points (data.census.gov, the API,
FTP, summary files, etc). This module scrapes that list so the report can link
out to it and so the report can mention which table codes are "popular".
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ACS_DATA_PAGE = "https://www.census.gov/programs-surveys/acs/data.html"

# Census.gov blocks the default python-requests user agent on some paths.
# Identify the bot honestly and give them a way to reach us, per robots.txt etiquette.
HEADERS = {
    "User-Agent": "healthy-vets-census-dashboard/1.0 (contact: mdreiling14@gmail.com)"
}


@dataclass
class PopularTable:
    code: str  # e.g. "DP02"
    title: str
    description: str
    url: str


@dataclass
class ResourceLink:
    title: str
    url: str


def fetch_page(url: str = ACS_DATA_PAGE, retries: int = 3) -> BeautifulSoup:
    """GETs a page, retrying on transient network errors (resets, timeouts).

    Real-world scraping hits flaky connections often enough that a bare
    requests.get() is worth wrapping -- a couple of retries with backoff
    turns an occasional dropped handshake into a non-issue.
    """
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, headers=HEADERS, timeout=15)
            response.raise_for_status()
            return BeautifulSoup(response.text, "html.parser")
        except requests.exceptions.RequestException as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(1.5 * attempt)
    raise last_error  # type: ignore[misc]


def get_popular_tables(soup: BeautifulSoup) -> list[PopularTable]:
    """Scrapes the 'Popular ACS Tables' cards (DP02-DP05 data profile links)."""
    tables = []
    for card in soup.select("a.uscb-list__card"):
        href = card.get("href", "")
        if "q=" not in href:
            continue
        code = href.split("q=")[-1].upper()
        title_el = card.select_one(".uscb-card__title")
        desc_el = card.select_one(".uscb-card__description")
        title = title_el.get_text(strip=True) if title_el else code
        description = desc_el.get_text(strip=True) if desc_el else ""
        tables.append(PopularTable(code=code, title=title, description=description, url=href))
    return tables


def get_resource_links(soup: BeautifulSoup) -> list[ResourceLink]:
    """Scrapes the 'Discover Popular ACS Data Resources' list."""
    links = []
    seen = set()
    for a in soup.select("a.uscb-list-text__item-container"):
        href = urljoin(ACS_DATA_PAGE, a.get("href", ""))
        title = a.get("title") or a.get_text(strip=True)
        if href not in seen and title:
            seen.add(href)
            links.append(ResourceLink(title=title, url=href))
    return links


def scrape() -> dict:
    soup = fetch_page()
    return {
        "popular_tables": get_popular_tables(soup),
        "resource_links": get_resource_links(soup),
    }


if __name__ == "__main__":
    result = scrape()
    print(f"Popular tables ({len(result['popular_tables'])}):")
    for t in result["popular_tables"]:
        print(f"  {t.code}: {t.title} -> {t.url}")
    print(f"\nResource links ({len(result['resource_links'])}):")
    for link in result["resource_links"]:
        print(f"  {link.title} -> {link.url}")
