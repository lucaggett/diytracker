"""Tests for the weekly calendar PNG generator."""

from datetime import date, timedelta

from PIL import Image

from diytracker.services.calendar_image import (
    _load_font,
    generate_weekly_calendar_image,
)


def _monday_of(d):
    return d - timedelta(days=d.weekday())


class TestGenerateWeeklyCalendarImage:
    def test_empty_week_renders_placeholder(self, app):
        monday = _monday_of(date.today())
        img = generate_weekly_calendar_image(monday)
        assert isinstance(img, Image.Image)
        assert img.size == (1080, 1350)
        assert img.mode == "RGBA"

    def test_renders_events_within_the_week(self, app, make_event, make_venue):
        monday = _monday_of(date.today())
        venue = make_venue(name="Kasheme", city="Zürich")
        make_event(
            name="Show",
            venue=venue,
            days_from_now=0,
            genre="Punk",
            acts="Band A, Band B",
        )
        img = generate_weekly_calendar_image(monday)
        assert img.size == (1080, 1350)

    def test_filters_by_parent_genre(self, app, make_event, make_venue):
        monday = _monday_of(date.today())
        venue = make_venue()
        make_event(name="Punk Show", venue=venue, days_from_now=0, genre="Punk")
        make_event(name="Metal Show", venue=venue, days_from_now=1, genre="Death Metal")

        # Matching genre still renders without error.
        img_match = generate_weekly_calendar_image(monday, parent_genre="Punk")
        assert img_match.size == (1080, 1350)

        # A parent genre with zero matching events falls back to the
        # "no events" placeholder branch rather than erroring.
        img_no_match = generate_weekly_calendar_image(monday, parent_genre="Jazz")
        assert img_no_match.size == (1080, 1350)

    def test_events_spanning_multiple_days_and_many_genres(
        self, app, make_event, make_venue
    ):
        monday = _monday_of(date.today())
        venue = make_venue()
        genres = [
            "Punk",
            "Death Metal",
            "Black Metal",
            "Hardcore",
            "Grindcore",
            "Doom Metal",
            "Crust Punk",
            "Sludge",
            "Post-Punk",
            "Noise",
            "Powerviolence",
            "Stoner Rock",
            "Screamo",
        ]
        for i, genre in enumerate(genres):
            make_event(
                name=f"Show {i}",
                venue=venue,
                days_from_now=i % 5,
                genre=genre,
                acts=f"Act {i}",
            )
        img = generate_weekly_calendar_image(monday)
        assert img.size == (1080, 1350)

    def test_long_venue_and_act_names_wrap_without_error(
        self, app, make_event, make_venue
    ):
        monday = _monday_of(date.today())
        venue = make_venue(
            name="A Venue With An Extremely Long Name That Must Wrap Across Lines",
            city="Zürich",
        )
        make_event(
            name="Show",
            venue=venue,
            days_from_now=0,
            genre="Punk",
            acts="Band With A Very Long Name, Another Band With An Even Longer Name Than That",
        )
        img = generate_weekly_calendar_image(monday)
        assert img.size == (1080, 1350)


class TestLoadFont:
    def test_returns_usable_font_regular_and_bold(self, app):
        regular = _load_font(20, bold=False)
        bold = _load_font(20, bold=True)
        assert regular is not None
        assert bold is not None
