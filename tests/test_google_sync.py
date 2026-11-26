"""
Unit tests for google_sync.py
"""

import base64
import email
import email.policy
import os
import webbrowser
from datetime import date, datetime
from unittest.mock import MagicMock, mock_open, patch

import pytest
from googleapiclient.errors import HttpError

from google_sync import (
    CALENDAR_NAME,
    _build_google_event_payload,
    _build_notification_email_message,
    _collect_event_changes,
    _delete_removed_events,
    _event_needs_update,
    _fetch_existing_events_by_uid,
    _format_event_when,
    _get_event_date,
    _get_oauth_credentials,
    _normalize_datetime_str,
    _save_token_file,
    _should_notify_event_update,
    _upsert_single_event,
    disable_calendar_other_notifications,
    get_gmail_service,
    get_google_calendar_service,
    get_or_create_calendar,
    send_event_notification_email,
    share_calendar_with_email,
    sync_games_to_google_calendar,
)
from ical_builder import generate_game_uid


def test_get_event_date():
    d = date(2026, 5, 1)
    dt = datetime(2026, 5, 1, 10, 0)
    assert _get_event_date(dt) == date(2026, 5, 1)
    assert _get_event_date(d) == date(2026, 5, 1)
    assert _get_event_date("invalid") is None
    assert _get_event_date(None) is None


def test_save_token_file():
    mock_creds = MagicMock()
    mock_creds.to_json.return_value = '{"token": "abc"}'
    m_open = mock_open()
    with patch("builtins.open", m_open):
        _save_token_file("token.json", mock_creds)
    m_open.assert_called_once_with("token.json", "w", encoding="utf-8")
    m_open().write.assert_called_once_with('{"token": "abc"}')

    # Non-string JSON is ignored
    mock_creds_non_str = MagicMock()
    mock_creds_non_str.to_json.return_value = None
    with patch("builtins.open", mock_open()) as m_open_2:
        _save_token_file("token.json", mock_creds_non_str)
    m_open_2.assert_not_called()


def test_build_google_event_payload():
    game = {
        "title": "Metroid Prime 4",
        "url": "https://backloggd.com/games/metroid/",
        "platforms": ["Switch"],
        "release_date_raw": "2026",
    }
    event_date = date(2026, 12, 31)
    payload = _build_google_event_payload(game, event_date, "uid123")

    assert payload["summary"] == "🎮 Metroid Prime 4"
    assert payload["iCalUID"] == "uid123"
    assert payload["status"] == "confirmed"
    assert payload["start"]["date"] == "2026-12-31"
    assert payload["end"]["date"] == "2027-01-01"
    assert "Platforms: Switch" in payload["description"]
    assert payload["source"]["url"] == "https://backloggd.com/games/metroid/"


def test_build_google_event_payload_with_extra():
    game = {
        "title": "Elden Ring: Shadow of the Erdtree",
        "url": "https://backloggd.com/games/shadow-of-the-erdtree/",
        "release_date_raw": "Jun 21, 2024",
        "category_type": "extra",
    }
    event_date = date(2024, 6, 21)
    payload = _build_google_event_payload(game, event_date, "uid456")

    assert payload["summary"] == "🎮 Elden Ring: Shadow of the Erdtree"
    assert "Type: DLC / Extra" in payload["description"]


@patch("google_sync.os.path.exists")
@patch("google_sync.Credentials")
def test_get_oauth_credentials_valid_token(mock_creds_cls, mock_exists):
    mock_exists.side_effect = lambda p: p == "token.json"
    mock_creds = MagicMock()
    mock_creds.valid = True
    mock_creds_cls.from_authorized_user_file.return_value = mock_creds

    creds = _get_oauth_credentials("credentials.json", "token.json")
    assert creds == mock_creds


@patch("google_sync.os.path.exists")
@patch("google_sync.Credentials")
def test_get_oauth_credentials_refresh_token(mock_creds_cls, mock_exists):
    mock_exists.side_effect = lambda p: p == "token.json"
    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.expired = True
    mock_creds.refresh_token = "refresh_token"
    mock_creds.to_json.return_value = '{"token": "refreshed"}'
    mock_creds_cls.from_authorized_user_file.return_value = mock_creds

    with patch("builtins.open", mock_open()) as m_open:
        creds = _get_oauth_credentials("credentials.json", "token.json")
    assert creds == mock_creds
    mock_creds.refresh.assert_called_once()
    m_open.assert_called_once_with("token.json", "w", encoding="utf-8")


