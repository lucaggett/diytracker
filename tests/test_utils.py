"""Domain-logic unit tests for utils.py.

These cover the project's own business rules — Swiss canton resolution, the
genre taxonomy, and ticket-URL cleanup — not library behaviour.
"""

from diytracker.utils import (
    clean_genre_tokens,
    clean_ticket_url,
    infer_canton_from_city,
    normalise_canton,
    parent_for_token,
    parent_genres,
    resolve_canton,
)


class TestCantonNormalisation:
    def test_passes_through_valid_code(self):
        assert normalise_canton("ZH") == "ZH"
        assert normalise_canton("zh") == "ZH"

    def test_maps_full_names_across_languages(self):
        assert normalise_canton("Zürich") == "ZH"
        assert normalise_canton("Genève") == "GE"  # French
        assert normalise_canton("Ginevra") == "GE"  # Italian
        assert normalise_canton("  Bern  ") == "BE"

    def test_unknown_value_returned_unchanged(self):
        assert normalise_canton("Atlantis") == "Atlantis"

    def test_empty_input(self):
        assert normalise_canton("") == ""
        assert normalise_canton(None) is None


class TestInferCantonFromCity:
    def test_known_city(self):
        assert infer_canton_from_city("Basel") == "BS"

    def test_strips_leading_plz(self):
        assert infer_canton_from_city("8005 Zürich") == "ZH"

    def test_unknown_city_returns_empty(self):
        assert infer_canton_from_city("Gotham") == ""
        assert infer_canton_from_city("") == ""


class TestResolveCanton:
    def test_prefers_valid_region(self):
        assert resolve_canton("Zürich", "Basel") == "ZH"

    def test_falls_back_to_city_when_region_useless(self):
        assert resolve_canton("", "Basel") == "BS"
        assert resolve_canton("Atlantis", "Basel") == "BS"

    def test_returns_normalised_region_when_city_unknown(self):
        # Region isn't a valid code and city can't be inferred -> region as-is.
        assert resolve_canton("Atlantis", "Gotham") == "Atlantis"


class TestGenreTaxonomy:
    def test_maps_subgenres_to_parents(self):
        assert parent_genres("Black Metal") == ["Metal"]
        assert parent_for_token("metalcore") == "Hardcore"

    def test_strips_noise_descriptors(self):
        assert parent_genres("Concert, Festival") == []
        assert clean_genre_tokens("Punk, Concert, Live") == ["Punk"]

    def test_unknown_token_collapses_to_other(self):
        assert parent_genres("Polka") == ["Other"]

    def test_dedupes_and_orders_by_taxonomy(self):
        # Punk before Electronic in PARENT_GENRES_ORDER; duplicates collapse.
        result = parent_genres("Techno, Punk, House, Punk Rock")
        assert result == ["Punk", "Electronic"]

    def test_handles_middot_separator(self):
        assert clean_genre_tokens("Metal · Hardcore") == ["Metal", "Hardcore"]

    def test_empty_input(self):
        assert parent_genres("") == []
        assert parent_genres(None) == []
        assert clean_genre_tokens(None) == []


class TestCleanTicketUrl:
    def test_drops_generic_landing_page(self):
        assert (
            clean_ticket_url("https://metalgigs.ch/tickets/kaufen", "metalgigs") is None
        )

    def test_drops_generic_page_with_query_and_trailing_slash(self):
        url = "https://metalgigs.ch/tickets/kaufen/?utm=x#frag"
        assert clean_ticket_url(url, "metalgigs") is None

    def test_keeps_real_ticket_url(self):
        url = "https://metalgigs.ch/tickets/specific-event"
        assert clean_ticket_url(url, "metalgigs") == url

    def test_empty_or_whitespace(self):
        assert clean_ticket_url("", "metalgigs") is None
        assert clean_ticket_url("   ", "metalgigs") is None

    def test_unknown_source_passes_through(self):
        url = "https://example.com/tickets"
        assert clean_ticket_url(url, "unknown-source") == url


class TestLinkify:
    def test_plain_text_is_escaped(self):
        from diytracker.utils import linkify

        out = str(linkify('<b>hi</b> & "there"'))
        assert "<b>" not in out
        assert "&lt;b&gt;" in out
        assert "&amp;" in out

    def test_http_urls_become_links(self):
        from diytracker.utils import linkify

        out = str(linkify("see https://example.org/x for info"))
        assert (
            '<a href="https://example.org/x" rel="nofollow noopener" '
            'target="_blank">https://example.org/x</a>' in out
        )

    def test_trailing_punctuation_stays_outside(self):
        from diytracker.utils import linkify

        out = str(linkify("see https://example.org/x."))
        assert 'href="https://example.org/x"' in out
        assert out.endswith(".")

    def test_unsafe_scheme_stays_text(self):
        from diytracker.utils import linkify

        out = str(linkify("javascript:alert(1) and data:text/html,x"))
        assert "<a " not in out

    def test_none_and_empty(self):
        from diytracker.utils import linkify

        assert str(linkify(None)) == ""
        assert str(linkify("")) == ""

    def test_html_inside_url_neighbourhood_is_escaped(self):
        from diytracker.utils import linkify

        out = str(linkify("<script> https://ok.ch <img src=x onerror=y>"))
        assert "<script>" not in out
        assert "<img" not in out
        assert 'href="https://ok.ch"' in out
