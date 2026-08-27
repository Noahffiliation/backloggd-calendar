"""
Main entry point to fetch Backloggd wishlist games, generate iCal (.ics) calendar files,
and optionally sync directly to Google Calendar via Google Calendar API.
"""

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from backloggd_client import fetch_backloggd_wishlist
from google_sync import (
    get_google_calendar_service,
    get_or_create_calendar,
    sync_games_to_google_calendar,
)
from ical_builder import build_wishlist_calendar, export_calendar_to_file

# Load .env if present
load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch Backloggd wishlist games with release dates and sync to Google Calendar / iCal."
    )
    parser.add_argument(
        "-u",
        "--username",
        default=os.getenv("BACKLOGGD_USERNAME"),
        help="Backloggd Username (or set BACKLOGGD_USERNAME env var)",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=os.getenv("OUTPUT_FILE", "backloggd_wishlist.ics"),
        help="Output .ics file path (default: backloggd_wishlist.ics)",
    )
    parser.add_argument(
        "-d",
        "--days-back",
        type=int,
        default=int(os.getenv("DAYS_BACK", "30")),
        help="Number of days in the past to include (default: 30 days / ~1 month ago)",
    )
    parser.add_argument(
        "-g",
        "--sync-google",
        action="store_true",
        default=os.getenv("SYNC_GOOGLE", "").lower() in ("true", "1", "yes"),
        help="Directly sync events to Google Calendar using Google Calendar API",
    )
    raw_extras = os.getenv("INCLUDE_EXTRAS")
    include_extras_default = (
        True
        if raw_extras is None or not raw_extras.strip()
        else raw_extras.strip().lower() in ("true", "1", "yes")
    )
    raw_list_types = os.getenv("BACKLOGGD_LIST_TYPES")
    list_types_default = (
        raw_list_types.strip() if raw_list_types and raw_list_types.strip() else "wishlist"
    )

    parser.add_argument(
        "--include-extras",
        action=argparse.BooleanOptionalAction,
        default=include_extras_default,
        help="Include Extras (DLC, Expansions, Editions, etc.) in addition to Base Games (default: True)",
    )
    parser.add_argument(
        "--list-types",
        default=list_types_default,
        help="Comma-separated Backloggd list types to sync (e.g. 'wishlist', 'wishlist,backlog'; default: wishlist)",
    )
    parser.add_argument(
        "--no-headless",
        action="store_true",
        help="Run browser in non-headless mode (useful for debugging)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose debug logging")
    args = parser.parse_args()
    if args.output:
        # Validate output path stays strictly within current working directory to prevent path injection
        validate_safe_path(args.output, Path.cwd())
    return args


def validate_safe_path(file_path_str: str, base_dir: Path | None = None) -> Path:
    """Ensure output path stays within workspace to prevent path traversal."""
    target_path = Path(file_path_str)
    if base_dir is not None:
        base_dir = base_dir.resolve()
        if not target_path.is_absolute():
            target_path = base_dir / target_path
        resolved_path = target_path.resolve()
        try:
            resolved_path.relative_to(base_dir)
        except ValueError:
            raise ValueError(f"Path '{file_path_str}' is outside allowed directory '{base_dir}'")
        return resolved_path
    return target_path


def main():
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    username = args.username
    if not username:
        logger.error(
            "Error: Backloggd username is required. Pass --username or set BACKLOGGD_USERNAME env var."
        )
        sys.exit(1)
        return

    output_path = validate_safe_path(args.output, Path.cwd())

    logger.info(
        f"Starting Backloggd sync for user '{username}' (days back: {args.days_back}, extras: {args.include_extras}, lists: {args.list_types})..."
    )

    # 1. Fetch wishlist/backlog games using Playwright
    try:
        games = fetch_backloggd_wishlist(
            username=username,
            days_back=args.days_back,
            headless=not args.no_headless,
            include_extras=args.include_extras,
            list_types=args.list_types,
        )
    except Exception as e:
        logger.exception(f"Failed to fetch games from Backloggd: {e}")
        sys.exit(1)
        return

    base_count = sum(1 for g in games if g.get("category_type") == "base")
    extra_count = sum(1 for g in games if g.get("category_type") == "extra")
    logger.info(
        f"Found {len(games)} total items in target release date range ({base_count} Base Games, {extra_count} Extras/DLCs)."
    )

    # 2. Build iCal (.ics) Calendar
    calendar = build_wishlist_calendar(games, calendar_name=f"Backloggd Wishlist - {username}")
    export_calendar_to_file(calendar, str(output_path))
    logger.info(f"Generated iCal calendar saved to '{output_path}'")

    # 3. Direct Google Calendar Sync (if enabled)
    if args.sync_google:
        logger.info("Starting direct Google Calendar sync...")
        try:
            service = get_google_calendar_service()
            cal_id = get_or_create_calendar(service)
            sync_games_to_google_calendar(service, cal_id, games)
            logger.info("Google Calendar sync completed successfully.")
        except Exception as e:
            logger.exception(f"Google Calendar sync failed: {e}")
            sys.exit(1)
            return


if __name__ == "__main__":
    main()
