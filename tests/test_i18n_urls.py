"""Locale-prefixed URLs and hreflang alternates.

The default locale (de) keeps the bare URLs (canonical + x-default); fr/it/en
get a /<lang>/ prefix registered under the same endpoints. The test client
browses with Accept-Language: en (see conftest), so unprefixed pages render
English while prefixed ones must follow the URL.
"""


class TestLocalePrefixRouting:
    def test_bare_url_serves_default_without_redirect(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_prefixed_url_renders_that_locale(self, client, make_event):
        make_event()
        html = client.get("/fr/").data.decode()
        assert '<html lang="fr"' in html

    def test_url_locale_beats_session(self, client):
        with client.session_transaction() as sess:
            sess["lang"] = "de"
        html = client.get("/fr/about").data.decode()
        assert '<html lang="fr"' in html
        assert "Calendrier" in html

    def test_default_locale_prefix_does_not_exist(self, client):
        # /de/... must 404: the bare URL is the canonical German page.
        # (single-segment /de follows the canton slash-redirect first)
        assert client.get("/de/about").status_code == 404
        assert client.get("/de/", follow_redirects=True).status_code == 404

    def test_meta_routes_are_never_prefixed(self, client):
        assert client.get("/fr/robots.txt", follow_redirects=True).status_code == 404
        assert client.get("/fr/sitemap.xml", follow_redirects=True).status_code == 404
        # The scrape honeypot exists only at its bare path.
        assert client.get("/fr/events/archive/").status_code == 404

    def test_links_stay_in_locale(self, client, make_event):
        ev = make_event()
        html = client.get("/fr/").data.decode()
        assert 'href="/fr/about"' in html
        assert f'href="/fr/events/{ev.id}/"' in html

    def test_legal_page_chrome_follows_url_lang(self, client):
        # Regression: /fr/impressum used to render German chrome when the
        # session locale disagreed with the URL.
        with client.session_transaction() as sess:
            sess["lang"] = "de"
        html = client.get("/fr/impressum").data.decode()
        assert '<html lang="fr"' in html


class TestCanonicalAndHreflang:
    def test_canonicals_are_self_referential_per_locale(self, client):
        assert (
            '<link rel="canonical" href="http://localhost/about">'
            in client.get("/about").data.decode()
        )
        assert (
            '<link rel="canonical" href="http://localhost/fr/about">'
            in client.get("/fr/about").data.decode()
        )

    def test_hreflang_alternates_on_localized_pages(self, client):
        expected = [
            ('hreflang="de" href="http://localhost/about"'),
            ('hreflang="fr" href="http://localhost/fr/about"'),
            ('hreflang="it" href="http://localhost/it/about"'),
            ('hreflang="en" href="http://localhost/en/about"'),
            ('hreflang="x-default" href="http://localhost/about"'),
        ]
        for page in ("/about", "/fr/about"):
            html = client.get(page).data.decode()
            for fragment in expected:
                assert fragment in html, (page, fragment)

    def test_no_hreflang_on_non_localized_pages(self, client, make_venue):
        venue = make_venue()
        html = client.get(f"/venues/{venue.id}/accessibility").data.decode()
        assert "hreflang" not in html

    def test_calendar_cache_keeps_locales_apart(self, client):
        # Guards the path-aware cache key: / and /fr/ share an endpoint but
        # must not share a cache entry.
        de = client.get("/").data.decode()
        fr = client.get("/fr/").data.decode()
        assert '<link rel="canonical" href="http://localhost/">' in de
        assert '<link rel="canonical" href="http://localhost/fr/">' in fr

    def test_lang_switcher_targets_localized_path(self, client):
        html = client.get("/fr/about").data.decode()
        # Switching to Italian from /fr/about must land on /it/about, and
        # switching to the default locale on the bare URL.
        assert 'href="/set-language/it?next=/it/about"' in html
        assert 'href="/set-language/de?next=/about"' in html


class TestSitemapAlternates:
    def test_sitemap_lists_all_locale_variants(self, client, make_event):
        ev = make_event()
        xml = client.get("/sitemap.xml").data.decode()
        assert 'xmlns:xhtml="http://www.w3.org/1999/xhtml"' in xml
        for loc in (
            "http://localhost/about",
            "http://localhost/fr/about",
            f"http://localhost/events/{ev.id}/",
            f"http://localhost/it/events/{ev.id}/",
        ):
            assert f"<loc>{loc}</loc>" in xml
        assert (
            '<xhtml:link rel="alternate" hreflang="x-default" '
            'href="http://localhost/about"/>' in xml
        )

    def test_sitemap_never_inherits_requester_locale(self, client):
        # The client browses in en; the de variants must still be unprefixed.
        xml = client.get("/sitemap.xml").data.decode()
        assert "<loc>http://localhost/about</loc>" in xml
        assert "<loc>http://localhost/de/about</loc>" not in xml


class TestCatalogIntegrity:
    """Guards on the compiled catalogs themselves, not on routing."""

    def test_english_catalog_is_the_identity_mapping(self):
        # English *is* the msgid, so every msgstr must mirror its msgid.
        # pybabel update fuzzy-matches new ids against old ones, and once
        # that flag is dropped the wrong string sticks: this is how the
        # footer's "Privacy" link came to render as "Price".
        from babel.messages.pofile import read_po

        with open("translations/en/LC_MESSAGES/messages.po", "rb") as f:
            catalog = read_po(f)
        wrong = [(m.id, m.string) for m in catalog if m.id and m.string != m.id]
        assert wrong == []

    def test_every_locale_is_fully_translated(self):
        from babel.messages.pofile import read_po

        for lang in ("de", "fr", "it", "en"):
            with open(f"translations/{lang}/LC_MESSAGES/messages.po", "rb") as f:
                catalog = read_po(f)
            untranslated = [m.id for m in catalog if m.id and not m.string]
            fuzzy = [m.id for m in catalog if m.id and m.fuzzy]
            assert untranslated == [], f"{lang} has untranslated strings"
            assert fuzzy == [], f"{lang} has fuzzy strings"

    def test_placeholders_survive_translation(self):
        # A dropped or renamed %(name)s raises at render time, so catch it here.
        import re

        from babel.messages.pofile import read_po

        placeholder = re.compile(r"%\([a-z_]+\)[sd]")
        for lang in ("de", "fr", "it", "en"):
            with open(f"translations/{lang}/LC_MESSAGES/messages.po", "rb") as f:
                catalog = read_po(f)
            for m in catalog:
                if not m.id or not m.string:
                    continue
                assert sorted(placeholder.findall(m.id)) == sorted(
                    placeholder.findall(m.string)
                ), f"{lang}: placeholder mismatch in {m.id!r}"
