"""Shared utilities for diytracker."""

import re

from markupsafe import Markup, escape

# ---------------------------------------------------------------------------
# Canton normalisation
# ---------------------------------------------------------------------------

_VALID_CODES = {
    "AG",
    "AI",
    "AR",
    "BE",
    "BL",
    "BS",
    "FR",
    "GE",
    "GL",
    "GR",
    "JU",
    "LU",
    "NE",
    "NW",
    "OW",
    "SG",
    "SH",
    "SO",
    "SZ",
    "TG",
    "TI",
    "UR",
    "VD",
    "VS",
    "ZG",
    "ZH",
}


def N_(s):
    """Babel extraction marker (in Babel's DEFAULT_KEYWORDS); identity at runtime."""
    return s


# Code → canonical display name, in the canton's locally dominant language
# (bilingual cantons use the majority language: BE→Bern, FR→Fribourg,
# VS→Valais, GR→Graubünden). Canonical names drive the /<canton-slug>/
# landing-page URLs via slugify(); display is localized through gettext.
CANTONS = {
    "AG": N_("Aargau"),
    "AI": N_("Appenzell Innerrhoden"),
    "AR": N_("Appenzell Ausserrhoden"),
    "BE": N_("Bern"),
    "BL": N_("Basel-Landschaft"),
    "BS": N_("Basel-Stadt"),
    "FR": N_("Fribourg"),
    "GE": N_("Genève"),
    "GL": N_("Glarus"),
    "GR": N_("Graubünden"),
    "JU": N_("Jura"),
    "LU": N_("Luzern"),
    "NE": N_("Neuchâtel"),
    "NW": N_("Nidwalden"),
    "OW": N_("Obwalden"),
    "SG": N_("St. Gallen"),
    "SH": N_("Schaffhausen"),
    "SO": N_("Solothurn"),
    "SZ": N_("Schwyz"),
    "TG": N_("Thurgau"),
    "TI": N_("Ticino"),
    "UR": N_("Uri"),
    "VD": N_("Vaud"),
    "VS": N_("Valais"),
    "ZG": N_("Zug"),
    "ZH": N_("Zürich"),
}

