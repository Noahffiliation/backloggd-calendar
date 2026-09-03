"""
iCal (.ics) generator for Backloggd wishlist games.
"""

import hashlib
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from icalendar import Alarm, Calendar, Event

logger = logging.getLogger(__name__)


def generate_game_uid(game: dict[str, Any]) -> str:
    """Generate a deterministic UID for a game event."""
    url_or_title = game.get("url") or game.get("title", "unknown")
    hash_digest = hashlib.md5(url_or_title.encode("utf-8")).hexdigest()[:12]
    return f"backloggd-{hash_digest}@backloggd-calendar"


def _add_event_alarms(event: Event, title: str) -> None:
    """Attach standard alarms to a game release event."""
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


def _create_game_event(game: dict[str, Any], now: datetime) -> Event | None:
    """Build a single iCalendar Event for a game release if date is valid."""
    rel_date = game.get("release_date")
    if not rel_date:
        return None

    if isinstance(rel_date, datetime):
        event_date = rel_date.date()
    elif isinstance(rel_date, date):
        event_date = rel_date
    else:
        return None

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

    category_type = game.get("category_type")
    description_lines = [
        f"Game: {title}",
        f"Release Date: {raw_date_str}",
    ]
    if category_type == "extra":
        description_lines.append("Type: DLC / Extra")
    if platforms:
        description_lines.append(f"Platforms: {platform_str}")
    if url:
        description_lines.append(f"Backloggd: {url}")

    event.add("description", "\n".join(description_lines))

    categories = ["Gaming", "Backloggd", "Wishlist"]
    if category_type == "extra":
        categories.append("DLC/Expansion")
    event.add("categories", categories)

    _add_event_alarms(event, title)
    return event


def build_wishlist_calendar(
    games: list[dict[str, Any]], calendar_name: str = "Backloggd Wishlist Releases"
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
        event = _create_game_event(game, now)
        if event:
            cal.add_component(event)

    return cal


def export_calendar_to_file(
    cal: Calendar, file_path: str | Path, base_dir: Path | None = None
) -> None:
    """Write the Calendar instance to a .ics file, enforcing path validation."""
    target_path = Path(file_path)
    if base_dir is not None:
        base_dir = base_dir.resolve()
        if not target_path.is_absolute():
            target_path = base_dir / target_path
        resolved_path = target_path.resolve()
        try:
            resolved_path.relative_to(base_dir)
        except ValueError:
            raise ValueError(f"Path '{file_path}' is outside allowed directory '{base_dir}'")
    else:
        resolved_path = target_path.resolve()

    with open(resolved_path, "wb") as f:
        f.write(cal.to_ical())
    logger.info(f"Successfully saved calendar to '{resolved_path}'")
