"""
iCal (.ics) generator for Backloggd wishlist games.
"""

from datetime import datetime, date, timedelta
import hashlib
import logging
from typing import List, Dict, Any, Optional

from icalendar import Calendar, Event, Alarm

logger = logging.getLogger(__name__)


def generate_game_uid(game: Dict[str, Any]) -> str:
    """Generate a deterministic UID for a game event."""
    url_or_title = game.get("url") or game.get("title", "unknown")
    hash_digest = hashlib.md5(url_or_title.encode("utf-8")).hexdigest()[:12]
    return f"backloggd-{hash_digest}@backloggd-calendar"


def build_wishlist_calendar(
    games: List[Dict[str, Any]],
    calendar_name: str = "Backloggd Wishlist Releases"
) -> Calendar:
    """
    Build an iCalendar containing events for wishlist games with known release dates.
    """
    cal = Calendar()
    cal.add("prodid", "-//Backloggd Calendar Sync//backloggd-calendar//EN")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", calendar_name)
    cal.add("x-wr-timezone", "UTC")
    cal.add("method", "PUBLISH")

    now = datetime.now(tz=None)

    for game in games:
        rel_date = game.get("release_date")
        if not rel_date:
            continue

        if isinstance(rel_date, datetime):
            event_date = rel_date.date()
        elif isinstance(rel_date, date):
            event_date = rel_date
        else:
            continue

        event = Event()
        title = game.get("title", "Untitled Game")
        event.add("summary", f"🎮 {title}")
        event.add("uid", generate_game_uid(game))
        event.add("dtstamp", now)

        # All-day event start and end
        event.add("dtstart", event_date)
        event.add("dtend", event_date + timedelta(days=1))

        url = game.get("url", "")
        if url:
            event.add("url", url)

        platforms = game.get("platforms", [])
        platform_str = ", ".join(platforms) if platforms else "N/A"
        raw_date_str = game.get("release_date_raw", str(event_date))

        description_lines = [
            f"Game: {title}",
            f"Release Date: {raw_date_str}",
        ]
        if platforms:
            description_lines.append(f"Platforms: {platform_str}")
        if url:
            description_lines.append(f"Backloggd: {url}")

        event.add("description", "\n".join(description_lines))
        event.add("categories", ["Gaming", "Backloggd", "Wishlist"])

        # Alarms / Notifications:
        # 1. Same day at 9:00 AM (+9 hours from 00:00 event start)
        alarm_same_day = Alarm()
        alarm_same_day.add("action", "DISPLAY")
        alarm_same_day.add("description", f"🎮 Release Today: {title}")
        alarm_same_day.add("trigger", timedelta(hours=9))
        event.add_component(alarm_same_day)

        # 2. Day before at 9:00 AM (-15 hours from 00:00 event start)
        alarm_day_before = Alarm()
        alarm_day_before.add("action", "DISPLAY")
        alarm_day_before.add("description", f"🎮 Release Tomorrow: {title}")
        alarm_day_before.add("trigger", -timedelta(hours=15))
        event.add_component(alarm_day_before)

        # 3. Week before at 9:00 AM (-6 days 15 hours from 00:00 event start)
        alarm_week_before = Alarm()
        alarm_week_before.add("action", "DISPLAY")
        alarm_week_before.add("description", f"🎮 Release in 1 Week: {title}")
        alarm_week_before.add("trigger", -timedelta(days=6, hours=15))
        event.add_component(alarm_week_before)

        cal.add_component(event)

    return cal


def export_calendar_to_file(cal: Calendar, file_path: str) -> None:
    """Write the Calendar instance to a .ics file."""
    with open(file_path, "wb") as f:
        f.write(cal.to_ical())
    logger.info(f"Successfully saved calendar to '{file_path}'")
