"""
Google Calendar API Sync module using Service Account or OAuth Credentials for Backloggd Wishlist,
with direct Gmail API event notifications.
"""

import base64
import contextlib
import html
import logging
import os
import os.path
import time
import webbrowser
from datetime import UTC, date, datetime, timedelta
from email.message import EmailMessage
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ical_builder import generate_game_uid

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
]
SERVICE_ACCOUNT_FILE = "service_account.json"
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
CALENDAR_NAME = "Backloggd Wishlist Releases"
EVENT_CHANGE_NOTIFICATION_TYPES = frozenset(
    {
        "eventCreation",
        "eventChange",
        "eventCancellation",
        "eventResponse",
    }
)


def _save_token_file(token_path: str, creds: Any) -> None:
    """Persist serialized OAuth credentials to token_path."""
    json_data = creds.to_json()
    if not isinstance(json_data, str):
        return
    with contextlib.suppress(OSError), open(token_path, "w", encoding="utf-8") as token_file:
        token_file.write(json_data)


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
            _save_token_file(token_path, creds)
            return creds
        except Exception as e:
            logger.warning(f"Could not refresh token: {e}")

    if not os.path.exists(credentials_path):
        raise FileNotFoundError(
            f"Neither '{SERVICE_ACCOUNT_FILE}' nor '{credentials_path}' was found.\n"
            "Please provide a service_account.json or credentials.json file."
        )

    flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
    try:
        creds = flow.run_local_server(port=0)
    except webbrowser.Error:
        logger.info(
            "No runnable browser found; please open the printed authorization URL in your browser."
        )
        creds = flow.run_local_server(port=0, open_browser=False)
    _save_token_file(token_path, creds)

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
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    creds = _get_oauth_credentials(credentials_path, token_path)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def get_gmail_service(
    credentials_path: str = CREDENTIALS_FILE,
    token_path: str = TOKEN_FILE,
) -> Any | None:
    """
    Authenticate via OAuth (credentials.json / token.json) and return a Gmail API service instance.
    Returns None if OAuth credentials are not available.
    """
    try:
        creds = _get_oauth_credentials(credentials_path, token_path)
        return build("gmail", "v1", credentials=creds, cache_discovery=False)
    except Exception as e:
        logger.warning(f"Gmail API service unavailable for email notifications: {e}")
        return None


def disable_calendar_other_notifications(
    calendar_ids: list[str],
    credentials_path: str = CREDENTIALS_FILE,
    token_path: str = TOKEN_FILE,
) -> None:
    """
    Clears Google Calendar's built-in event-change 'Other notifications'
    (eventCreation, eventChange, eventCancellation, eventResponse) on the user's
    CalendarList entries so Google Calendar does not send duplicate RSVP-style emails,
    while preserving non-event notifications such as Daily agenda ('agenda').
    """
    if not calendar_ids or not (os.path.exists(token_path) or os.path.exists(credentials_path)):
        return
    try:
        creds = _get_oauth_credentials(credentials_path, token_path)
        user_cal_service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        for cal_id in calendar_ids:
            if not cal_id:
                continue
            with contextlib.suppress(Exception):
                entry = (
                    user_cal_service.calendarList().get(calendarId=cal_id).execute(num_retries=3)
                )
                notification_settings = entry.get("notificationSettings") or {}
                existing_notifications = notification_settings.get("notifications") or []
                kept_notifications = [
                    notif
                    for notif in existing_notifications
                    if notif.get("type") not in EVENT_CHANGE_NOTIFICATION_TYPES
                ]
                if kept_notifications != existing_notifications:
                    user_cal_service.calendarList().patch(
                        calendarId=cal_id,
                        body={"notificationSettings": {"notifications": kept_notifications}},
                    ).execute(num_retries=3)
    except Exception as e:
        logger.debug(f"Could not update user calendarList notificationSettings: {e}")