@patch("google_sync.os.path.exists")
def test_get_oauth_credentials_file_not_found(mock_exists):
    mock_exists.return_value = False
    with pytest.raises(FileNotFoundError):
        _get_oauth_credentials("credentials.json", "token.json")


@patch("google_sync.os.path.exists")
@patch("google_sync.build")
@patch("google_sync.service_account.Credentials")
def test_get_google_calendar_service_account(mock_sa_creds, mock_build, mock_exists):
    mock_exists.side_effect = lambda p: p == "service_account.json"
    mock_sa_creds.from_service_account_file.return_value = MagicMock()

    _ = get_google_calendar_service()
    mock_build.assert_called_once_with(
        "calendar",
        "v3",
        credentials=mock_sa_creds.from_service_account_file.return_value,
        cache_discovery=False,
    )


@patch("google_sync.os.path.exists")
@patch("google_sync.build")
@patch("google_sync._get_oauth_credentials")
def test_get_google_calendar_service_oauth(mock_get_oauth, mock_build, mock_exists):
    mock_exists.return_value = False
    mock_get_oauth.return_value = MagicMock()

    _ = get_google_calendar_service()
    mock_build.assert_called_once()


def test_get_gmail_service_success_and_failure():
    mock_creds = MagicMock()
    mock_build = MagicMock()
    with (
        patch("google_sync._get_oauth_credentials", return_value=mock_creds),
        patch("google_sync.build", return_value=mock_build) as mock_b,
    ):
        svc = get_gmail_service("credentials.json", "token.json")
        assert svc == mock_build
        mock_b.assert_called_once_with("gmail", "v1", credentials=mock_creds, cache_discovery=False)

    with patch("google_sync._get_oauth_credentials", side_effect=FileNotFoundError("no creds")):
        assert get_gmail_service("credentials.json", "token.json") is None


def test_disable_calendar_other_notifications():
    # No calendar IDs or missing files -> no-op
    with patch("google_sync.os.path.exists", return_value=False):
        disable_calendar_other_notifications(["cal1"])
        disable_calendar_other_notifications([])

    mock_creds = MagicMock()
    mock_user_cal = MagicMock()
    mock_user_cal.calendarList().get().execute.side_effect = [
        {
            "notificationSettings": {
                "notifications": [
                    {"type": "eventCreation", "method": "email"},
                    {"type": "eventChange", "method": "email"},
                    {"type": "eventCancellation", "method": "email"},
                    {"type": "eventResponse", "method": "email"},
                    {"type": "agenda", "method": "email"},
                ]
            }
        },
        {
            "notificationSettings": {
                "notifications": [
                    {"type": "agenda", "method": "email"},
                ]
            }
        },
        {
            "notificationSettings": {
                "notifications": [
                    {"type": "eventCreation", "method": "email"},
                ]
            }
        },
        {},
    ]
    with (
        patch("google_sync.os.path.exists", return_value=True),
        patch("google_sync._get_oauth_credentials", return_value=mock_creds),
        patch("google_sync.build", return_value=mock_user_cal),
    ):
        disable_calendar_other_notifications(["", "cal1", "cal2", "cal3", "cal4"])
        assert mock_user_cal.calendarList().get.call_count == 5  # 1 setup + 4 calendars
        assert mock_user_cal.calendarList().patch.call_count == 2
        mock_user_cal.calendarList().patch.assert_any_call(
            calendarId="cal1",
            body={
                "notificationSettings": {"notifications": [{"type": "agenda", "method": "email"}]}
            },
        )
        mock_user_cal.calendarList().patch.assert_any_call(
            calendarId="cal3",
            body={"notificationSettings": {"notifications": []}},
        )

    # Outer exception handled gracefully
    with (
        patch("google_sync.os.path.exists", return_value=True),
        patch("google_sync._get_oauth_credentials", side_effect=Exception("Auth error")),
    ):
        disable_calendar_other_notifications(["cal1"])