# Code → locale → the complete "in <canton>" phrase, preposition included.
# A single hardcoded preposition can't work: German contracts to "im" for the
# masculine cantons and takes "in der" for Waadt, French switches between "à"
# (city cantons), "en", "au" and "dans le(s)", and Italian between "a", "in"
# and "nel(i)". Consumed by services.i18n.canton_in(); the landing-page strings
# interpolate the whole phrase as %(in_canton)s.
CANTON_LOCATIVE = {
    "AG": {
        "de": "im Aargau",
        "fr": "en Argovie",
        "it": "in Argovia",
        "en": "in Aargau",
    },
    "AI": {
        "de": "in Appenzell Innerrhoden",
        "fr": "en Appenzell Rhodes-Intérieures",
        "it": "in Appenzello Interno",
        "en": "in Appenzell Innerrhoden",
    },
    "AR": {
        "de": "in Appenzell Ausserrhoden",
        "fr": "en Appenzell Rhodes-Extérieures",
        "it": "in Appenzello Esterno",
        "en": "in Appenzell Ausserrhoden",
    },
    "BE": {"de": "in Bern", "fr": "à Berne", "it": "a Berna", "en": "in Bern"},
    "BL": {
        "de": "in Basel-Landschaft",
        "fr": "à Bâle-Campagne",
        "it": "a Basilea Campagna",
        "en": "in Basel-Landschaft",
    },
    "BS": {
        "de": "in Basel-Stadt",
        "fr": "à Bâle-Ville",
        "it": "a Basilea Città",
        "en": "in Basel-Stadt",
    },
    "FR": {
        "de": "in Freiburg",
        "fr": "à Fribourg",
        "it": "a Friburgo",
        "en": "in Fribourg",
    },
    "GE": {"de": "in Genf", "fr": "à Genève", "it": "a Ginevra", "en": "in Genève"},
    "GL": {"de": "in Glarus", "fr": "à Glaris", "it": "a Glarona", "en": "in Glarus"},
    "GR": {
        "de": "in Graubünden",
        "fr": "dans les Grisons",
        "it": "nei Grigioni",
        "en": "in Graubünden",
    },
    "JU": {"de": "im Jura", "fr": "dans le Jura", "it": "nel Giura", "en": "in Jura"},
    "LU": {"de": "in Luzern", "fr": "à Lucerne", "it": "a Lucerna", "en": "in Luzern"},
    "NE": {
        "de": "in Neuenburg",
        "fr": "à Neuchâtel",
        "it": "a Neuchâtel",
        "en": "in Neuchâtel",
    },
    "NW": {
        "de": "in Nidwalden",
        "fr": "à Nidwald",
        "it": "a Nidvaldo",
        "en": "in Nidwalden",
    },
    "OW": {
        "de": "in Obwalden",
        "fr": "à Obwald",
        "it": "a Obvaldo",
        "en": "in Obwalden",
    },
    "SG": {
        "de": "in St. Gallen",
        "fr": "à Saint-Gall",
        "it": "a San Gallo",
        "en": "in St. Gallen",
    },
    "SH": {
        "de": "in Schaffhausen",
        "fr": "à Schaffhouse",
        "it": "a Sciaffusa",
        "en": "in Schaffhausen",
    },
    "SO": {
        "de": "in Solothurn",
        "fr": "à Soleure",
        "it": "a Soletta",
        "en": "in Solothurn",
    },
    "SZ": {"de": "in Schwyz", "fr": "à Schwytz", "it": "a Svitto", "en": "in Schwyz"},
    "TG": {
        "de": "im Thurgau",
        "fr": "en Thurgovie",
        "it": "in Turgovia",
        "en": "in Thurgau",
    },
    "TI": {"de": "im Tessin", "fr": "au Tessin", "it": "in Ticino", "en": "in Ticino"},
    "UR": {"de": "in Uri", "fr": "à Uri", "it": "a Uri", "en": "in Uri"},
    "VD": {
        "de": "in der Waadt",
        "fr": "dans le canton de Vaud",
        "it": "nel Canton Vaud",
        "en": "in Vaud",
    },
    "VS": {"de": "im Wallis", "fr": "en Valais", "it": "in Vallese", "en": "in Valais"},
    "ZG": {"de": "in Zug", "fr": "à Zoug", "it": "a Zugo", "en": "in Zug"},
    "ZH": {"de": "in Zürich", "fr": "à Zurich", "it": "a Zurigo", "en": "in Zürich"},
}

# Canonical CANTONS name → code, for callers that only carry the display name.
CANTON_CODE_BY_NAME = {name: code for code, name in CANTONS.items()}