def _format_event_when(event_data: dict[str, Any]) -> str:
    """Format start/end fields of a Google Calendar event dict into a human-readable string."""
    start = event_data.get("start") or {}
    end = event_data.get("end") or {}

    start_date = start.get("date")
    if start_date:
        try:
            dt_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
            return f"{dt_obj.strftime('%a, %b %d, %Y')} (All day)"
        except ValueError:
            return f"{start_date} (All day)"

    start_dt_str = start.get("dateTime")
    if start_dt_str:
        try:
            start_dt = datetime.fromisoformat(start_dt_str)
            end_dt_str = end.get("dateTime")
            if end_dt_str:
                end_dt = datetime.fromisoformat(end_dt_str)
                return (
                    f"{start_dt.strftime('%a, %b %d, %Y, %H:%M')} - {end_dt.strftime('%H:%M UTC')}"
                )
            return start_dt.strftime("%a, %b %d, %Y, %H:%M UTC")
        except ValueError:
            return str(start_dt_str)

    return "Unknown date"


def _collect_event_changes(
    previous_event: dict[str, Any] | None,
    summary: str,
    when_str: str,
    description: str,
) -> tuple[list[str], list[str]]:
    """Build plain-text and HTML diff entries showing what changed from previous_event."""
    if not previous_event:
        return [], []

    text_changes: list[str] = []
    html_changes: list[str] = []

    old_summary = str(previous_event.get("summary") or "")
    if old_summary and old_summary != summary:
        text_changes.append(f"- Title: {old_summary} -> {summary}")
        html_changes.append(
            f"<li><strong>Title:</strong> <del style='color:#888;'>{html.escape(old_summary)}</del> "
            f"&rarr; <strong>{html.escape(summary)}</strong></li>"
        )

    old_when = _format_event_when(previous_event)
    if old_when != when_str:
        text_changes.append(f"- When: {old_when} -> {when_str}")
        html_changes.append(
            f"<li><strong>When:</strong> <del style='color:#888;'>{html.escape(old_when)}</del> "
            f"&rarr; <strong>{html.escape(when_str)}</strong></li>"
        )

    old_description = str(previous_event.get("description") or "").replace("\r\n", "\n").strip()
    new_description = description.replace("\r\n", "\n").strip()
    if old_description != new_description:
        old_desc_display = old_description or "(empty)"
        new_desc_display = new_description or "(empty)"
        text_changes.append(
            f"- Description:\n  Before: {old_desc_display}\n  After:  {new_desc_display}"
        )
        old_desc_html = html.escape(old_desc_display).replace("\n", "<br>")
        new_desc_html = html.escape(new_desc_display).replace("\n", "<br>")
        html_changes.append(
            f"<li><strong>Description:</strong><br>"
            f"<del style='color:#888;'>{old_desc_html}</del><br>"
            f"&rarr; {new_desc_html}</li>"
        )

    return text_changes, html_changes


def _build_notification_email_message(
    action: str,
    event_data: dict[str, Any],
    notify_email: str,
    calendar_name: str = CALENDAR_NAME,
    previous_event: dict[str, Any] | None = None,
) -> dict[str, str]:
    """
    Construct a standard MIME email without text/calendar or EventReservation schema
    so mobile Gmail notifications display standard Delete / Mark as Read actions.
    """
    summary = str(event_data.get("summary") or "Untitled Event")
    description = str(event_data.get("description") or "").replace("\r\n", "\n").strip()
    when_str = _format_event_when(event_data)

    action_label = {
        "created": "New event",
        "updated": "Changed event",
        "deleted": "Canceled event",
    }.get(action.lower(), "Updated event")

    subject = f"{action_label}: {summary} @ {when_str}"

    text_lines = [
        f"{action_label} on {calendar_name}",
        "",
        f"Event: {summary}",
        f"When:  {when_str}",
    ]
    text_changes, html_changes = _collect_event_changes(
        previous_event, summary, when_str, description
    )
    if text_changes:
        text_lines.extend(["", "What Changed:", *text_changes])

    if description:
        text_lines.extend(["", description])

    escaped_desc = html.escape(description).replace("\n", "<br>") if description else ""
    changes_block = (
        f"<p><strong>What Changed:</strong></p><ul>{''.join(html_changes)}</ul>"
        if html_changes
        else ""
    )
    html_body = (
        f"<div style='font-family: sans-serif; line-height: 1.5;'>"
        f"<p><strong>{html.escape(action_label)}</strong> on "
        f"<em>{html.escape(calendar_name)}</em></p>"
        f"<p><strong>Event:</strong> {html.escape(summary)}<br>"
        f"<strong>When:</strong> {html.escape(when_str)}</p>"
        f"{changes_block}"
        f"{f'<p>{escaped_desc}</p>' if escaped_desc else ''}"
        f"</div>"
    )

    msg = EmailMessage()
    msg["To"] = notify_email
    msg["From"] = notify_email
    msg["Subject"] = subject
    msg.set_content("\n".join(text_lines))
    msg.add_alternative(html_body, subtype="html")

    encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    return {"raw": encoded}