def test_format_event_when_and_build_notification_email():
    # All-day valid & invalid date
    assert _format_event_when({"start": {"date": "2026-11-15"}}) == "Sun, Nov 15, 2026 (All day)"
    assert _format_event_when({"start": {"date": "invalid-date"}}) == "invalid-date (All day)"

    # Timed event with start & end, start only, invalid datetime, and unknown
    assert (
        _format_event_when(
            {
                "start": {"dateTime": "2026-11-15T02:00:00+00:00"},
                "end": {"dateTime": "2026-11-15T03:00:00+00:00"},
            }
        )
        == "Sun, Nov 15, 2026, 02:00 - 03:00 UTC"
    )
    assert (
        _format_event_when({"start": {"dateTime": "2026-11-15T02:00:00+00:00"}})
        == "Sun, Nov 15, 2026, 02:00 UTC"
    )
    assert _format_event_when({"start": {"dateTime": "bad-dt"}}) == "bad-dt"
    assert _format_event_when({}) == "Unknown date"

    # Collect changes returns empty when no previous event
    assert _collect_event_changes(None, "Game", "Sun, Nov 15, 2026 (All day)", "Desc") == ([], [])

    # Build notification message with previous_event changes
    raw_msg = _build_notification_email_message(
        action="updated",
        event_data={
            "summary": "🎮 Hollow Knight: Silksong",
            "description": "Game: Hollow Knight: Silksong\nRelease Date: Nov 15, 2026",
            "start": {"date": "2026-11-15"},
            "end": {"date": "2026-11-16"},
        },
        notify_email="user@example.com",
        calendar_name=CALENDAR_NAME,
        previous_event={
            "summary": "🎮 Silksong",
            "description": "Game: Silksong\r\nRelease Date: 2026",
            "start": {"date": "2026-12-31"},
            "end": {"date": "2027-01-01"},
        },
    )
    assert "raw" in raw_msg
    decoded = email.message_from_bytes(
        base64.urlsafe_b64decode(raw_msg["raw"]), policy=email.policy.default
    )
    assert decoded["To"] == "user@example.com"
    assert decoded["From"] == "user@example.com"
    assert (
        "Changed event: 🎮 Hollow Knight: Silksong @ Sun, Nov 15, 2026 (All day)"
        in decoded["Subject"]
    )
    content_types = [part.get_content_type() for part in decoded.walk()]
    assert "text/plain" in content_types
    assert "text/html" in content_types
    assert "text/calendar" not in content_types

    plain_part = next(p for p in decoded.walk() if p.get_content_type() == "text/plain")
    plain_text = plain_part.get_payload(decode=True).decode("utf-8")
    assert "What Changed:" in plain_text
    assert "- Title: 🎮 Silksong -> 🎮 Hollow Knight: Silksong" in plain_text
    assert "- When: Thu, Dec 31, 2026 (All day) -> Sun, Nov 15, 2026 (All day)" in plain_text
    assert "- Description:" in plain_text

    # Deleted action without previous_event or description
    del_msg = _build_notification_email_message(
        action="deleted",
        event_data={"summary": "🎮 Removed Game", "start": {"date": "2026-11-20"}},
        notify_email="user@example.com",
    )
    decoded_del = email.message_from_bytes(
        base64.urlsafe_b64decode(del_msg["raw"]), policy=email.policy.default
    )
    assert decoded_del["Subject"].startswith("Canceled event: 🎮 Removed Game")


def test_send_event_notification_email():
    # Missing service or email returns False
    assert send_event_notification_email(None, "user@example.com", "created", {}) is False
    assert send_event_notification_email(MagicMock(), None, "created", {}) is False

    mock_gmail = MagicMock()
    with patch("google_sync.time.sleep") as mock_sleep:
        assert (
            send_event_notification_email(
                mock_gmail,
                "user@example.com",
                "created",
                {"summary": "🎮 Game 1", "start": {"date": "2026-11-15"}},
            )
            is True
        )
        mock_sleep.assert_called_once_with(0.5)
    mock_gmail.users().messages().send.assert_called_once()

    # Exception during send returns False
    mock_gmail.users().messages().send.side_effect = Exception("Send error")
    assert (
        send_event_notification_email(
            mock_gmail,
            "user@example.com",
            "created",
            {"summary": "🎮 Game 1"},
        )
        is False
    )


