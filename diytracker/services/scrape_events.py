"""Scrape events from metalgigs.ch and petzi.ch and save to a CSV file.

This script downloads all event URLs from the public `sitemap.xml` files of
metalgigs.ch and petzi.ch.  Each event page is then parsed to extract as
much useful information as possible, such as the event title, date,
location, ticket prices, door times, and any other structured data.  The
combined results from both websites are written to ``instance/events.csv``.

The script requires the ``requests`` and ``beautifulsoup4`` packages.
If these packages are not installed, install them with ``pip install
requests beautifulsoup4``.

Usage::

    python scrape_events.py

The CSV columns are a superset of all fields collected from both sites.
Missing values are left blank.
"""

import csv
import json
import logging
import random
import re
import sys
import time
import warnings
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from diytracker.paths import INSTANCE_DIR, LOGS_DIR
from diytracker.utils import resolve_canton, split_leading_plz

# Messages go nowhere until configure_logging() attaches handlers — the web
# app imports this module only for the parsers and must not open log files
# as an import side effect. Scrape runs (the scraper service and the CLI in
# scripts/) call configure_logging() first.
logger = logging.getLogger("scrape_events")


def configure_logging():
    """Attach file (logs/scrape_events.log) + stderr handlers for a scrape run.

    Idempotent: the logger is a process-wide singleton, so handlers are only
    attached once even across repeated runs.
    """
    if logger.handlers:
        return
    logger.setLevel(logging.DEBUG)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(
        LOGS_DIR / "scrape_events.log", mode="a", encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)


def _dedup_preserving_order(urls) -> list[str]:
    """Drop duplicate URLs while keeping first-seen order."""
    seen: set = set()
    unique: list[str] = []
    for url in urls:
        if url not in seen:
            unique.append(url)
            seen.add(url)
    return unique


# Random delay (in seconds) between requests to reduce the likelihood
# of triggering rate-limiting.  Values are inclusive in random.uniform.
REQUEST_DELAY_RANGE: tuple[float, float] = (0.5, 1.5)

# Identify ourselves honestly so site operators can see who is crawling
# and how to reach us, rather than disguising the scraper as a browser.
USER_AGENT = "diytracker (+https://diytracker.ch; lucaggett@gmail.com)"

# Per-host robots.txt parsers, refreshed after ROBOTS_CACHE_TTL seconds
# (the scheduler process runs for weeks, so entries must expire).
ROBOTS_CACHE_TTL = 24 * 3600
_robots_cache: dict[str, tuple[float, RobotFileParser | None]] = {}


def _get_robots_parser(base_url: str) -> RobotFileParser | None:
    cached = _robots_cache.get(base_url)
    if cached and time.time() - cached[0] < ROBOTS_CACHE_TTL:
        return cached[1]
    parser = None
    robots_url = base_url + "/robots.txt"
    try:
        resp = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=10)
        if resp.status_code == 200:
            parser = RobotFileParser()
            parser.parse(resp.text.splitlines())
        else:
            # No robots.txt (or an error page): treat as allow-all.
            logger.debug(
                f"No robots.txt at {robots_url} (HTTP {resp.status_code}); allowing"
            )
    except requests.RequestException as e:
        logger.warning(f"Could not fetch {robots_url} ({e}); assuming allowed")
    _robots_cache[base_url] = (time.time(), parser)
    return parser


def _robots_allowed(url: str) -> bool:
    """Check the target host's robots.txt before fetching a page."""
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return True
    parser = _get_robots_parser(f"{parsed.scheme}://{parsed.netloc}")
    return parser is None or parser.can_fetch(USER_AGENT, url)