# Maps lowercase full/alternative canton names → 2-letter code.
# Covers German, French, and Italian names plus common variants.
_CANTON_NAME_TO_CODE = {
    # German
    "aargau": "AG",
    "appenzell ausserrhoden": "AR",
    "appenzell a.rh.": "AR",
    "appenzell a.-rh.": "AR",
    "appenzell innerrhoden": "AI",
    "appenzell i.rh.": "AI",
    "appenzell i.-rh.": "AI",
    "basel-landschaft": "BL",
    "basel landschaft": "BL",
    "baselland": "BL",
    "basel-stadt": "BS",
    "basel stadt": "BS",
    "bern": "BE",
    "freiburg": "FR",
    "genf": "GE",
    "glarus": "GL",
    "graubünden": "GR",
    "graubunden": "GR",
    "grisons": "GR",
    "jura": "JU",
    "luzern": "LU",
    "nidwalden": "NW",
    "obwalden": "OW",
    "schaffhausen": "SH",
    "schwyz": "SZ",
    "solothurn": "SO",
    "st. gallen": "SG",
    "st gallen": "SG",
    "saint-gallen": "SG",
    "thurgau": "TG",
    "tessin": "TI",
    "uri": "UR",
    "waadt": "VD",
    "wallis": "VS",
    "zug": "ZG",
    "zürich": "ZH",
    "zurich": "ZH",
    # French
    "argovie": "AG",
    "appenzell rhodes-extérieures": "AR",
    "appenzell rhodes-intérieures": "AI",
    "bâle-campagne": "BL",
    "bale-campagne": "BL",
    "bâle-ville": "BS",
    "bale-ville": "BS",
    "berne": "BE",
    "fribourg": "FR",
    "genève": "GE",
    "geneve": "GE",
    "glaris": "GL",
    "lucerne": "LU",
    "neuchâtel": "NE",
    "neuchatel": "NE",
    "nidwald": "NW",
    "obwald": "OW",
    "saint-gall": "SG",
    "schaffhouse": "SH",
    "soleure": "SO",
    "thurgovie": "TG",
    "ticino": "TI",
    "vaud": "VD",
    "valais": "VS",
    "zoug": "ZG",
    # Italian
    "argovia": "AG",
    "appenzello esterno": "AR",
    "appenzello interno": "AI",
    "basilea campagna": "BL",
    "basilea città": "BS",
    "berna": "BE",
    "friburgo": "FR",
    "ginevra": "GE",
    "glarona": "GL",
    "grigioni": "GR",
    "gura": "JU",
    "lucerna": "LU",
    "nidvaldo": "NW",
    "obvaldo": "OW",
    "sciaffusa": "SH",
    "svitto": "SZ",
    "soletta": "SO",
    "san gallo": "SG",
    "turgovia": "TG",
    "valdo": "VD",
    "vallese": "VS",
    "zugo": "ZG",
}

# Maps lowercase city name → canton code for the most common Swiss cities.
# Used as a last-resort fallback when the region field is empty.
_CITY_TO_CANTON = {
    "aarau": "AG",
    "altdorf": "UR",
    "appenzell": "AI",
    "arbon": "TG",
    "arlesheim": "BL",
    "arth": "SZ",
    "arth-goldau": "SZ",
    "arth goldau": "SZ",
    "arth - goldau": "SZ",
    "baar": "ZG",
    "basel": "BS",
    "bellinzona": "TI",
    "bern": "BE",
    "berne": "BE",
    "bettlach": "SO",
    "biel": "BE",
    "bienne": "BE",
    "brig": "VS",
    "brugg": "AG",
    "buchs": "SG",
    "bulle": "FR",
    "burgdorf": "BE",
    "chur": "GR",
    "davos": "GR",
    "delémont": "JU",
    "delemont": "JU",
    "diessenhofen": "TG",
    "dietikon": "ZH",
    "dübendorf": "ZH",
    "dubendorf": "ZH",
    "emmen": "LU",
    "flawil": "SG",
    "frauenfeld": "TG",
    "freiburg": "FR",
    "fribourg": "FR",
    "frenkendorf": "BL",
    "genève": "GE",
    "geneve": "GE",
    "geneva": "GE",
    "genf": "GE",
    "glarus": "GL",
    "gossau": "SG",
    "grenchen": "SO",
    "herisau": "AR",
    "horgen": "ZH",
    "horw": "LU",
    "illnau": "ZH",
    "illnau-effretikon": "ZH",
    "ittigen": "BE",
    "kloten": "ZH",
    "kreuzlingen": "TG",
    "köniz": "BE",
    "koniz": "BE",
    "kriens": "LU",
    "küsnacht": "ZH",
    "küssnacht": "SZ",
    "kussnacht": "SZ",
    "la chaux-de-fonds": "NE",
    "langenthal": "BE",
    "lancy": "GE",
    "lausanne": "VD",
    "lenzburg": "AG",
    "liestal": "BL",
    "locarno": "TI",
    "lugano": "TI",
    "luzern": "LU",
    "lucerne": "LU",
    "männedorf": "ZH",
    "mannedorf": "ZH",
    "martigny": "VS",
    "meyrin": "GE",
    "monthey": "VS",
    "morges": "VD",
    "münchenbuchsee": "BE",
    "münsingen": "BE",
    "naters": "VS",
    "neftenbach": "ZH",
    "neuchâtel": "NE",
    "neuchatel": "NE",
    "niederurnen": "GL",
    "nyon": "VD",
    "oberwil": "BL",
    "oftringen": "AG",
    "olten": "SO",
    "onex": "GE",
    "opfikon": "ZH",
    "pfäffikon": "SZ",
    "pfaffikon": "SZ",
    "pratteln": "BL",
    "rapperswil": "SG",
    "rapperswil-jona": "SG",
    "regensdorf": "ZH",
    "reinach": "BL",
    "rheinfelden": "AG",
    "riehen": "BS",
    "romanshorn": "TG",
    "rorschach": "SG",
    "rüti": "ZH",
    "ruti": "ZH",
    "sarnen": "OW",
    "schaffhausen": "SH",
    "schlieren": "ZH",
    "schwyz": "SZ",
    "sierre": "VS",
    "sion": "VS",
    "sitten": "VS",
    "solothurn": "SO",
    "spiez": "BE",
    "spreitenbach": "AG",
    "stans": "NW",
    "st. gallen": "SG",
    "st gallen": "SG",
    "st.gallen": "SG",
    "thun": "BE",
    "thalwil": "ZH",
    "uster": "ZH",
    "visp": "VS",
    "volketswil": "ZH",
    "volketswill": "ZH",
    "wädenswil": "ZH",
    "wadenswil": "ZH",
    "weinfelden": "TG",
    "wettingen": "AG",
    "wil": "SG",
    "winterthur": "ZH",
    "wollerau": "SZ",
    "wünnewil": "FR",
    "yverdon-les-bains": "VD",
    "yverdon": "VD",
    "zollikon": "ZH",
    "zug": "ZG",
    "zürich": "ZH",
    "zurich": "ZH",
    "zwingen": "BL",
}


