"""Per-venue scrapers for the DIY spaces the aggregators never list.

metalgigs and petzi between them cover the big rooms. The venues here — squats,
autonomous centres, small bars — publish their programme only on their own
site, so every show was being typed in by hand. Each function below reads one
venue and returns rows in the same shape ``ingest_event()`` takes, the same
contract as ``parse_petzi_event``.

Two things differ from the aggregator scrapers and both matter:

* **Most of these are listing fetchers.** One HTTP request returns the whole
  programme, so a fetcher returns a *list* of rows rather than parsing one
  event page into one row. Only Gare de Lion and Safari Bar need a second
  request per event, and they say so.

* **Rows without a per-event URL must not set ``url``.** ``ScrapedEvent.url``
  is UNIQUE, so putting the listing page on every row would make all but the
  first collide and vanish as duplicates. Those venues set ``source_id``
  instead — a stable digest of date+title — which dedups through the
  ``UniqueConstraint("source", "source_id")`` on the model.

Venue name/address/PLZ come from the registry in ``venue_sources.py`` and are
the values already in the database, not whatever the site prints, so ingest
matches the existing venue instead of creating a near-duplicate.
"""

import contextlib
import csv
import hashlib
import io
import json
import re
from datetime import date, datetime, timedelta

from bs4 import BeautifulSoup

from diytracker.services.scrape_events import (
    fetch_json,
    fetch_url,
    html_to_text,
    logger,
    parse_rss_items,
)

# Shows more than a day in the past are dropped: several of these sites keep
# their whole archive on the listing page, and re-importing years of finished
# concerts would bury the moderation queue. One day of slack so a show that
# ran past midnight still counts as current.
_PAST_GRACE = timedelta(days=1)

GERMAN_MONTHS = {
    "januar": 1,
    "februar": 2,
    "märz": 3,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}


def _today() -> date:
    """Indirection so tests can freeze the clock.

    Every fetcher filters against "now", and the fixtures hold real captured
    dates that would otherwise start failing the day they age past the grace
    window.
    """
    return date.today()


def _is_current(start_date: date | None) -> bool:
    return bool(start_date) and start_date >= _today() - _PAST_GRACE


def _source_id(*parts) -> str:
    """Stable per-source record id for rows that have no URL of their own."""
    joined = "|".join(str(p or "") for p in parts)
    return hashlib.sha1(joined.encode("utf-8"), usedforsecurity=False).hexdigest()[:32]


def _fmt_date(value) -> str:
    return value.strftime("%Y-%m-%d") if value else ""


def _fmt_time(value) -> str:
    return value.strftime("%H:%M") if value else ""


def _clean_time(value: str) -> str:
    """Normalise the many ways these sites write a clock time to HH:MM.

    Deliberately accepts only ``:`` and ``h`` as separators. A dot would also
    read as a time on these pages, but ``07.08.2026`` would then parse as
    07:08 — every German date on the page would become a bogus door time.
    """
    if not value:
        return ""
    match = re.search(r"(\d{1,2})\s*[:h]\s*(\d{2})", str(value))
    if not match:
        match = re.search(r"^\s*(\d{1,2})\s*h\s*$", str(value))
        if match:
            return f"{int(match.group(1)):02d}:00"
        return ""
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return ""
    return f"{hour:02d}:{minute:02d}"


def _german_dates(text: str) -> list[date]:
    """Every "9. August 2026" style date in a string, in order."""
    found = []
    for day, name, year in re.findall(
        r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\s+(\d{4})", text
    ):
        month = GERMAN_MONTHS.get(name.strip().lower())
        if not month:
            continue
        try:
            found.append(date(int(year), month, int(day)))
        except ValueError:
            continue
    return found


def _year_nearest_today(month: int, day: int) -> date | None:
    """Pick the year that puts month/day closest to today.

    For the sites that print a day and month but no year. Choosing "the next
    occurrence" instead would push a stale May entry read in August out to the
    following May and invent an event that was never scheduled; nearest-year
    keeps it in the past where _is_current() then drops it.
    """
    today = _today()
    best = None
    for year in (today.year - 1, today.year, today.year + 1):
        try:
            candidate = date(year, month, day)
        except ValueError:
            continue
        if best is None or abs((candidate - today).days) < abs((best - today).days):
            best = candidate
    return best