def fetch_url(url: str, timeout: int = 30, max_retries: int = 3) -> str | None:
    """Fetch a URL and return its text content.

    Honors the host's robots.txt and identifies the scraper with an
    honest User-Agent.  If the request fails or returns a non-200 status
    code, None is returned instead of raising an exception.

    Args:
        url: The URL to fetch.
        timeout: The timeout for the request in seconds.
        max_retries: How many times to retry before giving up.

    Returns:
        The response text, or None if the request failed.

    """
    if not _robots_allowed(url):
        logger.warning(f"Skipping {url}: disallowed by robots.txt")
        return None
    headers = {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
    }
    for attempt in range(max_retries):
        # Introduce a randomized delay before each request to avoid rapid-fire
        # access patterns.  This can help with sites that rate-limit or
        # detect scraping.
        delay = random.uniform(*REQUEST_DELAY_RANGE)  # noqa: S311 - politeness jitter, not crypto
        logger.debug(
            f"Sleeping for {delay:.2f} seconds before fetching {url} (attempt {attempt + 1}/{max_retries})"
        )
        time.sleep(delay)
        # Log the request attempt
        logger.debug(f"Fetching URL: {url}")
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            # A traceback for every unreachable URL is noise, not signal.
            logger.error(f"Request to {url} failed with exception: {e}")  # noqa: TRY400
            continue
        # Check for success
        if resp.status_code == 200:
            resp.encoding = resp.apparent_encoding
            logger.debug(f"Fetched {url} successfully (length {len(resp.text)})")
            return resp.text
        # On rate limiting (429), wait longer and retry
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                wait_time = float(retry_after)
            else:
                # Exponential backoff: double the base delay each retry
                wait_time = (attempt + 1) * 2.0
            logger.warning(
                f"Got HTTP 429 for {url}, waiting {wait_time} seconds before retry"
            )
            time.sleep(wait_time)
            continue
        # A 403 is the server telling us to go away; retrying would only
        # add load, so give up immediately.
        if resp.status_code == 403:
            logger.warning(f"Got HTTP 403 for {url}, not retrying")
            return None
        # Other non-200 responses are logged and not retried
        logger.warning(f"Non-200 response for {url}: {resp.status_code}")
        return None
    logger.error(f"Failed to fetch {url} after {max_retries} attempts")
    return None


def get_sitemap_event_urls(
    sitemap_url: str, pattern: str, sub_sitemap_hints: list[str] | None = None
) -> list[str]:
    """Extract event URLs matching a pattern from a sitemap.xml file.

    Handles both regular sitemaps and sitemap index files (which contain
    ``<sitemap>`` entries pointing to sub-sitemaps).  In the index case,
    each sub-sitemap is fetched and searched for matching URLs.

    Args:
        sitemap_url: URL to the sitemap.xml document.
        pattern: A substring that must appear in the event URL (for
            example ``'/konzerte/'`` or ``'/en/events/'``).
        sub_sitemap_hints: Optional substrings used to pick which
            sub-sitemaps of an index to fetch (e.g. ``['concert']`` to
            skip a site's multi-megabyte bands/venues sitemaps).  If no
            sub-sitemap matches any hint, all are fetched as a fallback.

    Returns:
        A list of unique event URLs matching the pattern.

    """
    sitemap_text = fetch_url(sitemap_url)
    if not sitemap_text:
        return []

    # Detect sitemap index: contains <sitemap> elements (not <url> elements)
    sub_sitemap_urls = re.findall(r"<sitemap>\s*<loc>(.*?)</loc>", sitemap_text)
    if sub_sitemap_urls:
        sub_sitemap_urls = [u.strip() for u in sub_sitemap_urls]
        if sub_sitemap_hints:
            matching = [
                u
                for u in sub_sitemap_urls
                if any(hint in u for hint in sub_sitemap_hints)
            ]
            # If the site renamed its sub-sitemaps and nothing matches,
            # fall back to fetching all of them rather than missing events.
            if matching:
                skipped = len(sub_sitemap_urls) - len(matching)
                if skipped:
                    logger.debug(
                        f"Skipping {skipped} sub-sitemaps of {sitemap_url} "
                        f"not matching hints {sub_sitemap_hints}"
                    )
                sub_sitemap_urls = matching
        logger.debug(
            f"Detected sitemap index at {sitemap_url}, fetching {len(sub_sitemap_urls)} sub-sitemaps"
        )
        return _dedup_preserving_order(
            url
            for sub_url in sub_sitemap_urls
            for url in get_sitemap_event_urls(sub_url, pattern, sub_sitemap_hints)
        )

    # Regular sitemap: extract <loc> tags and filter by pattern
    loc_urls = re.findall(r"<loc>(.*?)</loc>", sitemap_text)
    return _dedup_preserving_order(
        loc.strip() for loc in loc_urls if pattern in loc.strip()
    )