def normalise_canton(value: str) -> str:
    """Normalise a canton value to its 2-letter ISO 3166-2:CH code.

    Accepts:
    - already-correct 2-letter codes (any case)  → uppercased
    - full canton names in German, French, or Italian → code
    - leading/trailing whitespace                 → stripped first

    Returns the original value unchanged if no match is found.
    """
    if not value:
        return value
    stripped = value.strip()
    upper = stripped.upper()
    if upper in _VALID_CODES:
        return upper
    lower = stripped.lower()
    return _CANTON_NAME_TO_CODE.get(lower, stripped)


_LEADING_PLZ = re.compile(r"^(\d{4})\s+(.*)$")


def split_leading_plz(value: str) -> tuple:
    """Split a leading Swiss postal code off a city string.

    ``"8005 Zürich"`` → ``("8005", "Zürich")``; no leading PLZ →
    ``("", value)``. Shared by canton inference, venue dedup and the
    scrapers so the PLZ convention lives in one place.
    """
    match = _LEADING_PLZ.match((value or "").strip())
    if match:
        return match.group(1), match.group(2)
    return "", (value or "").strip()


def infer_canton_from_city(city: str) -> str:
    """Attempt to infer a canton code from a city name.

    The city string may be prefixed with a Swiss postal code
    (e.g. ``"8005 Zürich"``); this is stripped before lookup.

    Returns an empty string if no match can be found.
    """
    if not city:
        return ""
    _plz, city_clean = split_leading_plz(city)
    return _CITY_TO_CANTON.get(city_clean.lower(), "")


def resolve_canton(region: str, city: str) -> str:
    """Return the best-effort 2-letter canton code.

    First tries to normalise the *region* field directly.  If that
    produces nothing useful (empty or unchanged non-code value), falls
    back to inferring from the *city* name.
    """
    candidate = normalise_canton(region or "")
    if candidate and candidate.upper() in _VALID_CODES:
        return candidate
    return infer_canton_from_city(city or "") or candidate


# ---------------------------------------------------------------------------
# Genre taxonomy
# ---------------------------------------------------------------------------

