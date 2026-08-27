"""
Google Calendar API Sync module using Service Account or OAuth Credentials for Backloggd Wishlist.
"""

import logging
import os
import os.path
import time
from datetime import date, datetime, timedelta
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ical_builder import generate_game_uid

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]
SERVICE_ACCOUNT_FILE = "service_account.json"
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
CALENDAR_NAME = "Backloggd Wishlist Releases"


def _get_oauth_credentials(credentials_path: str, token_path: str):
    """Load or refresh OAuth client credentials."""
    creds = None
    if os.path.exists(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        except Exception as e:
            logger.warning(f"Could not load {token_path}: {e}")

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            return creds
        except Exception as e:
            logger.warning(f"Could not refresh token: {e}")

    if not os.path.exists(credentials_path):
        raise FileNotFoundError(
            f"Neither '{SERVICE_ACCOUNT_FILE}' nor '{credentials_path}' was found.\n"
            "Please provide a service_account.json or credentials.json file."
        )

    flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(token_path, "w") as token_file:
        token_file.write(creds.to_json())

    return creds


def get_google_calendar_service(
    service_account_path: str = SERVICE_ACCOUNT_FILE,
    credentials_path: str = CREDENTIALS_FILE,
    token_path: str = TOKEN_FILE,
):
    """
    Authenticate and return a Google Calendar API service instance.
    Prefers service_account.json if present; otherwise falls back to OAuth credentials.
    """
    if os.path.exists(service_account_path):
        logger.info(f"Using Service Account authentication from '{service_account_path}'")
        creds = service_account.Credentials.from_service_account_file(
            service_account_path, scopes=SCOPES
        )
        return build("calendar", "v3", credentials=creds)

    creds = _get_oauth_credentials(credentials_path, token_path)
    return build("calendar", "v3", credentials=creds)


def share_calendar_with_email(service, calendar_id: str, share_email: str | None = None):
    """Share a Google Calendar with a user email address via ACL without sending notification emails."""
    email = share_email or os.getenv("GOOGLE_SHARE_EMAIL")
    if not email:
        return
    try:
        existing_acls = (
            service.acl().list(calendarId=calendar_id).execute(num_retries=3).get("items", [])
        )
        for acl in existing_acls:
            if acl.get("scope", {}).get("value", "").lower() == email.lower():
                return

        rule = {"scope": {"type": "user", "value": email}, "role": "writer"}
        service.acl().insert(calendarId=calendar_id, body=rule, sendNotifications=False).execute(
            num_retries=3
        )
        logger.info(f"Shared Google Calendar ID '{calendar_id}' with '{email}'")
    except HttpError as error:
        if "alreadyExists" not in str(error) and "duplicate" not in str(error).lower():
            logger.warning(f"Failed to share calendar with {email}: {error}")


def get_or_create_calendar(service, calendar_summary: str = CALENDAR_NAME) -> str:
    """Find existing calendar by summary or create a new dedicated secondary calendar."""
    custom_cal_id = os.getenv("GOOGLE_CALENDAR_ID")
    if custom_cal_id:
        return custom_cal_id

    calendar_list = service.calendarList().list().execute(num_retries=3)
    for cal in calendar_list.get("items", []):
        if cal.get("summary") == calendar_summary:
            logger.info(f"Found existing Google Calendar '{calendar_summary}' (ID: {cal['id']})")
            return cal["id"]

    new_cal = {
        "summary": calendar_summary,
        "description": "Automated calendar sync for Backloggd wishlist game releases.",
        "timeZone": "UTC",
    }
    created_cal = service.calendars().insert(body=new_cal).execute(num_retries=3)
    cal_id = created_cal["id"]
    logger.info(f"Created new Google Calendar '{calendar_summary}' (ID: {cal_id})")

    share_calendar_with_email(service, cal_id)
    return cal_id


def _fetch_existing_events_by_uid(service: Any, calendar_id: str) -> dict[str, dict[str, Any]]:
    """Fetch existing events indexed by iCalUID from Google Calendar."""
    existing_events_by_uid = {}
    page_token = None
    while True:
        events_res = (
            service.events()
            .list(calendarId=calendar_id, pageToken=page_token, singleEvents=True, maxResults=250)
            .execute(num_retries=3)
        )

        for item in events_res.get("items", []):
            ical_uid = item.get("iCalUID")
            if ical_uid:
                existing_events_by_uid[ical_uid] = item

        page_token = events_res.get("nextPageToken")
        if not page_token:
            break
    return existing_events_by_uid


def _get_event_date(rel_date: Any) -> date | None:
    """Extract a date object from release_date attribute."""
    if isinstance(rel_date, datetime):
        return rel_date.date()
    if isinstance(rel_date, date):
        return rel_date
    return None


def _build_google_event_payload(game: dict[str, Any], event_date: date, uid: str) -> dict[str, Any]:
    """Construct Google Calendar event resource dictionary."""
    title = game.get("title", "Untitled Game")
    url = game.get("url", "")
    platforms = game.get("platforms", [])
    platform_str = ", ".join(platforms) if platforms else "N/A"
    raw_date_str = game.get("release_date_raw", str(event_date))

    category_type = game.get("category_type")
    desc_lines = [
        f"Game: {title}",
        f"Release Date: {raw_date_str}",
    ]
    if category_type == "extra":
        desc_lines.append("Type: DLC / Extra")
    if platforms:
        desc_lines.append(f"Platforms: {platform_str}")
    if url:
        desc_lines.append(f"Backloggd: {url}")

    event_body: dict[str, Any] = {
        "summary": f"🎮 {title}",
        "description": "\n".join(desc_lines),
        "start": {"date": event_date.isoformat()},
        "end": {"date": (event_date + timedelta(days=1)).isoformat()},
        "iCalUID": uid,
        "reminders": {
            "useDefault": False,
            "overrides": [
                {"method": "popup", "minutes": -540},  # Same day at 9:00 AM
                {"method": "popup", "minutes": 900},  # 1 day before at 9:00 AM
                {"method": "popup", "minutes": 9540},  # 1 week before at 9:00 AM
            ],
        },
    }
    if url:
        event_body["source"] = {"title": "Backloggd", "url": url}

    return event_body


def _upsert_single_event(
    service: Any,
    calendar_id: str,
    event_body: dict[str, Any],
    existing_event: dict[str, Any] | None,
) -> bool:
    """Inserts or patches a Google Calendar event. Returns True if updated, False if created."""
    if existing_event:
        event_id = existing_event["id"]
        service.events().patch(calendarId=calendar_id, eventId=event_id, body=event_body).execute(
            num_retries=3
        )
        return True

    try:
        service.events().insert(calendarId=calendar_id, body=event_body).execute(num_retries=3)
        return False
    except HttpError as e:
        if getattr(e.resp, "status", None) == 409 or "alreadyExists" in str(e) or "duplicate" in str(e).lower():
            logger.debug(f"Event UID {event_body.get('iCalUID')} already exists in calendar.")
            return True
        raise


def sync_games_to_google_calendar(
    service: Any, calendar_id: str, games: list[dict[str, Any]]
) -> None:
    """
    Sync games to the Google Calendar, creating or updating events.
    """
    logger.info(f"Syncing {len(games)} game release events to Google Calendar '{calendar_id}'...")

    existing_events_by_uid = _fetch_existing_events_by_uid(service, calendar_id)

    count_created = 0
    count_updated = 0

    for game in games:
        event_date = _get_event_date(game.get("release_date"))
        if not event_date:
            continue

        uid = generate_game_uid(game)
        event_body = _build_google_event_payload(game, event_date, uid)
        existing_event = existing_events_by_uid.get(uid)

        is_updated = _upsert_single_event(service, calendar_id, event_body, existing_event)
        if is_updated:
            count_updated += 1
        else:
            count_created += 1

        time.sleep(0.1)

    logger.info(f"Google Calendar Sync finished: {count_created} created, {count_updated} updated.")
