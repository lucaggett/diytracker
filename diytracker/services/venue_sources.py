"""The registry of venue sources the scraper walks.

Adding a venue means adding one ``VenueSource`` here and one fetcher in
``venue_parsers.py``; ``scraper.py`` iterates this list and needs no change.
That replaces the two hardcoded list comprehensions that used to enumerate
metalgigs and petzi inline, which did not survive going from two sources to
nineteen.

Each entry also records *why* it is shaped the way it is — which feed was used
or rejected, what robots.txt says, and where the contact address came from —
because that context is what you need when a parser breaks in six months, and
it is otherwise only in a commit message.

``VenueIdentity`` values are copied from the venue rows already in the
database, deliberately including their existing oddities (trailing spaces on
``"Alte Post "`` and ``"Kaschemme "``, the space in ``"Biel / Bienne"``). Ingest
matches venues by name and PLZ, so "improving" these strings here would create
a second venue next to the real one.
"""

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from diytracker.services import venue_parsers as vp


@dataclass(frozen=True)
class VenueIdentity:
    """The venue as it already exists in the database."""

    name: str
    address: str
    city: str
    plz: str
    canton: str

    def as_fields(self) -> Dict[str, str]:
        return {
            "venue_name": self.name,
            "street_address": self.address,
            "city": self.city,
            "postal_code": self.plz,
            "region": self.canton,
        }


@dataclass(frozen=True)
class VenueSource:
    """One venue's programme and how to read it.

    Exactly one of ``fetcher`` or ``discover``/``parser`` is set:

    * ``fetcher()`` returns every row from a single request. This is the
      common case — a feed or a listing page that carries the whole programme.
    * ``discover()`` returns event URLs and ``parser(url)`` turns one into a
      row. Only for venues whose listing withholds the date, where a second
      request per event is unavoidable.
    """

    key: str
    venue: VenueIdentity
    lang: str
    contact_email: Optional[str] = None
    fetcher: Optional[Callable[[], List[dict]]] = None
    discover: Optional[Callable[[], List[str]]] = None
    parser: Optional[Callable[[str], Optional[dict]]] = None
    notes: str = ""

    @property
    def is_listing(self) -> bool:
        return self.fetcher is not None


# --- Venue identities, verbatim from the `venue` table ----------------------

_ALTEPOST = VenueIdentity(
    "Alte Post ", "Schaffhauserstrasse 510", "Zürich", "8000", "ZH"
)
_POSTSQUAT = VenueIdentity("Post Squat", "Wipkingerplatz 7", "Zürich", "8000", "ZH")
_HORSTKLUB = VenueIdentity("Horstklub", "Kirchstrasse 1", "Kreuzlingen", "8280", "TG")
_TREPPENHAUS = VenueIdentity(
    "Café Bar Treppenhaus", "Kirchstrasse 3", "Rorschach", "9400", "SG"
)
_ELDORADO = VenueIdentity(
    "Eldorado Biel", "Mattenstrasse 28", "Biel / Bienne", "2503", "BE"
)
_TAPTAB = VenueIdentity("TapTab", "Baumgartenstrasse 19", "Schaffhausen", "8200", "SH")
_PROVITREFF = VenueIdentity("Provitreff", "Sihlquai 240", "Zürich", "8005", "ZH")
_BADBONN = VenueIdentity("Bad Bonn", "Bonn 2", "Düdingen", "3186", "FR")
_WERKK = VenueIdentity("Werkk Kulturlokal", "Schmiedestrasse 1", "Baden", "5400", "AG")
_QUAIDUBAS = VenueIdentity(
    "QuaiDuBas30", "Unterer Quai 30", "Biel/Bienne", "2503", "BE"
)
_CAFETE = VenueIdentity("Die Cafete", "Neubrückstrasse 8", "Bern", "3012", "BE")
_KUZEB = VenueIdentity(
    "KUZEB - Kulturzentrum Bremgarten", "Zürcherstrasse 2", "Bremgarten", "5620", "AG"
)
_NOUVEAUMONDE = VenueIdentity(
    "Nouveau Monde", "Esplanade de l'Ancienne-Gare 3", "Fribourg", "1700", "FR"
)
_KASCHEMME = VenueIdentity("Kaschemme ", "Lehenmattstrasse 357", "Basel", "4052", "BS")
_GAREDELION = VenueIdentity("Gare De Lion", "Silostrasse 10 ", "Wil", "9500", "SG")
_RUEMPELTUM = VenueIdentity("Rümpeltum", "Bachstrasse 36", "St. Gallen", "9008", "SG")
_SAFARIBAR = VenueIdentity("Safari Bar", "Zähringerstrasse 29", "Zürich", "8001", "ZH")


