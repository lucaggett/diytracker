"""Admin-editable intro texts on canton and genre landing pages."""

from diytracker.models import PageText, db


def _add_text(kind, key, locale, text):
    db.session.add(PageText(kind=kind, key=key, locale=locale, text=text))
    db.session.commit()


class TestRendering:
    def test_canton_page_shows_locale_text(self, client, make_event):
        make_event()  # Kasheme, Zürich
        _add_text("canton", "zuerich", "en", "The Zurich DIY scene in English.")
        html = client.get("/en/zuerich/").data.decode()
        assert "The Zurich DIY scene in English." in html

    def test_missing_locale_falls_back_to_default(self, client, make_event):
        make_event()
        _add_text("canton", "zuerich", "de", "Die Zürcher DIY-Szene.")
        # Client browses in en; only a de row exists.
        html = client.get("/en/zuerich/").data.decode()
        assert "Die Zürcher DIY-Szene." in html

    def test_no_rows_renders_no_paragraph(self, client, make_event):
        make_event()
        resp = client.get("/en/zuerich/")
        assert resp.status_code == 200
        assert b"whitespace-pre-line" not in resp.data

    def test_genre_page_shows_text(self, client, make_event):
        make_event(genre="Punk")
        _add_text("genre", "punk", "en", "Punk shows all over Switzerland.")
        html = client.get("/en/genre/punk/").data.decode()
        assert "Punk shows all over Switzerland." in html


class TestAdminUI:
    def test_listing_requires_admin(self, client, make_user, login):
        resp = client.get("/admin/page-texts")
        assert resp.status_code in (302, 401, 403)
        login(make_user())  # non-admin
        assert client.get("/admin/page-texts").status_code in (302, 401, 403)

    def test_listing_shows_cantons_and_genres(self, client, admin, login):
        login(admin)
        html = client.get("/admin/page-texts").data.decode()
        assert "/admin/page-texts/canton/zuerich" in html
        assert "/admin/page-texts/genre/metal" in html

    def test_invalid_kind_or_key_404s(self, client, admin, login):
        login(admin)
        assert client.get("/admin/page-texts/venue/zuerich").status_code == 404
        assert client.get("/admin/page-texts/canton/atlantis").status_code == 404

    def test_post_upserts_and_blank_deletes(self, client, admin, login):
        login(admin)
        url = "/admin/page-texts/canton/zuerich"
        resp = client.post(
            url,
            data={"text_de": "Deutscher Text", "text_fr": "Texte français"},
            follow_redirects=True,
        )
        assert resp.status_code == 200
        rows = {
            row.locale: row.text
            for row in PageText.query.filter_by(kind="canton", key="zuerich")
        }
        assert rows == {"de": "Deutscher Text", "fr": "Texte français"}

        # Blanking fr deletes its row, editing de updates in place.
        client.post(url, data={"text_de": "Neuer Text", "text_fr": ""})
        rows = {
            row.locale: row.text
            for row in PageText.query.filter_by(kind="canton", key="zuerich")
        }
        assert rows == {"de": "Neuer Text"}

    def test_edit_form_prefills_existing_text(self, client, admin, login):
        login(admin)
        _add_text("canton", "bern", "it", "Testo italiano.")
        html = client.get("/admin/page-texts/canton/bern").data.decode()
        assert "Testo italiano." in html