def test_share_calendar_with_email():
    with patch.dict(os.environ, {}, clear=True):
        mock_service = MagicMock()
        acl_mock = mock_service.acl.return_value

        # Case 1: No email provided and no env var set
        share_calendar_with_email(mock_service, "cal123", None)
        acl_mock.list.assert_not_called()

        # Case 2: Email already present in ACL
        acl_mock.list.return_value.execute.return_value = {
            "items": [{"scope": {"value": "test@example.com"}}]
        }
        share_calendar_with_email(mock_service, "cal123", "test@example.com")
        acl_mock.insert.assert_not_called()

        # Case 3: Email not present in ACL -> insert rule
        acl_mock.list.return_value.execute.return_value = {"items": []}
        share_calendar_with_email(mock_service, "cal123", "new@example.com")
        acl_mock.insert.assert_called_once()


def test_share_calendar_with_email_httperror():
    with patch.dict(os.environ, {}, clear=True):
        mock_service = MagicMock()
        acl_mock = mock_service.acl.return_value
        acl_mock.list.return_value.execute.return_value = {"items": []}

        # HttpError duplicate warning caught silently
        err_resp = MagicMock(status=409)
        acl_mock.insert.return_value.execute.side_effect = HttpError(
            resp=err_resp, content=b"alreadyExists"
        )
        share_calendar_with_email(mock_service, "cal123", "error@example.com")


def test_get_or_create_calendar_env_override():
    with patch.dict(os.environ, {"GOOGLE_CALENDAR_ID": "custom_cal_id"}):
        res = get_or_create_calendar(MagicMock())
        assert res == "custom_cal_id"


def test_get_or_create_calendar_existing():
    mock_service = MagicMock()
    mock_service.calendarList.return_value.list.return_value.execute.return_value = {
        "items": [{"summary": "Backloggd Wishlist Releases", "id": "cal_existing_id"}]
    }

    cal_id = get_or_create_calendar(mock_service)
    assert cal_id == "cal_existing_id"


@patch("google_sync.share_calendar_with_email")
def test_get_or_create_calendar_create_new(mock_share):
    mock_service = MagicMock()
    mock_service.calendarList.return_value.list.return_value.execute.return_value = {"items": []}
    mock_service.calendars.return_value.insert.return_value.execute.return_value = {
        "id": "cal_new_id"
    }

    cal_id = get_or_create_calendar(mock_service)
    assert cal_id == "cal_new_id"
    mock_share.assert_called_once_with(mock_service, "cal_new_id")


def test_fetch_existing_events_by_uid():
    mock_service = MagicMock()
    mock_events = mock_service.events.return_value

    mock_events.list.return_value.execute.side_effect = [
        {
            "items": [{"id": "evt1", "iCalUID": "uid1"}],
            "nextPageToken": "token2",
        },
        {
            "items": [{"id": "evt2", "iCalUID": "uid2"}],
        },
    ]

    res = _fetch_existing_events_by_uid(mock_service, "cal123")
    assert len(res) == 2
    assert "uid1" in res
    assert "uid2" in res
    mock_events.list.assert_called_with(
        calendarId="cal123",
        pageToken="token2",
        singleEvents=False,
        showDeleted=True,
        maxResults=2500,
    )


def test_upsert_single_event_patch():
    mock_service = MagicMock()
    event_body = {"summary": "Test"}

    is_updated = _upsert_single_event(mock_service, "cal123", event_body, {"id": "existing_id"})
    assert is_updated is True
    mock_service.events.return_value.patch.assert_called_once_with(
        calendarId="cal123",
        eventId="existing_id",
        body={"summary": "Test", "status": "confirmed"},
        sendUpdates="none",
    )