def _price(value: str) -> str:
    """Pull a bare number out of a price string, or '' for free/unknown."""
    if not value:
        return ""
    text = str(value)
    if re.search(r"frei|gratis|libre|free|kollekte", text, re.IGNORECASE):
        return "0"
    match = re.search(r"(\d+(?:[.,]\d{1,2})?)", text)
    return match.group(1).replace(",", ".") if match else ""


def _row(source: str, **fields) -> dict[str, str]:
    """Build a row with every key ingest knows about defaulted to ''."""
    row = {
        "source": source,
        "url": "",
        "title": "",
        "performers": "",
        "styles": "",
        "description": "",
        "start_date": "",
        "end_date": "",
        "doors_open": "",
        "start_time": "",
        "ticket_price": "",
        "ticket_currency": "",
        "ticket_url": "",
        "organizer": "",
        "event_status": "",
        "source_id": "",
    }
    row.update({k: v for k, v in fields.items() if v is not None})
    return row


def _soup(url: str) -> BeautifulSoup | None:
    html = fetch_url(url)
    if not html:
        logger.error(f"Could not fetch listing page {url}")
        return None
    return BeautifulSoup(html, "html.parser")


# ---------------------------------------------------------------------------
# The two zureich.rip squats. Both run WordPress Events Manager and both
# advertise an iCal export that turns out to be unusable — see fetch_altepost.
# ---------------------------------------------------------------------------


def fetch_altepost() -> list[dict[str, str]]:
    """Alte Post, Zürich Seebach — Events Manager listing page.

    Both zureich.rip venues expose ``?ical=1``, which looked like the obvious
    source, but that export returns the *oldest* fifty events and ignores both
    ``scope=future`` and ``limit`` — so it is a stale archive that contains
    none of the current programme. The rendered listing is the only place the
    upcoming shows appear, hence HTML.
    """
    soup = _soup("https://altepost.zureich.rip/events/")
    if not soup:
        return []
    rows = []
    for item in soup.select(".em-item.em-event"):
        title_link = item.select_one(".em-item-title a")
        if not title_link:
            continue
        date_block = item.select_one(".em-event-date")
        dates = _german_dates(
            date_block.get_text(" ", strip=True) if date_block else ""
        )
        if not dates:
            continue
        start = dates[0]
        if not _is_current(start):
            continue
        time_block = item.select_one(".em-event-time")
        raw_time = time_block.get_text(" ", strip=True) if time_block else ""
        desc = item.select_one(".em-item-desc")
        rows.append(
            _row(
                "altepost",
                url=title_link.get("href") or "",
                title=html_to_text(title_link.get_text(" ", strip=True)),
                description=html_to_text(
                    desc.get_text(" ", strip=True) if desc else ""
                ),
                start_date=_fmt_date(start),
                end_date=_fmt_date(dates[1]) if len(dates) > 1 else "",
                # "All Day" yields no time, which is correct for the
                # multi-day closures these listings also carry.
                start_time=_clean_time(raw_time),
            )
        )
    return rows