def get_petzi_event_urls() -> list[str]:
    """Return a list of event URLs from the PETZI home page.

    The PETZI site no longer includes all events in its sitemap.  This
    helper scrapes the main agenda page (``https://www.petzi.ch/en/``)
    and extracts all anchor tags whose ``href`` attribute points into
    the ``/en/events/`` namespace.  As of 2025-11-03 this covers
    hundreds of upcoming events.  If the home page cannot be fetched,
    an empty list is returned.

    Returns:
        A list of absolute event URLs.

    """
    base_url = "https://www.petzi.ch"
    home_html = fetch_url("https://www.petzi.ch/en/")
    if not home_html:
        return []
    soup = BeautifulSoup(home_html, "html.parser")
    urls: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Accept both absolute and relative event URLs
        if "/en/events/" in href:
            # Build absolute URL
            url = href if href.startswith("http") else base_url + href
            urls.append(url)
    return _dedup_preserving_order(urls)


def parse_metalgigs_event(url: str) -> dict[str, str] | None:
    """Parse a metalgigs.ch event page and extract relevant information.

    The metalgigs event pages embed a JSON‑LD snippet describing the
    event according to the ``MusicEvent`` schema.  This function looks
    for that JSON‑LD and falls back to scraping individual fields if it
    cannot be found.

    Args:
        url: The URL of the metalgigs event page.

    Returns:
        A dictionary of event information, or None if the page could not
        be parsed.

    """
    logger.info(f"Parsing MetalGigs event: {url}")
    html = fetch_url(url)
    if not html:
        logger.error(f"Failed to retrieve MetalGigs event page: {url}")
        return None
    soup = BeautifulSoup(html, "html.parser")
    event: dict[str, str] = {
        "source": "metalgigs",
        "url": url,
    }

    # Try to extract JSON‑LD describing the MusicEvent.
    # Some pages embed the JSON directly inside a <script> tag without
    # specifying type="application/ld+json".  We'll search the raw text for
    # '"MusicEvent"'.
    script_json = None
    # Search each <script> element for a MusicEvent JSON-LD object.  Use
    # get_text() rather than .string because BeautifulSoup sets .string
    # to None when the script contains whitespace or comments.
    for script in soup.find_all("script"):
        text = script.get_text() or ""
        text = text.strip()
        if not text:
            continue
        # Look for the MusicEvent or Festival object inside the script text
        if (
            '"@type":"MusicEvent"' in text
            or '"MusicEvent"' in text
            or '"@type":"Festival"' in text
        ):
            start_idx = text.find("{")
            if start_idx >= 0:
                brace_count = 0
                for i, ch in enumerate(text[start_idx:]):
                    if ch == "{":
                        brace_count += 1
                    elif ch == "}":
                        brace_count -= 1
                        if brace_count == 0:
                            end_idx = start_idx + i + 1
                            candidate = text[start_idx:end_idx]
                            script_json = candidate
                            break
        if script_json:
            break

    if script_json:
        try:
            data = json.loads(script_json)
        except json.JSONDecodeError as e:
            # Malformed JSON-LD is an expected scrape outcome, not a fault.
            logger.error(f"Failed to decode JSON-LD for {url}: {e}")  # noqa: TRY400
            data = None
        if data:
            logger.debug(f"Parsed MusicEvent JSON-LD for {url}")
            # Extract fields from the MusicEvent JSON
            event["title"] = data.get("name", "")
            event["description"] = data.get("description", "")
            # Start and end dates are ISO dates (YYYY‑MM‑DD).  We'll keep
            # them as strings; if endDate is missing we'll duplicate startDate.
            start_date = data.get("startDate")
            end_date = data.get("endDate", start_date)
            event["start_date"] = start_date or ""
            event["end_date"] = end_date or ""
            # Doors open time may be present as ``doorTime``; convert to HH:MM
            event["doors_open"] = data.get("doorTime", "")
            # Location information
            location = (
                data.get("location", {})
                if isinstance(data.get("location"), dict)
                else {}
            )
            event["venue_name"] = location.get("name", "")
            address = (
                location.get("address", {})
                if isinstance(location.get("address"), dict)
                else {}
            )
            event["street_address"] = address.get("streetAddress", "")
            event["city"] = address.get("addressLocality", "")
            event["region"] = address.get("addressRegion", "")
            event["postal_code"] = address.get("postalCode", "")
            # Offer (ticket) information
            offers = (
                data.get("offers", {}) if isinstance(data.get("offers"), dict) else {}
            )
            # Price may be missing; ensure conversion to string
            price = offers.get("price")
            if price is not None:
                event["ticket_price"] = str(price)
            else:
                event["ticket_price"] = ""
            event["ticket_currency"] = offers.get("priceCurrency", "")
            event["ticket_url"] = offers.get("url", "")
            # Performers
            performers = data.get("performer", [])
            performer_names: list[str] = []
            if isinstance(performers, list):
                for p in performers:
                    if isinstance(p, dict):
                        name = p.get("name")
                        if name:
                            performer_names.append(name)
            event["performers"] = ", ".join(performer_names)
            event["event_status"] = data.get("eventStatus", "")
            # For Festival type, collect genres from performer entries if styles not set
            if not event.get("styles"):
                genre_set: list[str] = []
                seen_genres: set = set()
                for p in performers if isinstance(performers, list) else []:
                    if isinstance(p, dict):
                        for g in p.get("genre") or []:
                            if g and g not in seen_genres:
                                genre_set.append(g)
                                seen_genres.add(g)
                if genre_set:
                    event["styles"] = ", ".join(genre_set)
    else:
        logger.debug(f"No JSON-LD found for {url}, falling back to HTML parsing")

    # In addition to JSON‑LD, parse specific fields from the page for any
    # missing values.
    def extract_info_fragment(title: str) -> str:
        """Extract the text corresponding to an info fragment title (dt).

        For example, passing ``'Stil'`` returns the contents of the
        subsequent ``dd`` element in the ``info-fragment`` list.
        """
        dt = soup.find("dt", string=lambda s: s and s.strip().lower() == title.lower())
        if dt and dt.find_next_sibling("dd"):
            return dt.find_next_sibling("dd").get_text(" ", strip=True)
        return ""

    # If performers are missing, attempt to extract from the page title
    if not event.get("performers"):
        title = soup.find("h1")
        if title:
            event["performers"] = title.get_text(strip=True)

    # Styles (Stil)
    style_text = extract_info_fragment("Stil")
    event["styles"] = style_text
    if style_text:
        logger.debug(f"MetalGigs styles: {style_text}")
    # Date from fragment if missing
    if not event.get("start_date"):
        date_text = extract_info_fragment("Datum")
        # Convert e.g. "Mittwoch, 5. November 2025" to ISO date
        # We'll try to parse the German day and month names using datetime.
        if date_text:
            # Remove weekday
            parts = date_text.split(",", 1)
            date_part = parts[1] if len(parts) > 1 else parts[0]
            date_part = date_part.strip()
            # Map German month names to numbers
            german_months = {
                "Januar": 1,
                "Februar": 2,
                "März": 3,
                "April": 4,
                "Mai": 5,
                "Juni": 6,
                "Juli": 7,
                "August": 8,
                "September": 9,
                "Oktober": 10,
                "November": 11,
                "Dezember": 12,
            }
            m = re.search(r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\s+(\d{4})", date_part)
            if m:
                day = int(m.group(1))
                month_name = m.group(2)
                year = int(m.group(3))
                month = german_months.get(month_name, 0)
                try:
                    iso_date = datetime(year, month, day).strftime("%Y-%m-%d")
                    event["start_date"] = iso_date
                    event["end_date"] = iso_date
                    logger.debug(f"MetalGigs date: {iso_date}")
                except ValueError:
                    pass

    # Doors open and start time if missing
    if not event.get("doors_open"):
        # Einlass · Beginn might be separated by '·'
        times = extract_info_fragment("Einlass · Beginn")
        if times:
            # Could be like '19:00 · 19:30' or '19:00 · 19:30'
            time_parts = [
                t.strip() for t in re.split(r"[·|\u00b7]", times) if t.strip()
            ]
            if time_parts:
                event["doors_open"] = time_parts[0]
                if len(time_parts) > 1:
                    event["start_time"] = time_parts[1]
                    logger.debug(
                        f"MetalGigs times — doors open: {event.get('doors_open')}, start: {event.get('start_time')}"
                    )

    # Location if missing
    if not event.get("venue_name"):
        loc_text = extract_info_fragment("Location")
        if loc_text:
            # Example: 'Exil · Hardstrasse 245 · 8005 Zürich'
            parts = [p.strip() for p in loc_text.split("·")]
            if parts:
                event["venue_name"] = parts[0]
            if len(parts) > 1:
                event["street_address"] = parts[1]
            if len(parts) > 2:
                # e.g. "8005 Zürich" → city="Zürich", postal_code="8005"
                plz, city = split_leading_plz(parts[2])
                if plz and not event.get("postal_code"):
                    event["postal_code"] = plz
                event["city"] = city
            logger.debug(
                f"MetalGigs location: {event.get('venue_name')}, {event.get('street_address')}, {event.get('city')}"
            )

    # Ticket price if missing
    if not event.get("ticket_price"):
        ticket_link = soup.find("a", string=lambda s: s and "Tickets" in s)
        if ticket_link and ticket_link.get_text():
            # Extract the price from text like 'Tickets CHF 39.80'
            price_match = re.search(r"CHF\s*([0-9.,]+)", ticket_link.get_text())
            if price_match:
                event["ticket_price"] = price_match.group(1).replace(",", ".")
            event["ticket_url"] = ticket_link.get("href", "")
            logger.debug(
                f"MetalGigs ticket: {event.get('ticket_price')} CHF, URL: {event.get('ticket_url')}"
            )

    # Resolve canton from region + city (handles full names, case variants, PLZ prefixes)
    event["region"] = resolve_canton(event.get("region", ""), event.get("city", ""))
    logger.debug(f"MetalGigs canton resolved to: {event.get('region')!r}")

    return event


def parse_petzi_event(url: str) -> dict[str, str] | None:
    """Parse a petzi.ch event page and extract relevant information.

    Unlike metalgigs, PETZI pages do not embed JSON‑LD for events.  This
    function scrapes the structured parts of the page to extract the
    title, date, venue, price, door times, and other metadata.

    Args:
        url: The URL of the PETZI event page.

    Returns:
        A dictionary of event information, or None if the page could not
        be parsed.

    """
    logger.info(f"Parsing PETZI event: {url}")
    html = fetch_url(url)
    if not html:
        logger.error(f"Failed to retrieve PETZI event page: {url}")
        return None
    soup = BeautifulSoup(html, "html.parser")
    event: dict[str, str] = {
        "source": "petzi",
        "url": url,
    }

    # Title
    title_tag = soup.find("h1")
    if title_tag:
        event["title"] = title_tag.get_text(strip=True)
        logger.debug(f"PETZI title: {event['title']}")

    # Price.  PETZI uses an <h4> inside the mobile ticket bar and writes
    # something like "Price starting at CHF 49.00".  We'll search for
    # any h4 containing "CHF" and extract the amount.
    price = ""
    currency = ""
    price_tag = None
    for h4 in soup.find_all("h4"):
        text = h4.get_text(" ", strip=True)
        if "CHF" in text:
            price_tag = h4
            break
    if price_tag:
        match = re.search(r"CHF\s*([0-9.,]+)", price_tag.get_text())
        if match:
            price = match.group(1).replace(",", ".")
            currency = "CHF"
    event["ticket_price"] = price
    event["ticket_currency"] = currency
    if price:
        logger.debug(f"PETZI price: {price} {currency}")

    # Date.  The date appears in an <h3> element near the ticket bar.
    # Look for a string that resembles a day, month and year.
    date_text = ""
    for h3 in soup.find_all("h3"):
        text = h3.get_text(" ", strip=True)
        if re.search(r"\d{1,2} [A-Za-z]+ \d{4}", text):
            date_text = text
            break
    if date_text:
        # Convert English month names to numbers
        try:
            # Remove day of week if present
            parts = date_text.split()
            # Format may be: 'Thursday 6 November 2025'
            if len(parts) >= 3:
                # Take the last three parts
                day = int(parts[-3])
                month_name = parts[-2]
                year = int(parts[-1])
                month_num = datetime.strptime(month_name, "%B").month
                iso_date = datetime(year, month_num, day).strftime("%Y-%m-%d")
                event["start_date"] = iso_date
                event["end_date"] = iso_date
                logger.debug(f"PETZI date: {iso_date}")
        except (ValueError, IndexError):
            pass

    # Venue and city.  The venue appears in an <h4> after the date
    # container.  We search for the first <h4> that does not include "CHF"
    # and is not part of the ticket bar.
    venue = ""
    city = ""
    for h4 in soup.find_all("h4"):
        text = h4.get_text(" ", strip=True)
        # Skip price headings
        if "CHF" in text:
            continue
        # Skip headings that are part of organized by
        if "Organized by" in text:
            continue
        # Consider headings with an en dash (–) separating venue and city
        if "–" in text:
            parts = [part.strip() for part in text.split("–", 1)]
            if parts:
                venue = parts[0]
                if len(parts) > 1:
                    city = parts[1]
                break
        # Otherwise, if this h4 is inside the right column and there is an
        # accompanying list with door/open times, accept it
        if h4.find_next_sibling("ul"):
            venue = text
            break
    event["venue_name"] = venue
    event["city"] = city
    if venue or city:
        logger.debug(f"PETZI venue: {venue}, city: {city}")

    # Doors open and event start times.  These appear in <li> elements
    # labelled "Doors open at:" and "Event starts at:".
    doors_open = ""
    start_time = ""
    for li in soup.find_all("li"):
        li_text = li.get_text(" ", strip=True)
        if "Doors open" in li_text:
            # The time is on the next line after a <br>
            # Extract the last part after the colon
            match = re.search(r"Doors open at:\s*([0-9:.]+)", li_text)
            if match:
                doors_open = match.group(1)
        if "Event starts" in li_text:
            match = re.search(r"Event starts at:\s*([0-9:.]+)", li_text)
            if match:
                start_time = match.group(1)
    event["doors_open"] = doors_open
    event["start_time"] = start_time
    if doors_open or start_time:
        logger.debug(f"PETZI times — doors open: {doors_open}, start: {start_time}")

    # Organized by
    organizer = ""
    h5 = soup.find("h5")
    if h5:
        match = re.search(r"Organized by:\s*(.*)", h5.get_text(" ", strip=True))
        if match:
            organizer = match.group(1)
    event["organizer"] = organizer
    if organizer:
        logger.debug(f"PETZI organizer: {organizer}")

    # Tags (styles)
    tag_list = soup.find("section", {"class": "tag-list"})
    tags: list[str] = []
    if tag_list:
        for a in tag_list.find_all("a", class_="tag"):
            tag_text = a.get_text(strip=True)
            if tag_text:
                tags.append(tag_text)
    event["styles"] = ", ".join(tags)
    if tags:
        logger.debug(f"PETZI styles/tags: {event['styles']}")

    # Description (first paragraph following price)
    # There may be multiple <p> elements; choose the first text_block after
    # the price bar.
    description = ""
    for p in soup.find_all("p", class_="text_block"):
        text = p.get_text(" ", strip=True)
        if text:
            description = text
            break
    event["description"] = description
    if description:
        logger.debug(f"PETZI description (truncated): {description[:60]}…")

    # Resolve canton from region + city (handles full names, case variants)
    event["region"] = resolve_canton(event.get("region", ""), event.get("city", ""))
    logger.debug(f"PETZI canton resolved to: {event.get('region')!r}")

    return event


# ---------------------------------------------------------------------------
# Generic feed helpers
#
# The venue scrapers in venue_parsers.py read iCal, RSS, CSV and JSON rather
# than scraping markup wherever the venue publishes one. These helpers do the
# format-level work so each venue fetcher only has to map fields onto the row
# dict. Deliberately hand-rolled on the stdlib + bs4: the sitemap parsing above
# already sets that precedent, and neither `icalendar` nor `feedparser` earns a
# new dependency for the handful of properties actually read here.
# ---------------------------------------------------------------------------

SWISS_TZ = ZoneInfo("Europe/Zurich")


def to_swiss_local(dt: datetime) -> datetime:
    """Convert an aware datetime to naive Swiss wall-clock time.

    Event.date/doors/end_date hold the hour a show starts *as entered*, in
    local Swiss time (see CLAUDE.md, "Two clocks"). Feeds that publish UTC —
    the Events Manager iCal exports do — must be converted here or every
    show lands one or two hours early depending on the season.
    """
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(SWISS_TZ).replace(tzinfo=None)


def _unfold_ical(text: str) -> list[str]:
    """Join RFC 5545 folded continuation lines (leading space/tab) back up."""
    lines: list[str] = []
    for raw in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _unescape_ical(value: str) -> str:
    r"""Undo the TEXT escaping of RFC 5545 (\\n, \\, \\, ; and ,)."""
    out = value.replace("\\N", "\n").replace("\\n", "\n")
    out = out.replace("\\,", ",").replace("\\;", ";")
    return out.replace("\\\\", "\\").strip()


def _parse_ical_datetime(value: str, params: str) -> datetime | None:
    """Parse a DTSTART/DTEND value into naive Swiss wall-clock time.

    Handles the three forms these feeds actually emit: a trailing ``Z`` for
    UTC, an explicit ``TZID=`` parameter (already local), and a bare
    ``VALUE=DATE`` day with no time at all.
    """
    value = value.strip()
    if not value:
        return None
    try:
        if value.endswith("Z"):
            parsed = datetime.strptime(value, "%Y%m%dT%H%M%SZ")
            return to_swiss_local(parsed.replace(tzinfo=timezone.utc))
        if "T" in value:
            return datetime.strptime(value, "%Y%m%dT%H%M%S")
            # A TZID we don't recognise is still far likelier to be local
            # Swiss time than UTC for these venues, so take it as-is.
        if "VALUE=DATE" in params or len(value) == 8:
            return datetime.strptime(value, "%Y%m%d")
    except ValueError:
        logger.debug(f"Unparseable iCal datetime {value!r} (params {params!r})")
    return None


def parse_ical_feed(text: str) -> list[dict[str, object]]:
    """Split an iCalendar document into its VEVENTs.

    Returns one dict per event with ``uid``, ``summary``, ``description``,
    ``url``, ``location``, ``start`` and ``end`` (the last two naive Swiss
    wall-clock datetimes, or None). Malformed events are skipped rather than
    raising — a feed with one bad entry should still yield the rest.
    """
    events: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for line in _unfold_ical(text):
        if line.startswith("BEGIN:VEVENT"):
            current = {}
            continue
        if line.startswith("END:VEVENT"):
            if current:
                events.append(current)
            current = None
            continue
        if current is None or ":" not in line:
            continue
        name_part, _, value = line.partition(":")
        name, _, params = name_part.partition(";")
        name = name.upper()
        if name == "UID":
            current["uid"] = value.strip()
        elif name == "SUMMARY":
            current["summary"] = _unescape_ical(value)
        elif name == "DESCRIPTION":
            current["description"] = _unescape_ical(value)
        elif name == "URL":
            current["url"] = value.strip()
        elif name == "LOCATION":
            current["location"] = _unescape_ical(value)
        elif name == "DTSTART":
            current["start"] = _parse_ical_datetime(value, params)
        elif name == "DTEND":
            current["end"] = _parse_ical_datetime(value, params)
    logger.debug(f"Parsed {len(events)} VEVENTs from iCal feed")
    return events


def parse_rss_items(text: str) -> list[dict[str, str]]:
    """Extract ``<item>`` entries from an RSS/Atom document.

    Returns dicts with ``title``, ``link``, ``description`` and ``pub_date``.
    Parsed with bs4's XML-ish HTML parser so no lxml dependency is needed;
    CDATA sections come through as text.
    """
    with warnings.catch_warnings():
        # bs4 warns that this is XML; html.parser handles these feeds fine and
        # parsing as XML would mean adding lxml for no gain.
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(text, "html.parser")
    items: list[dict[str, str]] = []
    for item in soup.find_all("item"):
        entry = {}
        for field, tag in (
            ("title", "title"),
            ("link", "link"),
            ("description", "description"),
            ("pub_date", "pubdate"),
        ):
            found = item.find(tag)
            entry[field] = found.get_text(strip=True) if found else ""
        # RSS <link> text is sometimes empty when the parser treats it as a
        # void element; fall back to the guid, which these feeds set to the
        # permalink.
        if not entry["link"]:
            guid = item.find("guid")
            entry["link"] = guid.get_text(strip=True) if guid else ""
        items.append(entry)
    logger.debug(f"Parsed {len(items)} items from RSS feed")
    return items


def fetch_json(url: str) -> object | None:
    """Fetch a URL and decode it as JSON, or None on failure.

    Goes through fetch_url() so robots.txt, the honest User-Agent, the
    inter-request delay and the 429/403 handling all still apply.
    """
    text = fetch_url(url)
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError) as exc:
        # A non-JSON response is an expected scrape outcome, not a fault.
        logger.error(f"Could not decode JSON from {url}: {exc}")  # noqa: TRY400
        return None