# Event-format descriptors that appear in petzi's style field but are not
# musical genres. Stripped before mapping to a parent genre.
_GENRE_NOISE = {
    "concert",
    "konzert",
    "live",
    "club",
    "festival",
    "party",
    "workshop",
    "performance",
    "theatre",
    "theater",
    "movie",
    "outdoor",
    "free",
    "covers",
    "unbekannt",
    "unknown",
}

# Parent genres in the order they should appear in UI listings.
PARENT_GENRES_ORDER = [
    "Metal",
    "Hardcore",
    "Punk",
    "Rock",
    "Goth/Industrial",
    "Electronic",
    "Hip-Hop",
    "Folk",
    "Jazz/Blues",
    "Reggae/Ska",
    "Pop",
    "Other",
]

# Sub-genre → parent genre. Keys are lowercased.
_GENRE_PARENT_MAP = {
    # Metal
    "metal": "Metal",
    "heavy metal": "Metal",
    "black metal": "Metal",
    "melodic black metal": "Metal",
    "death metal": "Metal",
    "melodic death metal": "Metal",
    "brutal death metal": "Metal",
    "doom metal": "Metal",
    "doom": "Metal",
    "thrash metal": "Metal",
    "speed metal": "Metal",
    "power metal": "Metal",
    "folk metal": "Metal",
    "viking metal": "Metal",
    "pagan metal": "Metal",
    "gothic metal": "Metal",
    "melodic metal": "Metal",
    "progressive metal": "Metal",
    "alternative metal": "Metal",
    "nu metal": "Metal",
    "extreme metal": "Metal",
    "dark metal": "Metal",
    "post metal": "Metal",
    "post-metal": "Metal",
    "sludge": "Metal",
    "drone": "Metal",
    "djent": "Metal",
    # Hardcore
    "hardcore": "Hardcore",
    "metalcore": "Hardcore",
    "deathcore": "Hardcore",
    "grindcore": "Hardcore",
    "goregrind": "Hardcore",
    "post hardcore": "Hardcore",
    "post-hardcore": "Hardcore",
    "beatdown": "Hardcore",
    "crossover": "Hardcore",
    "emo": "Hardcore",
    "screamo": "Hardcore",
    "powerviolence": "Hardcore",
    "mathcore": "Hardcore",
    # Punk
    "punk": "Punk",
    "punk rock": "Punk",
    "crustpunk": "Punk",
    "crust punk": "Punk",
    "crust": "Punk",
    "post-punk": "Punk",
    "post punk": "Punk",
    # Rock
    "rock": "Rock",
    "hard rock": "Rock",
    "rock'n'roll": "Rock",
    "rock n roll": "Rock",
    "rock and roll": "Rock",
    "alternative": "Rock",
    "indie": "Rock",
    "grunge": "Rock",
    "stoner rock": "Rock",
    "stoner": "Rock",
    "garage": "Rock",
    "psychedelic": "Rock",
    "experimental": "Rock",
    "shoegaze": "Rock",
    # Goth / Industrial
    "goth": "Goth/Industrial",
    "gothic": "Goth/Industrial",
    "industrial": "Goth/Industrial",
    "ebm": "Goth/Industrial",
    "darkwave": "Goth/Industrial",
    "new wave": "Goth/Industrial",
    "ndw": "Goth/Industrial",
    "wave": "Goth/Industrial",
    "neofolk": "Goth/Industrial",
    "synthpop": "Goth/Industrial",
    # Electronic
    "electro": "Electronic",
    "electronic": "Electronic",
    "techno": "Electronic",
    "house": "Electronic",
    "trance": "Electronic",
    "drum & bass": "Electronic",
    "drum and bass": "Electronic",
    "dnb": "Electronic",
    "dubstep": "Electronic",
    "synth": "Electronic",
    "ambient": "Electronic",
    "noise": "Electronic",
    "hyperpop": "Electronic",
    "gabber": "Electronic",
    "psycore": "Electronic",
    # Hip-Hop
    "hip-hop": "Hip-Hop",
    "hip hop": "Hip-Hop",
    "hiphop": "Hip-Hop",
    "rap": "Hip-Hop",
    "urban": "Hip-Hop",
    # Folk
    "folk": "Folk",
    "songwriting": "Folk",
    "songwriter": "Folk",
    # Jazz / Blues
    "jazz": "Jazz/Blues",
    "blues": "Jazz/Blues",
    "soul": "Jazz/Blues",
    # Reggae / Ska
    "reggae": "Reggae/Ska",
    "dub": "Reggae/Ska",
    "dancehall": "Reggae/Ska",
    "ska": "Reggae/Ska",
    # Pop
    "pop": "Pop",
}