def test_upsert_single_event_patch_clears_leftover_attendees():
    mock_service = MagicMock()
    event_body = {
        "summary": "🎮 Test",
        "description": "Game: Test",
        "start": {"date": "2026-12-31"},
        "end": {"date": "2027-01-01"},
        "status": "confirmed",
    }
    existing_event = {
        "id": "existing_id",
        "summary": "🎮 Test",
        "description": "Game: Test",
        "start": {"date": "2026-12-31"},
        "end": {"date": "2027-01-01"},
        "status": "confirmed",
        "attendees": [{"email": "user@example.com"}],
    }

    is_updated = _upsert_single_event(mock_service, "cal123", event_body, existing_event)
    assert is_updated is True
    mock_service.events.return_value.patch.assert_called_once_with(
        calendarId="cal123",
        eventId="existing_id",
        body={**event_body, "attendees": []},
        sendUpdates="none",
    )


@patch("google_sync.os.path.exists")
@patch("google_sync.Credentials")
def test_get_oauth_credentials_token_corrupted(mock_creds_cls, mock_exists):
    # token.json exists but throws exception on load, fallback to credentials.json
    mock_exists.side_effect = lambda p: p in ("token.json", "credentials.json")
    mock_creds_cls.from_authorized_user_file.side_effect = Exception("Corrupt token JSON")

    with (
        patch("google_sync.InstalledAppFlow") as mock_flow_cls,
        patch("builtins.open", MagicMock()),
    ):
        mock_flow = MagicMock()
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow
        mock_new_creds = MagicMock()
        mock_new_creds.to_json.return_value = '{"token": "xyz"}'
        mock_flow.run_local_server.return_value = mock_new_creds

        creds = _get_oauth_credentials("credentials.json", "token.json")
        assert creds == mock_new_creds


@patch("google_sync.os.path.exists")
@patch("google_sync.Credentials")
def test_get_oauth_credentials_refresh_fails_and_headless_fallback(mock_creds_cls, mock_exists):
    mock_exists.side_effect = lambda p: p in ("token.json", "credentials.json")
    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.expired = True
    mock_creds.refresh_token = "refresh_token"
    mock_creds.refresh.side_effect = Exception("Refresh failed")
    mock_creds_cls.from_authorized_user_file.return_value = mock_creds

    with (
        patch("google_sync.InstalledAppFlow") as mock_flow_cls,
        patch("builtins.open", mock_open()),
    ):
        mock_flow = MagicMock()
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow
        mock_new_creds = MagicMock()
        mock_new_creds.to_json.return_value = '{"token": "xyz"}'
        mock_flow.run_local_server.side_effect = [
            webbrowser.Error("could not locate runnable browser"),
            mock_new_creds,
        ]

        creds = _get_oauth_credentials("credentials.json", "token.json")
        assert creds == mock_new_creds
        assert mock_flow.run_local_server.call_count == 2


@patch("google_sync.os.path.exists")
def test_get_oauth_credentials_interactive_flow(mock_exists):
    # token.json does not exist, credentials.json exists
    mock_exists.side_effect = lambda p: p == "credentials.json"

    with (
        patch("google_sync.InstalledAppFlow") as mock_flow_cls,
        patch("builtins.open", mock_open()),
    ):
        mock_flow = MagicMock()
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow
        mock_new_creds = MagicMock()
        mock_new_creds.to_json.return_value = '{"token": "xyz"}'
        mock_flow.run_local_server.return_value = mock_new_creds

        creds = _get_oauth_credentials("credentials.json", "token.json")
        assert creds == mock_new_creds


def test_share_calendar_with_email_httperror_logged():
    with patch.dict(os.environ, {}, clear=True):
        mock_service = MagicMock()
        acl_mock = mock_service.acl.return_value
        acl_mock.list.return_value.execute.return_value = {"items": []}

        # HttpError with non-duplicate error (e.g. 500 internal error)
        err_resp = MagicMock(status=500)
        acl_mock.insert.return_value.execute.side_effect = HttpError(
            resp=err_resp, content=b"Server Error"
        )
        share_calendar_with_email(mock_service, "cal123", "error@example.com")


def test_upsert_single_event_insert():
    mock_service = MagicMock()
    event_body = {"summary": "Test"}

    is_updated = _upsert_single_event(mock_service, "cal123", event_body, None)
    assert is_updated is False
    mock_service.events.return_value.insert.assert_called_once_with(
        calendarId="cal123",
        body={"summary": "Test", "status": "confirmed"},
        sendUpdates="none",
    )


