"""
Unit tests for ical_builder.py
"""

from datetime import date, datetime
import os
import tempfile

from icalendar import Calendar, Event

from ical_builder import build_wishlist_calendar, export_calendar_to_file, generate_game_uid


def test_generate_game_uid():
    game1 = {"url": "https://backloggd.com/games/hades-ii/", "title": "Hades II"}
    game2 = {"title": "Hades II"}
    game3 = {}

    uid1 = generate_game_uid(game1)
    uid2 = generate_game_uid(game2)
    uid3 = generate_game_uid(game3)

    assert uid1.startswith("backloggd-")
    assert uid1.endswith("@backloggd-calendar")
    assert uid2.startswith("backloggd-")
    assert uid3 == generate_game_uid({})


def test_build_wishlist_calendar():
    games = [
        {
            "title": "Zelda 3",
            "url": "https://backloggd.com/games/zelda-3/",
            "release_date": date(2026, 11, 20),
            "release_date_raw": "Nov 20, 2026",
            "platforms": ["Switch 2", "PC"],
        },
        {
            "title": "Hollow Knight Silksong",
            "url": "https://backloggd.com/games/silksong/",
            "release_date": datetime(2026, 6, 15, 0, 0),
            "release_date_raw": "Jun 15, 2026",
        },
        {
            "title": "TBA Game",
            "release_date": None,
        },
        {
            "title": "Invalid Date Game",
            "release_date": "not-a-date-obj",
        },
    ]

    cal = build_wishlist_calendar(games, calendar_name="My Wishlist")
    assert isinstance(cal, Calendar)
    assert cal.get("x-wr-calname") == "My Wishlist"

    # Only 2 games had valid dates
    events = [c for c in cal.subcomponents if isinstance(c, Event)]
    assert len(events) == 2

    e1 = events[0]
    assert e1.get("summary") == "🎮 Zelda 3"
    assert e1.get("url") == "https://backloggd.com/games/zelda-3/"
    assert "Switch 2, PC" in e1.get("description")

    # Verify alarms exist
    alarms = [sub for sub in e1.subcomponents if sub.name == "VALARM"]
    assert len(alarms) == 3


def test_export_calendar_to_file():
    cal = Calendar()
    cal.add("prodid", "-//Test//EN")
    cal.add("version", "2.0")

    with tempfile.NamedTemporaryFile(suffix=".ics", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        export_calendar_to_file(cal, tmp_path)
        assert os.path.exists(tmp_path)
        with open(tmp_path, "rb") as f:
            content = f.read().decode("utf-8")
        assert "BEGIN:VCALENDAR" in content
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