def fetch_postsquat() -> list[dict[str, str]]:
    """Post Squat, Zürich Wipkingen — themed Events Manager listing.

    Same plugin as Alte Post but a different theme, so different markup: each
    show is one `a.event_link` whose text reads
    "So., 9. August 2026 – 18:30" followed by the title. The venue's iCal
    export has the same oldest-fifty problem described in fetch_altepost().
    """
    soup = _soup("https://post.zureich.rip/kalender-alle/")
    if not soup:
        return []
    rows = []
    for link in soup.select("a.event_link"):
        date_tag = link.select_one(".event_datum")
        title_tag = link.select_one(".event_title")
        if not date_tag or not title_tag:
            continue
        dates = _german_dates(date_tag.get_text(" ", strip=True))
        if not dates or not _is_current(dates[0]):
            continue
        # The time sits in the anchor's own text after the date span.
        after = date_tag.next_sibling
        raw_time = str(after) if after else ""
        rows.append(
            _row(
                "postsquat",
                url=link.get("href") or "",
                title=html_to_text(title_tag.get_text(" ", strip=True)),
                start_date=_fmt_date(dates[0]),
                end_date=_fmt_date(dates[1]) if len(dates) > 1 else "",
                start_time=_clean_time(raw_time),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Venues that publish a machine-readable feed — CSV, REST, RSS or embedded JSON
# ---------------------------------------------------------------------------


def fetch_horstklub() -> list[dict[str, str]]:
    """Horstklub, Kreuzlingen — the CSV its own front page loads.

    The site is static HTML that fetches events.csv client-side and renders it
    with PapaParse, so reading the CSV is reading exactly what a visitor sees,
    at one request instead of many. Columns are German; `Art` separates
    concerts from darts nights and plain bar openings.
    """
    text = fetch_url("https://horstklub.ch/events.csv")
    if not text:
        return []
    rows = []
    for record in csv.DictReader(io.StringIO(text)):
        kind = (record.get("Art") or "").strip().lower()
        if kind not in ("concert", "horstevent"):
            continue
        try:
            # Two-digit years: 04.10.25 -> 2025-10-04.
            start = datetime.strptime((record.get("Datum") or "").strip(), "%d.%m.%y")
        except ValueError:
            logger.debug(f"Horstklub: unparseable date {record.get('Datum')!r}")
            continue
        if not _is_current(start.date()):
            continue
        bands = [(record.get(f"Band{n}") or "").strip() for n in range(1, 5)]
        performers = ", ".join(b for b in bands if b)
        title = performers or (record.get("Textdavor") or "").strip()
        rows.append(
            _row(
                "horstklub",
                title=title,
                performers=performers,
                styles=html_to_text(record.get("Genres") or ""),
                description=html_to_text(record.get("Textdavor") or ""),
                start_date=_fmt_date(start),
                doors_open=_clean_time(record.get("Startzeit")),
                start_time=_clean_time(record.get("Konzertstart")),
                ticket_price=_price(record.get("Damage")),
                ticket_currency="CHF",
                source_id=_source_id("horstklub", start.date(), title),
            )
        )
    return rows


def fetch_treppenhaus() -> list[dict[str, str]]:
    """Café Bar Treppenhaus, Rorschach — custom WP REST `events` post type.

    The theme stores date/time/price in a `details` meta block, already split
    into fields, so no HTML parsing is needed at all.
    """
    data = fetch_json("https://treppenhaus.ch/wp-json/wp/v2/events?per_page=50")
    if not isinstance(data, list):
        return []
    rows = []
    for entry in data:
        details = entry.get("details") or {}

        def first(key, details=details):
            value = details.get(key)
            return (value[0] if isinstance(value, list) and value else "") or ""

        try:
            start = datetime.strptime(first("date_full"), "%Y%m%d")
        except ValueError:
            continue
        if not _is_current(start.date()):
            continue
        title = html_to_text((entry.get("title") or {}).get("rendered", ""))
        rows.append(
            _row(
                "treppenhaus",
                url=entry.get("link") or "",
                title=title,
                styles=first("category"),
                description=html_to_text(
                    (entry.get("excerpt") or {}).get("rendered", "")
                ),
                start_date=_fmt_date(start),
                start_time=_clean_time(first("time")),
                ticket_price=_price(first("price")),
                ticket_currency="CHF",
                ticket_url=first("ticket"),
                source_id=str(entry.get("id") or "") or None,
            )
        )
    return rows


def fetch_eldorado() -> list[dict[str, str]]:
    """Eldorado, Biel/Bienne — The Events Calendar REST API.

    `start_date` is already local time in the venue's own timezone, so it is
    used as-is rather than the `utc_start_date` sibling field.
    """
    data = fetch_json(
        "https://eldoradobielbienne.ch/wp-json/tribe/events/v1/events?per_page=50"
    )
    if not isinstance(data, dict):
        return []
    rows = []
    for entry in data.get("events") or []:
        try:
            start = datetime.strptime(entry.get("start_date", ""), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if not _is_current(start.date()):
            continue
        end = None
        with contextlib.suppress(ValueError):
            end = datetime.strptime(entry.get("end_date", ""), "%Y-%m-%d %H:%M:%S")
        rows.append(
            _row(
                "eldorado",
                url=entry.get("url") or "",
                title=html_to_text(entry.get("title") or ""),
                description=html_to_text(entry.get("description") or ""),
                start_date=_fmt_date(start),
                end_date=_fmt_date(end) if end and end.date() > start.date() else "",
                start_time=_fmt_time(start),
                ticket_price=_price(entry.get("cost") or ""),
                ticket_currency="CHF" if entry.get("cost") else "",
                ticket_url=entry.get("website") or "",
                source_id=str(entry.get("id") or "") or None,
            )
        )
    return rows


def fetch_taptab() -> list[dict[str, str]]:
    """TapTab, Schaffhausen — Joomla RSS.

    The plain /feed path 404s; the programme feed is the query-string form.
    `pubDate` carries the actual event date and time (not a publication
    timestamp), which the `YYYY_MM_DD_` title prefix corroborates.
    """
    text = fetch_url("https://taptab.ch/?format=feed&type=rss")
    if not text:
        return []
    rows = []
    for item in parse_rss_items(text):
        start = None
        try:
            start = datetime.strptime(
                item["pub_date"][:25].strip(), "%a, %d %b %Y %H:%M:%S"
            )
        except (ValueError, KeyError):
            # Fall back to the date encoded in the permalink slug.
            match = re.search(r"/(\d{4})-(\d{2})-(\d{2})", item.get("link", ""))
            if match:
                start = datetime(*(int(g) for g in match.groups()))
        if not start or not _is_current(start.date()):
            continue
        # Titles are filenames: 2026_08_29_HAUSFEST_2 -> HAUSFEST 2
        title = re.sub(r"^\d{4}[_-]\d{2}[_-]\d{2}[_-]?", "", item.get("title", ""))
        title = title.replace("_", " ").strip() or item.get("title", "")
        rows.append(
            _row(
                "taptab",
                url=item.get("link") or "",
                title=title,
                description=html_to_text(item.get("description") or ""),
                start_date=_fmt_date(start),
                start_time=_fmt_time(start),
                source_id=_source_id("taptab", start.date(), title),
            )
        )
    return rows


def fetch_provitreff() -> list[dict[str, str]]:
    """Provitreff, Zürich — the JSON Next.js embeds in the homepage.

    There is no feed, but the page ships its own hydration payload with the
    programme already structured, which beats parsing the rendered markup.
    Dates are the weak point: `eventTime` is written by hand as "08.05 / 23:00"
    or "23.5. / 22:00" with no year, so the year is inferred from the month
    block the entry sits in.
    """
    html = fetch_url("https://provitreff.ch/")
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        logger.error("Provitreff: __NEXT_DATA__ payload missing (site redesigned?)")
        return []
    try:
        data = json.loads(tag.string)
    except ValueError:
        logger.error("Provitreff: __NEXT_DATA__ is not valid JSON")
        return []

    blocks: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("__typename") == "program_programm_BlockType":
                blocks.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(data)

    rows = []
    for block in blocks:
        month = GERMAN_MONTHS.get((block.get("month") or "").strip().lower())
        for entry in block.get("selected_events") or []:
            if entry.get("__typename") != "events_event_Entry":
                continue
            raw = (entry.get("eventTime") or "").strip()
            match = re.match(r"(\d{1,2})\s*[./]\s*(\d{1,2})", raw)
            if not match:
                continue
            day, entry_month = int(match.group(1)), int(match.group(2))
            entry_month = entry_month or month or 0
            if not (1 <= entry_month <= 12 and 1 <= day <= 31):
                continue
            # No year anywhere in the payload, so infer the nearest one. The
            # page keeps finished months around, and those must stay in the
            # past to be dropped rather than being rolled forward a year into
            # events that were never scheduled.
            start = _year_nearest_today(entry_month, day)
            if not _is_current(start):
                continue
            title = html_to_text(entry.get("title") or "")
            time_match = re.search(r"/\s*(\d{1,2}[:.]\d{2})", raw)
            rows.append(
                _row(
                    "provitreff",
                    title=title,
                    description=html_to_text(entry.get("eventInfo") or ""),
                    start_date=_fmt_date(start),
                    start_time=_clean_time(time_match.group(1)) if time_match else "",
                    source_id=_source_id("provitreff", start, title),
                )
            )
    return rows


# ---------------------------------------------------------------------------
# Listing pages with stable hooks — no feed, but markup worth trusting
# ---------------------------------------------------------------------------


def fetch_badbonn() -> list[dict[str, str]]:
    """Bad Bonn, Düdingen — each listing anchor carries data-* attributes.

    The club hangs title, date, time and price straight off the link, so the
    listing page alone has everything and no event page needs fetching.
    """
    soup = _soup("https://club.badbonn.ch/")
    if not soup:
        return []
    rows = []
    for anchor in soup.select("a[data-date]"):
        try:
            start = datetime.strptime(anchor["data-date"].strip(), "%d.%m.%Y")
        except (ValueError, KeyError):
            continue
        if not _is_current(start.date()):
            continue
        title = (anchor.get("data-title") or "").strip()
        rows.append(
            _row(
                "badbonn",
                url=anchor.get("href") or "",
                title=title,
                performers=title,
                start_date=_fmt_date(start),
                start_time=_clean_time(anchor.get("data-time")),
                ticket_price=_price(anchor.get("data-price")),
                ticket_currency="CHF" if anchor.get("data-price") else "",
                source_id=_source_id("badbonn", start.date(), title),
            )
        )
    return rows


def fetch_werkk() -> list[dict[str, str]]:
    """Werkk Kulturlokal, Baden — REDAXO agenda with <time datetime>.

    The layout renders each event twice (a mobile and a desktop block), so
    rows are deduplicated on the event href.
    """
    soup = _soup("https://werkk-baden.ch/agenda/programm/")
    if not soup:
        return []
    rows = []
    seen = set()
    for element in soup.select("time[datetime]"):
        anchor = element.find_parent("a")
        if not anchor or not anchor.get("href"):
            continue
        href = anchor["href"]
        if href in seen:
            continue
        try:
            start = datetime.strptime(element["datetime"].strip(), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
        if not _is_current(start.date()):
            continue
        seen.add(href)
        # title attribute reads "10.08.26 - Metal Monday"
        title = (anchor.get("title") or "").split(" - ", 1)[-1].strip()
        rows.append(
            _row(
                "werkk",
                url=f"https://werkk-baden.ch{href}" if href.startswith("/") else href,
                title=title,
                start_date=_fmt_date(start),
                start_time=_fmt_time(start),
                source_id=_source_id("werkk", start.date(), title),
            )
        )
    return rows


def fetch_quaidubas() -> list[dict[str, str]]:
    """QuaiDuBas30, Biel/Bienne — Hugo listing of li.event-card.

    The German locale is requested because the venue is in bilingual Biel and
    the tracker's base language is German; the events themselves are the same
    in every locale.
    """
    soup = _soup("https://quaidubas30.ch/de/events/")
    if not soup:
        return []
    rows = []
    for card in soup.select("li.event-card"):
        time_tag = card.find("time")
        anchor = card.find("a")
        if not time_tag or not time_tag.get("datetime"):
            continue
        try:
            start = datetime.strptime(time_tag["datetime"].strip(), "%Y-%m-%d")
        except ValueError:
            continue
        if not _is_current(start.date()):
            continue
        title_tag = card.select_one(".event-card-title, h2, h3")
        title = html_to_text(title_tag.get_text(" ", strip=True)) if title_tag else ""
        body = card.select_one(".event-card-body")
        clock = ""
        if body:
            match = re.search(r"\b(\d{1,2}:\d{2})\b", body.get_text(" ", strip=True))
            clock = match.group(1) if match else ""
        rows.append(
            _row(
                "quaidubas",
                url=anchor.get("href") if anchor else "",
                title=title,
                start_date=_fmt_date(start),
                start_time=_clean_time(clock),
                source_id=_source_id("quaidubas", start.date(), title),
            )
        )
    return rows


def fetch_cafete() -> list[dict[str, str]]:
    """Die Cafete, Bern — hand-written static HTML, div.event per show.

    Dates read "Do. 06. August 2026 — 23:30" with a German month name and an
    em-dash before the time.
    """
    soup = _soup("https://cafete.ch/")
    if not soup:
        return []
    rows = []
    for block in soup.select("div.event"):

        def part(name, block=block):
            found = block.select_one(f".{name}")
            return found.get_text(" ", strip=True) if found else ""

        raw_date = part("date")
        match = re.search(r"(\d{1,2})\.\s*([A-Za-zäöüÄÖÜ]+)\s*(\d{4})", raw_date)
        if not match:
            continue
        month = GERMAN_MONTHS.get(match.group(2).strip().lower())
        if not month:
            continue
        try:
            start = date(int(match.group(3)), month, int(match.group(1)))
        except ValueError:
            continue
        if not _is_current(start):
            continue
        title = part("title")
        clock = re.search(r"(\d{1,2}:\d{2})\s*$", raw_date)
        rows.append(
            _row(
                "cafete",
                title=title,
                performers=part("acts").replace("\n", ", "),
                styles=re.sub(r"^Style:\s*", "", part("style"), flags=re.IGNORECASE),
                description=part("description"),
                start_date=_fmt_date(start),
                start_time=_clean_time(clock.group(1)) if clock else "",
                source_id=_source_id("cafete", start, title),
            )
        )
    return rows


def fetch_kuzeb() -> list[dict[str, str]]:
    """KUZEB, Bremgarten — Bootstrap cards, full detail in a paired modal.

    Each card opens a modal carrying the title, the date (or date range) and
    the description, so one request covers the whole programme.
    """
    soup = _soup("https://www.kuzeb.ch/events")
    if not soup:
        return []
    rows = []
    for modal in soup.select("div.modal[data-event-id]"):
        text = modal.get_text("\n", strip=True)
        dates = re.findall(r"(\d{2}\.\d{2}\.\d{4})", text)
        if not dates:
            continue
        try:
            start = datetime.strptime(dates[0], "%d.%m.%Y")
        except ValueError:
            continue
        if not _is_current(start.date()):
            continue
        end = None
        if len(dates) > 1:
            try:
                end = datetime.strptime(dates[1], "%d.%m.%Y")
            except ValueError:
                end = None
        heading = modal.select_one(".modal-title, h1, h2, h3, h4")
        title = html_to_text(heading.get_text(" ", strip=True)) if heading else ""
        body = modal.select_one(".modal-body")
        description = html_to_text(body.get_text(" ", strip=True)) if body else ""
        clock = re.search(r"\b(\d{1,2}:\d{2})\b", text)
        rows.append(
            _row(
                "kuzeb",
                title=title,
                description=description,
                start_date=_fmt_date(start),
                end_date=_fmt_date(end) if end and end.date() > start.date() else "",
                start_time=_clean_time(clock.group(1)) if clock else "",
                source_id=str(modal.get("data-event-id") or "") or None,
            )
        )
    return rows


def fetch_nouveaumonde() -> list[dict[str, str]]:
    """Nouveau Monde, Fribourg — ProcessWire agenda of a.poster links.

    `data-toFilter` classifies each entry (crt concert, spt spectacle, fte
    fête…); everything that isn't a concert or a party is dropped, since the
    tracker is a concert calendar. Dates are DD.MM.YYYY but can also be the
    words for today/tomorrow.
    """
    soup = _soup("https://www.nouveaumonde.ch/agenda/")
    if not soup:
        return []
    today = _today()
    relative = {
        "aujourd'hui": today,
        "aujourdhui": today,
        "heute": today,
        "demain": today + timedelta(days=1),
        "morgen": today + timedelta(days=1),
    }
    rows = []
    for anchor in soup.select("a.poster"):
        kinds = (anchor.get("data-tofilter") or "").lower()
        if not re.search(r"concert|fete|fête", kinds):
            continue
        date_tag = anchor.select_one("h3.date")
        if not date_tag:
            continue
        raw = date_tag.get_text(strip=True)
        start = relative.get(raw.strip().lower())
        if start is None:
            try:
                start = datetime.strptime(raw.strip(), "%d.%m.%Y").date()
            except ValueError:
                continue
        if not _is_current(start):
            continue
        title_tag = anchor.select_one("h4:not(.dateHM)")
        title = html_to_text(title_tag.get_text(" ", strip=True)) if title_tag else ""
        clock_tag = anchor.select_one("h4.dateHM")
        href = anchor.get("href") or ""
        rows.append(
            _row(
                "nouveaumonde",
                url=f"https://www.nouveaumonde.ch{href}"
                if href.startswith("/")
                else href,
                title=title,
                performers=title,
                start_date=_fmt_date(start),
                start_time=_clean_time(
                    clock_tag.get_text(strip=True) if clock_tag else ""
                ),
                source_id=_source_id("nouveaumonde", start, title),
            )
        )
    return rows


def fetch_kaschemme() -> list[dict[str, str]]:
    """Kaschemme, Basel — Squarespace event list, parsed from HTML.

    Squarespace also exposes ?format=json and ?format=ical for this page and
    both would be easier, but robots.txt disallows those query forms
    explicitly while leaving the page itself open — so the page it is.
    """
    soup = _soup("https://www.kaschemme.ch/programm")
    if not soup:
        return []
    rows = []
    for article in soup.select("article.eventlist-event"):
        time_tag = article.select_one("time.event-date")
        if not time_tag or not time_tag.get("datetime"):
            continue
        try:
            start = datetime.strptime(time_tag["datetime"].strip(), "%Y-%m-%d").date()
        except ValueError:
            continue
        if not _is_current(start):
            continue
        title_tag = article.select_one(".eventlist-title")
        title = html_to_text(title_tag.get_text(" ", strip=True)) if title_tag else ""
        link = article.select_one("a.eventlist-title-link") or article.find("a")
        href = link.get("href") if link else ""
        # 24hr only — the block also renders a 12-hour variant, and a
        # comma selector would match whichever comes first in the document,
        # turning "6:00 PM" into 06:00.
        start_tag = article.select_one(".event-time-24hr-start")
        excerpt = article.select_one(".eventlist-excerpt")
        rows.append(
            _row(
                "kaschemme",
                url=f"https://www.kaschemme.ch{href}" if href.startswith("/") else href,
                title=title,
                description=html_to_text(
                    excerpt.get_text(" ", strip=True) if excerpt else ""
                ),
                start_date=_fmt_date(start),
                start_time=_clean_time(
                    start_tag.get_text(strip=True) if start_tag else ""
                ),
                source_id=_source_id("kaschemme", start, title),
            )
        )
    return rows


def get_garedelion_event_urls() -> list[str]:
    """Discover Gare de Lion event pages via the WP REST `event` post type.

    The REST rows carry the slug and title but no date, doors or price — those
    only exist in the rendered page — so this is discovery only and
    parse_garedelion_event() does the real work per URL.
    """
    data = fetch_json("https://garedelion.ch/wp-json/wp/v2/event?per_page=50")
    if not isinstance(data, list):
        return []
    return [entry.get("link") for entry in data if entry.get("link")]


def parse_garedelion_event(url: str) -> dict[str, str] | None:
    """Parse one Gare de Lion event page.

    Date, doors, start, genre and price sit in a labelled `.detail-info` grid
    ("Türöffnung", "Start", "Datum", "Genre", "Abendkasse"), which is read by
    label rather than position so a reordered grid still parses.
    """
    soup = _soup(url)
    if not soup:
        return None
    labels = {}
    for element in soup.select(".detail-info .element"):
        label = element.select_one(".label")
        value = element.select_one(".value")
        if label and value:
            labels[label.get_text(strip=True).lower()] = value.get_text(strip=True)

    raw_date = labels.get("datum", "")
    match = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", raw_date)
    if not match:
        logger.debug(f"Gare de Lion: no date on {url}")
        return None
    try:
        start = date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    except ValueError:
        return None
    if not _is_current(start):
        return None
    heading = soup.find("h1")
    return _row(
        "garedelion",
        url=url,
        title=html_to_text(heading.get_text(" ", strip=True)) if heading else "",
        styles=labels.get("genre", ""),
        start_date=_fmt_date(start),
        doors_open=_clean_time(labels.get("türöffnung", "")),
        start_time=_clean_time(labels.get("start", "")),
        ticket_price=_price(labels.get("abendkasse", "")),
        ticket_currency="CHF" if labels.get("abendkasse") else "",
    )


# ---------------------------------------------------------------------------
# Fragile layouts, kept last on purpose. These two have no structure worth the
# name; they are expected to miss events and to break sooner than the rest.
# ---------------------------------------------------------------------------


def fetch_ruempeltum() -> list[dict[str, str]]:
    """Rümpeltum, St. Gallen — WPBakery layout with no per-event wrapper.

    Fragile by nature: events are page-builder rows, so this keys off the
    `.work-meta` title blocks and only keeps entries whose text contains a
    parseable date. Expect it to return little and to need revisiting; that is
    better than inventing structure the page does not have. robots.txt asks
    for crawl-delay 10, and this makes a single request.
    """
    soup = _soup("https://rumpeltum.ch/programm/")
    if not soup:
        return []
    rows = []
    for meta in soup.select(".work-meta"):
        text = meta.get_text(" ", strip=True)
        match = re.search(
            r"(\d{1,2})\.\s*(\d{1,2})\.\s*(\d{4})|(\d{1,2})\.\s*([A-Za-zäöü]+)", text
        )
        if not match:
            continue
        try:
            if match.group(3):
                start = date(
                    int(match.group(3)), int(match.group(2)), int(match.group(1))
                )
            else:
                month = GERMAN_MONTHS.get((match.group(5) or "").lower())
                if not month:
                    continue
                start = date(_today().year, month, int(match.group(4)))
                if start < _today() - _PAST_GRACE:
                    start = date(start.year + 1, month, int(match.group(4)))
        except ValueError:
            continue
        if not _is_current(start):
            continue
        title_tag = meta.select_one(".title, h4")
        title = html_to_text(title_tag.get_text(" ", strip=True)) if title_tag else ""
        if not title:
            continue
        rows.append(
            _row(
                "ruempeltum",
                title=title,
                description=html_to_text(text),
                start_date=_fmt_date(start),
                source_id=_source_id("ruempeltum", start, title),
            )
        )
    return rows


def get_safaribar_event_urls() -> list[str]:
    """Discover Safari Bar event pages from the homepage slider.

    The slider opens each event in a modal, so the real link lives in
    `data-targeturl` rather than href (href is "#").
    """
    soup = _soup("https://www.safaribar.ch/")
    if not soup:
        return []
    urls = []
    for anchor in soup.select("a[data-targeturl]"):
        target = (anchor.get("data-targeturl") or "").strip()
        if target.startswith("http") and target not in urls:
            urls.append(target)
    return urls


def parse_safaribar_event(url: str) -> dict[str, str] | None:
    """Parse one Safari Bar event page.

    No structured data anywhere on these pages, so this reads the first
    DD.MM.YYYY it finds in the content and the page's h1 as the title. Returns
    None when there is no date, which is the common case for the non-event
    pages the slider also links to.
    """
    soup = _soup(url)
    if not soup:
        return None
    text = soup.get_text(" ", strip=True)
    match = re.search(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", text)
    if not match:
        return None
    try:
        start = date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
    except ValueError:
        return None
    if not _is_current(start):
        return None
    heading = soup.find("h1")
    title = html_to_text(heading.get_text(" ", strip=True)) if heading else ""
    if not title:
        return None
    clock = re.search(r"\b(\d{1,2}:\d{2})\b", text)
    return _row(
        "safaribar",
        url=url,
        title=title,
        start_date=_fmt_date(start),
        start_time=_clean_time(clock.group(1)) if clock else "",
    )


def _dry_run() -> int:
    """Fetch every registered venue and print what came back, writing nothing.

    The check that matters before trusting any of this:
    ``uv run python -m diytracker.services.venue_parsers``. A parser that
    silently returns zero rows is indistinguishable from a venue with nothing
    booked, so the empty sources are called out at the end for eyeballing
    against the site.
    """
    from diytracker.services.scrape_events import configure_logging
    from diytracker.services.venue_sources import get_sources

    configure_logging()
    empty = []
    total = 0
    for source in get_sources():
        try:
            if source.is_listing:
                rows = source.fetcher() or []
            else:
                rows = [
                    row
                    for row in (source.parser(url) for url in source.discover() or [])
                    if row
                ]
        except Exception as exc:  # noqa: BLE001 - a dry run reports, never dies
            print(f"\n{source.key}: FAILED — {exc!r}")
            empty.append(source.key)
            continue
        total += len(rows)
        print(f"\n{source.key} ({source.venue.name.strip()}): {len(rows)} events")
        if not rows:
            empty.append(source.key)
        for row in rows:
            print(
                f"  {row['start_date']}  {row['start_time'] or '--:--'}  "
                f"{row['title'][:60]}"
            )
    print(f"\n{total} events from {len(get_sources())} sources")
    if empty:
        print(f"no events from: {', '.join(empty)} — check these against the site")
    return 0


if __name__ == "__main__":
    raise SystemExit(_dry_run())