def test_upsert_single_event_insert_duplicate_409():
    mock_service = MagicMock()
    event_body = {"summary": "Test", "iCalUID": "uid123"}
    err_resp = MagicMock(status=409)
    mock_service.events.return_value.insert.return_value.execute.side_effect = HttpError(
        resp=err_resp, content=b"alreadyExists"
    )
    mock_service.events.return_value.list.return_value.execute.return_value = {
        "items": [{"id": "recovered_id"}]
    }

    is_updated = _upsert_single_event(mock_service, "cal123", event_body, None)
    assert is_updated is True
    mock_service.events.return_value.patch.assert_called_once_with(
        calendarId="cal123",
        eventId="recovered_id",
        body={
            "summary": "Test",
            "iCalUID": "uid123",
            "status": "confirmed",
            "attendees": [],
        },
        sendUpdates="none",
    )


def test_upsert_single_event_insert_error():
    mock_service = MagicMock()
    event_body = {"summary": "Test", "iCalUID": "uid123"}
    err_resp = MagicMock(status=500)
    mock_service.events.return_value.insert.return_value.execute.side_effect = HttpError(
        resp=err_resp, content=b"Internal Server Error"
    )

    with pytest.raises(HttpError):
        _upsert_single_event(mock_service, "cal123", event_body, None)


def test_delete_removed_events():
    mock_service = MagicMock()
    mock_gmail = MagicMock()
    existing_events = [
        {
            "id": "ev1",
            "iCalUID": "backloggd-111@backloggd-calendar",
            "summary": "🎮 Game 1",
            "status": "confirmed",
        },
        {
            "id": "ev2",
            "iCalUID": "backloggd-222@backloggd-calendar",
            "summary": "🎮 Game 2",
            "status": "confirmed",
            "start": {"date": "2026-12-01"},
        },
        {
            "id": "ev3",
            "iCalUID": "other-uid-3",
            "summary": "Other",
            "status": "confirmed",
        },
        {
            "id": "ev4",
            "iCalUID": "backloggd-444@backloggd-calendar",
            "summary": "Cancelled",
            "status": "cancelled",
        },
    ]

    current_uids = {"backloggd-111@backloggd-calendar"}
    with patch("google_sync.time.sleep"):
        deleted = _delete_removed_events(
            mock_service,
            "cal_id",
            current_uids,
            existing_events,
            gmail_service=mock_gmail,
            notify_email="user@example.com",
            calendar_name=CALENDAR_NAME,
        )
    assert deleted == 1
    mock_service.events().delete.assert_called_once_with(
        calendarId="cal_id", eventId="ev2", sendUpdates="none"
    )
    mock_gmail.users().messages().send.assert_called_once()


def test_delete_removed_events_httperror():
    mock_service = MagicMock()
    mock_gmail = MagicMock()
    existing_events = [
        {
            "id": "ev1",
            "iCalUID": "backloggd-111@backloggd-calendar",
            "summary": "🎮 Game 1",
        }
    ]
    resp = MagicMock(status=410, reason="Gone")
    mock_service.events().delete().execute.side_effect = HttpError(resp, b"Resource deleted")

    with patch("google_sync.time.sleep"):
        deleted = _delete_removed_events(
            mock_service,
            "cal_id",
            current_uids=set(),
            existing_events=existing_events,
            gmail_service=mock_gmail,
            notify_email="user@example.com",
        )
    assert deleted == 0
    mock_gmail.users().messages().send.assert_not_called()