def send_event_notification_email(
    gmail_service: Any,
    notify_email: str | None,
    action: str,
    event_data: dict[str, Any],
    calendar_name: str = CALENDAR_NAME,
    previous_event: dict[str, Any] | None = None,
) -> bool:
    """Send an individual plain email notification via the Gmail API."""
    if not gmail_service or not notify_email:
        return False
    try:
        raw_body = _build_notification_email_message(
            action=action,
            event_data=event_data,
            notify_email=notify_email,
            calendar_name=calendar_name,
            previous_event=previous_event,
        )
        gmail_service.users().messages().send(userId="me", body=raw_body).execute(num_retries=5)
        # Gmail API messages.send costs 100 quota units against a 15,000 units/minute/user limit
        time.sleep(0.5)
        return True
    except Exception as e:
        logger.warning(
            f"Could not send '{action}' email notification for '{event_data.get('summary')}': {e}"
        )
        return False


def share_calendar_with_email(service, calendar_id: str, share_email: str | None = None):
    """Share a Google Calendar with a user email address via ACL without sending notification emails."""
    email = (
        share_email or os.getenv("GOOGLE_SHARE_EMAIL") or os.getenv("GOOGLE_CALENDAR_SHARE_EMAIL")
    )
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
    """Fetch existing events (including deleted/tombstoned ones) indexed by iCalUID from Google Calendar."""
    existing_events_by_uid = {}
    page_token = None
    while True:
        events_res = (
            service.events()
            .list(
                calendarId=calendar_id,
                pageToken=page_token,
                singleEvents=False,
                showDeleted=True,
                maxResults=2500,
            )
            .execute(num_retries=5)
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


def _normalize_datetime_str(dt_str: str | None) -> str | None:
    """Normalize ISO-8601 / RFC3339 datetime strings (e.g. 'Z' vs '+00:00') to UTC ISO format."""
    if not dt_str:
        return None
    try:
        return datetime.fromisoformat(dt_str).astimezone(UTC).isoformat()
    except ValueError:
        return dt_str


def _event_schedule_changed(existing_event: dict[str, Any], new_body: dict[str, Any]) -> bool:
    """Check if start or end date/dateTime of an event has changed."""
    existing_start = existing_event.get("start") or {}
    new_start = new_body.get("start") or {}
    if existing_start.get("date") != new_start.get("date") or _normalize_datetime_str(
        existing_start.get("dateTime")
    ) != _normalize_datetime_str(new_start.get("dateTime")):
        return True

    existing_end = existing_event.get("end") or {}
    new_end = new_body.get("end") or {}
    return existing_end.get("date") != new_end.get("date") or _normalize_datetime_str(
        existing_end.get("dateTime")
    ) != _normalize_datetime_str(new_end.get("dateTime"))


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
        "status": "confirmed",
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


def _should_notify_event_update(
    existing_event: dict[str, Any] | None, event_body: dict[str, Any]
) -> bool:
    """
    Return True if an existing event's title, description, or schedule (start/end date or time)
    changed, warranting a 'Changed event' email notification.
    """
    if not existing_event:
        return True
    if str(existing_event.get("summary") or "") != str(event_body.get("summary") or ""):
        return True
    old_desc = str(existing_event.get("description") or "").replace("\r\n", "\n").strip()
    new_desc = str(event_body.get("description") or "").replace("\r\n", "\n").strip()
    if old_desc != new_desc:
        return True
    return _event_schedule_changed(existing_event, event_body)


def _event_needs_update(existing_event: dict[str, Any], event_body: dict[str, Any]) -> bool:
    """Check if the existing Google Calendar event differs in key fields from the new payload."""
    if bool(existing_event.get("attendees")) != bool(event_body.get("attendees")):
        return True

    existing_status = existing_event.get("status") or "confirmed"
    new_status = event_body.get("status") or "confirmed"
    if existing_status != new_status:
        return True

    if _should_notify_event_update(existing_event, event_body):
        return True

    existing_source = existing_event.get("source") or {}
    new_source = event_body.get("source") or {}
    return existing_source.get("url") != new_source.get("url")


def _upsert_single_event(
    service: Any,
    calendar_id: str,
    event_body: dict[str, Any],
    existing_event: dict[str, Any] | None,
) -> bool | None:
    """
    Inserts or patches a Google Calendar event.
    Ensures 'status': 'confirmed' so tombstoned/cancelled events are revived, and suppresses
    Google Calendar's built-in email notifications (sendUpdates='none') because notifications
    are sent directly via the Gmail API without RSVP metadata.

    Returns:
        True: Event was updated (patched)
        False: Event was created (inserted)
        None: Event was already up to date (no API call made)
    """
    body_with_status = dict(event_body)
    body_with_status.setdefault("status", "confirmed")

    if existing_event:
        if not _event_needs_update(existing_event, body_with_status):
            logger.debug(
                f"Event UID {body_with_status.get('iCalUID')} is unchanged, skipping update."
            )
            return None

        event_id = existing_event["id"]
        if existing_event.get("attendees"):
            body_with_status.setdefault("attendees", [])

        service.events().patch(
            calendarId=calendar_id,
            eventId=event_id,
            body=body_with_status,
            sendUpdates="none",
        ).execute(num_retries=5)
        return True

    try:
        service.events().insert(
            calendarId=calendar_id,
            body=body_with_status,
            sendUpdates="none",
        ).execute(num_retries=5)
        return False
    except HttpError as e:
        if (
            getattr(e.resp, "status", None) == 409
            or "alreadyExists" in str(e)
            or "duplicate" in str(e).lower()
        ):
            uid = body_with_status.get("iCalUID")
            logger.debug(
                f"Event UID {uid} already exists in calendar, attempting patch fallback..."
            )
            existing_list = (
                service.events()
                .list(calendarId=calendar_id, iCalUID=uid, showDeleted=True)
                .execute(num_retries=3)
                .get("items", [])
            )
            if existing_list:
                body_with_status.setdefault("attendees", [])
                service.events().patch(
                    calendarId=calendar_id,
                    eventId=existing_list[0]["id"],
                    body=body_with_status,
                    sendUpdates="none",
                ).execute(num_retries=5)
            return True
        raise


def _delete_removed_events(
    service: Any,
    calendar_id: str,
    current_uids: set[str],
    existing_events: list[dict[str, Any]],
    gmail_service: Any = None,
    notify_email: str | None = None,
    calendar_name: str = CALENDAR_NAME,
) -> int:
    """Delete events from Google Calendar that are no longer present in the current sync set."""
    deleted_count = 0
    for g_event in existing_events:
        if g_event.get("status") == "cancelled":
            continue
        i_cal_uid = g_event.get("iCalUID", "")
        if i_cal_uid.startswith("backloggd-") and i_cal_uid not in current_uids:
            event_id = g_event.get("id")
            summary = g_event.get("summary", "Unknown Event")
            try:
                service.events().delete(
                    calendarId=calendar_id,
                    eventId=event_id,
                    sendUpdates="none",
                ).execute(num_retries=5)
                logger.info(f"Deleted removed/dropped event '{summary}' from Google Calendar.")
                send_event_notification_email(
                    gmail_service=gmail_service,
                    notify_email=notify_email,
                    action="deleted",
                    event_data=g_event,
                    calendar_name=calendar_name,
                )
                deleted_count += 1
                time.sleep(0.05)
            except HttpError as error:
                logger.warning(f"Could not delete removed event '{summary}': {error}")
    return deleted_count


def _sync_single_game(
    service: Any,
    calendar_id: str,
    game: dict[str, Any],
    event_date: date,
    existing_events_by_uid: dict[str, dict[str, Any]],
    gmail_service: Any,
    notify_email: str | None,
    calendar_name: str,
) -> tuple[int, int, int]:
    """Sync one game event into Google Calendar and send notification email; returns (created, updated, unchanged)."""
    uid = generate_game_uid(game)
    event_body = _build_google_event_payload(game, event_date, uid)
    existing_event = existing_events_by_uid.get(uid)

    should_notify_update = _should_notify_event_update(existing_event, event_body)
    was_cancelled = bool(existing_event and existing_event.get("status") == "cancelled")

    try:
        result = _upsert_single_event(service, calendar_id, event_body, existing_event)
    except HttpError as error:
        logger.warning(f"Error syncing event '{event_body.get('summary')}': {error}")
        return 0, 0, 0

    if result is None:
        return 0, 0, 1

    time.sleep(0.1)
    if result is True and not was_cancelled:
        if should_notify_update:
            send_event_notification_email(
                gmail_service=gmail_service,
                notify_email=notify_email,
                action="updated",
                event_data=event_body,
                calendar_name=calendar_name,
                previous_event=existing_event,
            )
        return 0, 1, 0

    send_event_notification_email(
        gmail_service=gmail_service,
        notify_email=notify_email,
        action="created",
        event_data=event_body,
        calendar_name=calendar_name,
    )
    return (0, 1, 0) if result is True else (1, 0, 0)


def sync_games_to_google_calendar(
    service: Any,
    calendar_id: str,
    games: list[dict[str, Any]],
    gmail_service: Any = None,
    notify_email: str | None = None,
    calendar_name: str | None = None,
) -> None:
    """
    Sync games to the Google Calendar, creating, updating, or deleting events
    and sending direct Gmail API notifications for changes.
    """
    valid_game_entries: list[tuple[dict[str, Any], date]] = []
    for game in games:
        event_date = _get_event_date(game.get("release_date"))
        if event_date is not None:
            valid_game_entries.append((game, event_date))

    resolved_calendar_name = calendar_name or CALENDAR_NAME
    resolved_notify_email = (
        notify_email or os.getenv("GOOGLE_SHARE_EMAIL") or os.getenv("GOOGLE_CALENDAR_SHARE_EMAIL")
    )

    logger.info(
        f"Syncing {len(valid_game_entries)} game release events to Google Calendar '{calendar_id}'..."
    )

    existing_events_by_uid = _fetch_existing_events_by_uid(service, calendar_id)

    count_deleted = 0
    if valid_game_entries:
        current_uids = {generate_game_uid(game) for game, _ in valid_game_entries}
        count_deleted = _delete_removed_events(
            service=service,
            calendar_id=calendar_id,
            current_uids=current_uids,
            existing_events=list(existing_events_by_uid.values()),
            gmail_service=gmail_service,
            notify_email=resolved_notify_email,
            calendar_name=resolved_calendar_name,
        )
    else:
        logger.info("Skipping deletion of removed events because 0 valid games were fetched.")

    count_created = 0
    count_updated = 0
    count_unchanged = 0

    for game, event_date in valid_game_entries:
        created_delta, updated_delta, unchanged_delta = _sync_single_game(
            service=service,
            calendar_id=calendar_id,
            game=game,
            event_date=event_date,
            existing_events_by_uid=existing_events_by_uid,
            gmail_service=gmail_service,
            notify_email=resolved_notify_email,
            calendar_name=resolved_calendar_name,
        )
        count_created += created_delta
        count_updated += updated_delta
        count_unchanged += unchanged_delta

    logger.info(
        f"Google Calendar Sync finished: {count_created} created, {count_updated} updated, "
        f"{count_deleted} deleted, {count_unchanged} unchanged."
    )