def _split_genre_tokens(raw):
    if not raw:
        return []
    return [p.strip() for p in raw.replace("·", ",").split(",") if p.strip()]


def clean_genre_tokens(raw):
    """Return the sub-genre tokens from *raw* with noise descriptors removed."""
    return [t for t in _split_genre_tokens(raw) if t.lower() not in _GENRE_NOISE]


def parent_genres(raw):
    """Map a comma-separated genre string to a de-duplicated list of parents.

    Noise descriptors (concert, club, festival, …) are dropped. Unknown
    tokens collapse to ``'Other'``. The result is ordered by
    :data:`PARENT_GENRES_ORDER`.
    """
    seen = set()
    for token in _split_genre_tokens(raw):
        low = token.lower()
        if low in _GENRE_NOISE:
            continue
        seen.add(_GENRE_PARENT_MAP.get(low, "Other"))
    return [p for p in PARENT_GENRES_ORDER if p in seen]


def parent_for_token(token):
    """Return the parent genre for a single sub-genre token (or 'Other')."""
    return _GENRE_PARENT_MAP.get((token or "").lower(), "Other")


# ---------------------------------------------------------------------------
# Ticket URL cleanup
# ---------------------------------------------------------------------------

# Generic "buy tickets" landing pages per source. Matched after stripping
# query/fragment/trailing slash and lowercasing.
_GENERIC_TICKET_URLS = {
    "metalgigs": {"https://metalgigs.ch/tickets/kaufen"},
}


def is_safe_link(url):
    """True if *url* is an absolute http(s) URL, i.e. safe to put in an href.

    Anything else — most importantly `javascript:` and `data:` — would
    execute in the visitor's browser when the link is clicked, so links are
    rendered only when this passes. Leading whitespace and mixed case are
    stripped first because browsers ignore both when resolving a scheme.
    """
    if not url:
        return False
    return url.strip().lower().startswith(("http://", "https://"))


_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)


def linkify(text):
    """Escape *text* and wrap plain http(s) URLs in anchors.

    The whole text is escaped first, then only substrings that pass
    is_safe_link() become links — the href is never built from anything
    that fails that check, so a `javascript:` string stays inert text.
    Returns Markup for direct template use.
    """
    if not text:
        return Markup("")
    result = []
    pos = 0
    for match in _URL_RE.finditer(text):
        result.append(escape(text[pos : match.start()]))
        url = match.group(0).rstrip(".,;:)")
        rest = match.group(0)[len(url) :]
        if is_safe_link(url):
            result.append(
                Markup(
                    '<a href="{0}" rel="nofollow noopener" target="_blank">{0}</a>'
                ).format(url)
            )
        else:
            result.append(escape(url))
        result.append(escape(rest))
        pos = match.end()
    result.append(escape(text[pos:]))
    return Markup("").join(result)


def clean_ticket_url(url, source):
    """Return None when *url* is unusable as a ticket link for *source*.

    Returns None for empty input, a non-http(s) URL (see is_safe_link) or a
    matched generic landing page; otherwise returns the stripped original.
    """
    if not url:
        return None
    trimmed = url.strip()
    if not trimmed:
        return None
    if not is_safe_link(trimmed):
        return None
    normalised = trimmed.split("?", 1)[0].split("#", 1)[0].rstrip("/").lower()
    generics = _GENERIC_TICKET_URLS.get((source or "").lower(), set())
    if normalised in generics:
        return None
    return trimmed