@patch("google_sync.send_event_notification_email")
@patch("google_sync.time.sleep")
@patch("google_sync._fetch_existing_events_by_uid")
def test_sync_games_to_google_calendar_inserts_updates_revives_and_deletes(
    mock_fetch_existing, mock_sleep, mock_notify
):
    mock_service = MagicMock()
    mock_gmail = MagicMock()
    game1 = {
        "title": "Game 1",
        "url": "https://backloggd.com/games/g1/",
        "release_date": date(2026, 12, 1),
    }
    game2 = {
        "title": "Game 2",
        "url": "https://backloggd.com/games/g2/",
        "release_date": date(2026, 12, 2),
    }
    game3 = {
        "title": "Game 3",
        "release_date": None,  # should be skipped
    }
    game4 = {
        "title": "Game 4",
        "url": "https://backloggd.com/games/g4/",
        "release_date": date(2026, 12, 4),
    }
    uid1 = generate_game_uid(game1)
    uid4 = generate_game_uid(game4)

    # Game 1 exists and changed; Game 4 was cancelled and is revived; Game 5 was removed
    mock_fetch_existing.return_value = {
        uid1: {
            "id": "evt_existing_id",
            "iCalUID": uid1,
            "summary": "🎮 Game 1 Old",
            "start": {"date": "2026-12-01"},
            "end": {"date": "2026-12-02"},
            "status": "confirmed",
        },
        uid4: {
            "id": "evt_cancelled_id",
            "iCalUID": uid4,
            "summary": "🎮 Game 4",
            "status": "cancelled",
        },
        "backloggd-removed99999@backloggd-calendar": {
            "id": "evt_removed_id",
            "iCalUID": "backloggd-removed99999@backloggd-calendar",
            "summary": "🎮 Removed Game",
            "status": "confirmed",
        },
    }

    games = [game1, game2, game3, game4]

    with patch("google_sync._upsert_single_event") as mock_upsert:
        # Game 1 updated, Game 2 created, Game 4 revived (patched)
        mock_upsert.side_effect = [True, False, True]
        sync_games_to_google_calendar(
            mock_service,
            "cal123",
            games,
            gmail_service=mock_gmail,
            notify_email="user@example.com",
            calendar_name=CALENDAR_NAME,
        )
        assert mock_upsert.call_count == 3

    # Delete called for removed game, notifications sent for deleted + updated + created + revived
    mock_service.events().delete.assert_called_once_with(
        calendarId="cal123", eventId="evt_removed_id", sendUpdates="none"
    )
    actions = [call.kwargs["action"] for call in mock_notify.call_args_list]
    assert actions == ["deleted", "updated", "created", "created"]


@patch("google_sync.send_event_notification_email")
@patch("google_sync.time.sleep")
@patch("google_sync._fetch_existing_events_by_uid")
def test_sync_games_skips_delete_when_no_valid_games(mock_fetch_existing, mock_sleep, mock_notify):
    mock_service = MagicMock()
    mock_fetch_existing.return_value = {
        "backloggd-existing@backloggd-calendar": {
            "id": "evt_1",
            "iCalUID": "backloggd-existing@backloggd-calendar",
            "summary": "🎮 Existing Game",
            "status": "confirmed",
        }
    }

    sync_games_to_google_calendar(mock_service, "cal123", [])
    mock_service.events().delete.assert_not_called()
    mock_notify.assert_not_called()


@patch("google_sync.send_event_notification_email")
@patch("google_sync.time.sleep")
@patch("google_sync._fetch_existing_events_by_uid")
def test_sync_games_handles_httperror_and_attendee_cleanup(
    mock_fetch_existing, mock_sleep, mock_notify
):
    mock_service = MagicMock()
    game1 = {
        "title": "Game 1",
        "url": "https://backloggd.com/games/g1/",
        "release_date": date(2026, 12, 1),
    }
    game2 = {
        "title": "Game 2",
        "url": "https://backloggd.com/games/g2/",
        "release_date": date(2026, 12, 2),
    }
    uid1 = generate_game_uid(game1)

    mock_fetch_existing.return_value = {
        uid1: {
            "id": "evt_1",
            "iCalUID": uid1,
            "summary": "🎮 Game 1",
            "description": "Game: Game 1\nRelease Date: 2026-12-01\nBackloggd: https://backloggd.com/games/g1/",
            "start": {"date": "2026-12-01"},
            "end": {"date": "2026-12-02"},
            "status": "confirmed",
            "attendees": [{"email": "user@example.com"}],
        }
    }
    games = [game1, game2]

    err = HttpError(MagicMock(status=500), b"Server Error")
    with patch("google_sync._upsert_single_event", side_effect=[True, err]):
        sync_games_to_google_calendar(
            mock_service,
            "cal123",
            games,
            gmail_service=MagicMock(),
            notify_email="user@example.com",
        )
    # Attendee-only cleanup should not send a noisy 'Changed event' email, and HttpError is handled
    mock_notify.assert_not_called()


