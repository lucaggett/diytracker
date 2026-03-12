"""Scrape events from metalgigs.ch and petzi.ch and save to a CSV file.

This script downloads all event URLs from the public `sitemap.xml` files of
metalgigs.ch and petzi.ch.  Each event page is then parsed to extract as
much useful information as possible, such as the event title, date,
location, ticket prices, door times, and any other structured data.  The
combined results from both websites are written to a CSV file named
``events.csv`` in the current working directory.

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
import re
import sys
import os
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
import time
import random

# Allow importing shared utilities from the project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import resolve_canton

# Configure verbose logging
import logging

# Set up a logger for the scraper.  Log messages are written to a file
# and to stderr.  This aids troubleshooting by capturing all requests
# and parsing operations.  The log level can be adjusted here; DEBUG
# includes the most detail.  Each message includes a timestamp and
# severity level.
logger = logging.getLogger("scrape_events")
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

# File handler writes logs to scrape_events.log
file_handler = logging.FileHandler("scrape_events.log", mode="w", encoding="utf-8")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Stream handler outputs logs to stderr
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)
logger.addHandler(stream_handler)


# Random delay (in seconds) between requests to reduce the likelihood
# of triggering rate-limiting.  Values are inclusive in random.uniform.
REQUEST_DELAY_RANGE: Tuple[float, float] = (0.5, 1.5)


def fetch_url(url: str, timeout: int = 30, max_retries: int = 3) -> Optional[str]:
    """Fetch a URL and return its text content.

    A custom User‑Agent is supplied to reduce the chance of the server
    returning a 403.  If the request fails or returns a non‑200 status
    code, None is returned instead of raising an exception.

    Args:
        url: The URL to fetch.
        timeout: The timeout for the request in seconds.

    Returns:
        The response text, or None if the request failed.
    """
    # Rotate among several common browser User‑Agent strings to reduce
    # the chance of being flagged as a bot.  Each request picks one at
    # random.
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/117.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.5993.91 Safari/537.36",
    ]
    user_agent = random.choice(USER_AGENTS)
    headers = {
        "User-Agent": user_agent,
        "Accept-Language": "en-US,en;q=0.9",
    }
    for attempt in range(max_retries):
        # Introduce a randomized delay before each request to avoid rapid-fire
        # access patterns.  This can help with sites that rate-limit or
        # detect scraping.
        delay = random.uniform(*REQUEST_DELAY_RANGE)
        logger.debug(f"Sleeping for {delay:.2f} seconds before fetching {url} (attempt {attempt+1}/{max_retries})")
        time.sleep(delay)
        # Log the request attempt
        logger.debug(f"Fetching URL: {url}")
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
        except requests.RequestException as e:
            logger.error(f"Request to {url} failed with exception: {e}")
            continue
        # Check for success
        if resp.status_code == 200:
            resp.encoding = resp.apparent_encoding
            logger.debug(f"Fetched {url} successfully (length {len(resp.text)})")
            return resp.text
        # On rate limiting (429) or forbidden (403), wait longer and retry
        if resp.status_code in (403, 429):
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                wait_time = float(retry_after)
            else:
                # Exponential backoff: double the base delay each retry
                wait_time = (attempt + 1) * 2.0
            logger.warning(f"Got HTTP {resp.status_code} for {url}, waiting {wait_time} seconds before retry")
            time.sleep(wait_time)
            continue
        # Other non-200 responses are logged and not retried
        logger.warning(f"Non-200 response for {url}: {resp.status_code}")
        return None
    logger.error(f"Failed to fetch {url} after {max_retries} attempts")
    return None


def get_sitemap_event_urls(sitemap_url: str, pattern: str) -> List[str]:
    """Extract event URLs matching a pattern from a sitemap.xml file.

    The sitemap is treated as plain text; all substrings containing the
    provided pattern are returned.  Duplicate URLs are removed.

    Args:
        sitemap_url: URL to the sitemap.xml document.
        pattern: A substring that must appear in the event URL (for
            example ``'/konzerte/'`` or ``'/en/events/'``).

    Returns:
        A list of unique event URLs matching the pattern.
    """
    sitemap_text = fetch_url(sitemap_url)
    if not sitemap_text:
        return []
    # Extract the text inside <loc> tags which contain fully qualified
    # URLs.  This avoids capturing trailing markup like </loc>.
    loc_urls = re.findall(r"<loc>(.*?)</loc>", sitemap_text)
    urls: List[str] = []
    for loc in loc_urls:
        loc = loc.strip()
        # Only include entries that match the desired substring pattern
        if pattern in loc:
            urls.append(loc)
    # Deduplicate while preserving order
    seen: set = set()
    unique_urls: List[str] = []
    for url in urls:
        if url not in seen:
            unique_urls.append(url)
            seen.add(url)
    return unique_urls


def get_petzi_event_urls() -> List[str]:
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
    urls: List[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        # Accept both absolute and relative event URLs
        if "/en/events/" in href:
            # Build absolute URL
            if href.startswith("http"):
                url = href
            else:
                url = base_url + href
            urls.append(url)
    # Deduplicate
    seen: set = set()
    unique_urls: List[str] = []
    for url in urls:
        if url not in seen:
            unique_urls.append(url)
            seen.add(url)
    return unique_urls


def parse_metalgigs_event(url: str) -> Optional[Dict[str, str]]:
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
    event: Dict[str, str] = {
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
        # Look for the MusicEvent object inside the script text
        if "\"@type\":\"MusicEvent\"" in text or "\"MusicEvent\"" in text:
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
            logger.error(f"Failed to decode JSON-LD for {url}: {e}")
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
            location = data.get("location", {}) if isinstance(data.get("location"), dict) else {}
            event["venue_name"] = location.get("name", "")
            address = location.get("address", {}) if isinstance(location.get("address"), dict) else {}
            event["street_address"] = address.get("streetAddress", "")
            event["city"] = address.get("addressLocality", "")
            event["region"] = address.get("addressRegion", "")
            event["postal_code"] = address.get("postalCode", "")
            # Offer (ticket) information
            offers = data.get("offers", {}) if isinstance(data.get("offers"), dict) else {}
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
            performer_names: List[str] = []
            if isinstance(performers, list):
                for p in performers:
                    if isinstance(p, dict):
                        name = p.get("name")
                        if name:
                            performer_names.append(name)
            event["performers"] = ", ".join(performer_names)
            event["event_status"] = data.get("eventStatus", "")
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
            text = dt.find_next_sibling("dd").get_text(" ", strip=True)
            return text
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
                except Exception:
                    pass

    # Doors open and start time if missing
    if not event.get("doors_open"):
        # Einlass · Beginn might be separated by '·'
        times = extract_info_fragment("Einlass · Beginn")
        if times:
            # Could be like '19:00 · 19:30' or '19:00 · 19:30'
            time_parts = [t.strip() for t in re.split(r"[·|\u00b7]", times) if t.strip()]
            if time_parts:
                event["doors_open"] = time_parts[0]
                if len(time_parts) > 1:
                    event["start_time"] = time_parts[1]
                    logger.debug(f"MetalGigs times — doors open: {event.get('doors_open')}, start: {event.get('start_time')}")

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
                raw_city = parts[2]
                # Strip leading PLZ (e.g. "8005 Zürich" → city="Zürich", postal_code="8005")
                plz_match = re.match(r'^(\d{4})\s+(.+)$', raw_city)
                if plz_match:
                    if not event.get("postal_code"):
                        event["postal_code"] = plz_match.group(1)
                    event["city"] = plz_match.group(2)
                else:
                    event["city"] = raw_city
            logger.debug(f"MetalGigs location: {event.get('venue_name')}, {event.get('street_address')}, {event.get('city')}")

    # Ticket price if missing
    if not event.get("ticket_price"):
        ticket_link = soup.find("a", string=lambda s: s and "Tickets" in s)
        if ticket_link and ticket_link.get_text():
            # Extract the price from text like 'Tickets CHF 39.80'
            price_match = re.search(r"CHF\s*([0-9.,]+)", ticket_link.get_text())
            if price_match:
                event["ticket_price"] = price_match.group(1).replace(",", ".")
            event["ticket_url"] = ticket_link.get("href", "")
            logger.debug(f"MetalGigs ticket: {event.get('ticket_price')} CHF, URL: {event.get('ticket_url')}")

    # Resolve canton from region + city (handles full names, case variants, PLZ prefixes)
    event["region"] = resolve_canton(event.get("region", ""), event.get("city", ""))
    logger.debug(f"MetalGigs canton resolved to: {event.get('region')!r}")

    return event


def parse_petzi_event(url: str) -> Optional[Dict[str, str]]:
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
    event: Dict[str, str] = {
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
        except Exception:
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
    tags: List[str] = []
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


def write_csv(events: Iterable[Dict[str, str]], filename: str) -> None:
    """Write a list of event dictionaries to a CSV file.

    The header is determined by the union of all keys.  Missing keys
    produce blank cells.

    Args:
        events: Iterable of event dictionaries.
        filename: Output CSV filename.
    """
    # Determine all field names
    fieldnames = set()
    events_list: List[Dict[str, str]] = []
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
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for event in events_list:
            writer.writerow(event)


def main() -> None:
    """Main entry point of the script."""
    events: List[Dict[str, str]] = []
    # Fetch and parse metalgigs events
    print("Fetching metalgigs event URLs…", file=sys.stderr)
    mg_urls = get_sitemap_event_urls("https://metalgigs.ch/sitemap.xml", "/konzerte/")
    print(f"Found {len(mg_urls)} metalgigs events", file=sys.stderr)
    for idx, url in enumerate(mg_urls, 1):
        parsed = parse_metalgigs_event(url)
        if parsed:
            events.append(parsed)
            logger.info(f"Added MetalGigs event: {url}")
        else:
            logger.warning(f"Skipped MetalGigs event due to parse failure: {url}")
        # Print progress occasionally
        if idx % 50 == 0:
            print(f"Processed {idx}/{len(mg_urls)} metalgigs events", file=sys.stderr)
    # Fetch and parse PETZI events
    print("Fetching PETZI event URLs…", file=sys.stderr)
    # Try sitemap first; if empty, fall back to scraping the agenda page
    petzi_urls = get_sitemap_event_urls("https://www.petzi.ch/en/sitemap.xml", "/en/events/")
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
    # Write to CSV
    print(f"Writing {len(events)} events to events.csv", file=sys.stderr)
    write_csv(events, "events.csv")
    print("Done", file=sys.stderr)


if __name__ == "__main__":
    main()