def html_to_text(value: str) -> str:
    """Flatten an HTML description into plain text with tidy whitespace."""
    if not value:
        return ""
    text = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


def write_csv(events: Iterable[dict[str, str]], filename: str) -> None:
    """Write a list of event dictionaries to a CSV file.

    The header is determined by the union of all keys.  Missing keys
    produce blank cells.

    Args:
        events: Iterable of event dictionaries.
        filename: Output CSV filename.

    """
    # Determine all field names
    fieldnames = set()
    events_list: list[dict[str, str]] = []
    for event in events:
        events_list.append(event)
        fieldnames.update(event.keys())
    # Order the columns so that commonly used fields come first
    preferred_order = [
        "source",
        "url",
        "title",
        "performers",
        "styles",
        "description",
        "start_date",
        "end_date",
        "doors_open",
        "start_time",
        "venue_name",
        "street_address",
        "city",
        "region",
        "postal_code",
        "ticket_price",
        "ticket_currency",
        "ticket_url",
        "organizer",
        "event_status",
    ]
    # Append any remaining fields not in the preferred order
    remaining = [f for f in fieldnames if f not in preferred_order]
    header = preferred_order + sorted(remaining)
    with Path(filename).open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for event in events_list:
            writer.writerow(event)


def main() -> None:
    """Main entry point of the script."""
    configure_logging()
    events: list[dict[str, str]] = []
    # Fetch and parse metalgigs concerts and festivals
    print("Fetching metalgigs event URLs…", file=sys.stderr)
    mg_urls = get_sitemap_event_urls(
        "https://metalgigs.ch/sitemap.xml", "/konzerte/", sub_sitemap_hints=["concert"]
    )
    mg_festival_urls = get_sitemap_event_urls(
        "https://metalgigs.ch/sitemap.xml",
        "/festivals/",
        sub_sitemap_hints=["festival"],
    )
    print(
        f"Found {len(mg_urls)} metalgigs concerts, {len(mg_festival_urls)} festivals",
        file=sys.stderr,
    )
    all_mg_urls = mg_urls + mg_festival_urls
    for idx, url in enumerate(all_mg_urls, 1):
        parsed = parse_metalgigs_event(url)
        if parsed:
            events.append(parsed)
            logger.info(f"Added MetalGigs event: {url}")
        else:
            logger.warning(f"Skipped MetalGigs event due to parse failure: {url}")
        # Print progress occasionally
        if idx % 50 == 0:
            print(
                f"Processed {idx}/{len(all_mg_urls)} metalgigs events", file=sys.stderr
            )
    # Fetch and parse PETZI events
    print("Fetching PETZI event URLs…", file=sys.stderr)
    # Try sitemap first; if empty, fall back to scraping the agenda page
    petzi_urls = get_sitemap_event_urls(
        "https://www.petzi.ch/en/sitemap.xml", "/en/events/"
    )
    if not petzi_urls:
        petzi_urls = get_petzi_event_urls()
    print(f"Found {len(petzi_urls)} PETZI events", file=sys.stderr)
    for idx, url in enumerate(petzi_urls, 1):
        parsed = parse_petzi_event(url)
        if parsed:
            events.append(parsed)
            logger.info(f"Added PETZI event: {url}")
        else:
            logger.warning(f"Skipped PETZI event due to parse failure: {url}")
        if idx % 50 == 0:
            print(f"Processed {idx}/{len(petzi_urls)} PETZI events", file=sys.stderr)
    INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = str(INSTANCE_DIR / "events.csv")
    print(f"Writing {len(events)} events to {out_path}", file=sys.stderr)
    write_csv(events, out_path)
    print("Done", file=sys.stderr)


if __name__ == "__main__":
    main()