def test_event_needs_update_detects_changes():
    existing = {
        "status": "confirmed",
        "summary": "🎮 Game",
        "description": "Game: Game\r\nRelease Date: 2026",
        "start": {"date": "2026-12-31"},
        "end": {"date": "2027-01-01"},
        "source": {"url": "https://backloggd.com/games/g"},
    }
    new_event = {
        "status": "confirmed",
        "summary": "🎮 Game",
        "description": "Game: Game\nRelease Date: 2026",
        "start": {"date": "2026-12-31"},
        "end": {"date": "2027-01-01"},
        "source": {"url": "https://backloggd.com/games/g"},
    }
    # Unchanged (CRLF vs LF normalized)
    assert not _event_needs_update(existing, new_event)

    # Cancelled status change
    assert _event_needs_update({**existing, "status": "cancelled"}, new_event)

    # Leftover attendees
    assert _event_needs_update({**existing, "attendees": [{"email": "u@e.com"}]}, new_event)

    # Summary change
    diff_summary = {**new_event, "summary": "🎮 Game Remaster"}
    assert _event_needs_update(existing, diff_summary)

    # Description change
    diff_desc = {**new_event, "description": "Game: Game\nRelease Date: 2027"}
    assert _event_needs_update(existing, diff_desc)

    # Start date change
    diff_start = {**new_event, "start": {"date": "2026-10-01"}}
    assert _event_needs_update(existing, diff_start)

    # End date change
    diff_end = {**new_event, "end": {"date": "2026-10-02"}}
    assert _event_needs_update(existing, diff_end)

    # Source URL change
    diff_source = {**new_event, "source": {"url": "https://backloggd.com/games/diff"}}
    assert _event_needs_update(existing, diff_source)

    # Missing fields in existing
    assert _event_needs_update({}, new_event)

    # Timed event with RFC3339 'Z' vs ISO '+00:00' recognized as unchanged
    assert not _event_needs_update(
        {
            "status": "confirmed",
            "summary": "🎮 Game",
            "description": "Desc",
            "start": {"dateTime": "2026-11-15T02:00:00Z"},
            "end": {"dateTime": "2026-11-15T03:00:00Z"},
        },
        {
            "status": "confirmed",
            "summary": "🎮 Game",
            "description": "Desc",
            "start": {"dateTime": "2026-11-15T02:00:00+00:00"},
            "end": {"dateTime": "2026-11-15T03:00:00+00:00"},
        },
    )
    assert _normalize_datetime_str("not-a-dt") == "not-a-dt"
    assert _should_notify_event_update(None, new_event) is True
    assert _should_notify_event_update(existing, diff_source) is False


def test_upsert_single_event_unchanged():
    mock_service = MagicMock()
    event_body = {
        "summary": "🎮 Test",
        "description": "Game: Test\nRelease Date: 2026",
        "start": {"date": "2026-12-31"},
        "end": {"date": "2027-01-01"},
        "source": {"url": "https://backloggd.com/games/test"},
        "iCalUID": "uid123",
    }
    existing_event = {
        "id": "existing_id",
        "summary": "🎮 Test",
        "description": "Game: Test\nRelease Date: 2026",
        "start": {"date": "2026-12-31"},
        "end": {"date": "2027-01-01"},
        "source": {"url": "https://backloggd.com/games/test"},
    }

    res = _upsert_single_event(mock_service, "cal123", event_body, existing_event)
    assert res is None
    mock_service.events.return_value.patch.assert_not_called()


@patch("google_sync.time.sleep")
@patch("google_sync._fetch_existing_events_by_uid")
def test_sync_games_to_google_calendar_handles_unchanged(mock_fetch_existing, mock_sleep):
    mock_service = MagicMock()
    mock_fetch_existing.return_value = {}

    games = [
        {
            "title": "Game 1",
            "url": "https://backloggd.com/games/g1/",
            "release_date": date(2026, 12, 1),
        },
    ]

    with patch("google_sync._upsert_single_event", return_value=None):
        sync_games_to_google_calendar(mock_service, "cal123", games)
        mock_sleep.assert_not_called()