VENUE_SOURCES: Tuple[VenueSource, ...] = (
    # --- feed-backed ------------------------------------------------------
    VenueSource(
        key="horstklub",
        venue=_HORSTKLUB,
        lang="de",
        contact_email=None,
        fetcher=vp.fetch_horstklub,
        notes=(
            "events.csv is what the site's own JS loads to render the "
            "programme, so one request replaces the whole page. No general "
            "contact address is published — only a merch-orders one — so the "
            "permission mail has no obvious recipient."
        ),
    ),
    VenueSource(
        key="treppenhaus",
        venue=_TREPPENHAUS,
        lang="de",
        contact_email="info@treppenhaus.ch",
        fetcher=vp.fetch_treppenhaus,
        notes="Custom WP REST 'events' post type with date/time/price already split out.",
    ),
    VenueSource(
        key="eldorado",
        venue=_ELDORADO,
        lang="de",
        contact_email="eldorado-bielbienne@gmx.ch",
        fetcher=vp.fetch_eldorado,
        notes=(
            "The Events Calendar REST API. Biel is bilingual but the site is "
            "German-only, so the mail goes in German."
        ),
    ),
    VenueSource(
        key="taptab",
        venue=_TAPTAB,
        lang="de",
        contact_email="sekretariat@taptab.ch",
        fetcher=vp.fetch_taptab,
        notes="Joomla RSS at ?format=feed&type=rss; the plain /feed path 404s.",
    ),
    VenueSource(
        key="provitreff",
        venue=_PROVITREFF,
        lang="de",
        contact_email="info@provitreff.ch",
        fetcher=vp.fetch_provitreff,
        notes=(
            "Next.js __NEXT_DATA__ payload. Dates are hand-typed without a "
            "year, so expect this one to need the most attention."
        ),
    ),
    # --- listing pages ----------------------------------------------------
    VenueSource(
        key="altepost",
        venue=_ALTEPOST,
        lang="de",
        contact_email="altepostseebach@proton.me",
        fetcher=vp.fetch_altepost,
        notes=(
            "Events Manager listing. Its ?ical=1 export returns the oldest 50 "
            "events and ignores scope/limit, so the feed is a stale archive "
            "and the HTML is the only current source. The address is decoded "
            "from the site's obfuscated contact link — treat as unverified."
        ),
    ),
    VenueSource(
        key="postsquat",
        venue=_POSTSQUAT,
        lang="de",
        contact_email="postevents@systemli.org",
        fetcher=vp.fetch_postsquat,
        notes="Same plugin and same stale-iCal problem as Alte Post, different theme.",
    ),
    VenueSource(
        key="badbonn",
        venue=_BADBONN,
        lang="de",
        contact_email="info@badbonn.ch",
        fetcher=vp.fetch_badbonn,
        notes=(
            "Listing anchors carry data-title/date/time/price, so no event "
            "page is ever fetched. Site is bilingual DE/FR."
        ),
    ),
    VenueSource(
        key="werkk",
        venue=_WERKK,
        lang="de",
        contact_email="love@werkk-baden.ch",
        fetcher=vp.fetch_werkk,
        notes="REDAXO agenda; each event renders twice (mobile+desktop), deduped on href.",
    ),
    VenueSource(
        key="quaidubas",
        venue=_QUAIDUBAS,
        lang="fr",
        contact_email="contact@quaidubas30.ch",
        fetcher=vp.fetch_quaidubas,
        notes=(
            "Hugo site, DE/FR/EN mirrored; the German locale is read since "
            "the events are identical. The collective writes primarily in "
            "French, so the mail goes in French."
        ),
    ),
    VenueSource(
        key="cafete",
        venue=_CAFETE,
        lang="de",
        contact_email="cafete@cafete.ch",
        fetcher=vp.fetch_cafete,
        notes="Hand-written static HTML, one div.event per show.",
    ),
    VenueSource(
        key="kuzeb",
        venue=_KUZEB,
        lang="de",
        contact_email="office@kuzeb.ch",
        fetcher=vp.fetch_kuzeb,
        notes="Bootstrap cards; the paired modal holds the date range and description.",
    ),
    VenueSource(
        key="nouveaumonde",
        venue=_NOUVEAUMONDE,
        lang="fr",
        contact_email="info@nouveaumonde.ch",
        fetcher=vp.fetch_nouveaumonde,
        notes=(
            "ProcessWire agenda; data-toFilter separates concerts and parties "
            "from theatre and workshops, which are dropped."
        ),
    ),
    VenueSource(
        key="kaschemme",
        venue=_KASCHEMME,
        lang="de",
        contact_email="kontakt@kaschemme.ch",
        fetcher=vp.fetch_kaschemme,
        notes=(
            "Squarespace. Its ?format=json and ?format=ical endpoints work and "
            "would be easier, but robots.txt disallows exactly those query "
            "forms while leaving the page open, so the page is what we read."
        ),
    ),
    # --- discovery + per-event parse --------------------------------------
    VenueSource(
        key="garedelion",
        venue=_GAREDELION,
        lang="de",
        contact_email="promo@garedelion.ch",
        discover=vp.get_garedelion_event_urls,
        parser=vp.parse_garedelion_event,
        notes=(
            "WP REST lists slugs but carries no date (ACF comes back empty), "
            "so the date/doors/price grid on each event page has to be read."
        ),
    ),
    VenueSource(
        key="safaribar",
        venue=_SAFARIBAR,
        lang="de",
        contact_email="info@safaribar.ch",
        discover=vp.get_safaribar_event_urls,
        parser=vp.parse_safaribar_event,
        notes=(
            "Fragile: a modal slider with the real link in data-targeturl and "
            "no structured data on the event pages. Expect misses."
        ),
    ),
    VenueSource(
        key="ruempeltum",
        venue=_RUEMPELTUM,
        lang="de",
        contact_email="ruempeltum@mail.ch",
        fetcher=vp.fetch_ruempeltum,
        notes=(
            "Fragile: WPBakery page-builder rows with no per-event wrapper, so "
            "only entries carrying a parseable date are kept. robots.txt asks "
            "for crawl-delay 10 and this makes one request."
        ),
    ),
)


# Venues that were considered and deliberately have no scraper. Kept in code
# rather than only in a commit message so the next person does not spend an
# afternoon rediscovering why.
UNSCRAPED: Dict[str, str] = {
    "Photobastei": (
        "robots.txt is 'User-agent: * / Disallow: /' for the whole site. "
        "fetch_url() refuses it, so any parser would be dead code. Written "
        "permission is the only thing that changes this."
    ),
    "Picadilly Brugg": "Programme is posted as flyer images only; the WP RSS feed is empty.",
    "Moshpit Club": "The /programm widgets are empty; shows are announced on Facebook.",
    "Grüntal": "No website — Facebook only.",
    "Bahnhöfli Biel": "No website — Instagram/Facebook only.",
    "Toms Beer Box": "No website (toms.ch, cited in listings, is the TOMS shoe shop).",
    "Château d'Erguël": (
        "No venue site; a municipal tourism page. Shows there are the "
        "Toxoplasmose festival, which is its own organiser."
    ),
    "Röonda": "Could not be identified at all — the venue name needs checking first.",
}


def get_sources() -> Tuple[VenueSource, ...]:
    return VENUE_SOURCES


def sources_by_key() -> Dict[str, VenueSource]:
    return {source.key: source for source in VENUE_SOURCES}
