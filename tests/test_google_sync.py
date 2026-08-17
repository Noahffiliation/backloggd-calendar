"""
Unit tests for google_sync.py
"""

import os
from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest
from googleapiclient.errors import HttpError

from google_sync import (
    _build_google_event_payload,
    _fetch_existing_events_by_uid,
    _get_event_date,
    _get_oauth_credentials,
    _upsert_single_event,
    get_google_calendar_service,
    get_or_create_calendar,
    share_calendar_with_email,
    sync_games_to_google_calendar,
)


def test_get_event_date():
    d = date(2026, 5, 1)
    dt = datetime(2026, 5, 1, 10, 0)
    assert _get_event_date(dt) == date(2026, 5, 1)
    assert _get_event_date(d) == date(2026, 5, 1)
    assert _get_event_date("invalid") is None
    assert _get_event_date(None) is None


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
    assert payload["start"]["date"] == "2026-12-31"
    assert payload["end"]["date"] == "2027-01-01"
    assert "Platforms: Switch" in payload["description"]
    assert payload["source"]["url"] == "https://backloggd.com/games/metroid/"


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
    mock_creds_cls.from_authorized_user_file.return_value = mock_creds

    creds = _get_oauth_credentials("credentials.json", "token.json")
    assert creds == mock_creds
    mock_creds.refresh.assert_called_once()


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
    mock_build.assert_called_once_with("calendar", "v3", credentials=mock_sa_creds.from_service_account_file.return_value)


@patch("google_sync.os.path.exists")
@patch("google_sync.build")
@patch("google_sync._get_oauth_credentials")
def test_get_google_calendar_service_oauth(mock_get_oauth, mock_build, mock_exists):
    mock_exists.return_value = False
    mock_get_oauth.return_value = MagicMock()

    _ = get_google_calendar_service()
    mock_build.assert_called_once()


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
        acl_mock.insert.return_value.execute.side_effect = HttpError(resp=err_resp, content=b"alreadyExists")
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
    mock_service.calendars.return_value.insert.return_value.execute.return_value = {"id": "cal_new_id"}

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


def test_upsert_single_event_patch():
    mock_service = MagicMock()
    event_body = {"summary": "Test"}

    is_updated = _upsert_single_event(mock_service, "cal123", event_body, {"id": "existing_id"})
    assert is_updated is True
    mock_service.events.return_value.patch.assert_called_once_with(
        calendarId="cal123", eventId="existing_id", body=event_body
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
def test_get_oauth_credentials_refresh_fails(mock_creds_cls, mock_exists):
    mock_exists.side_effect = lambda p: p in ("token.json", "credentials.json")
    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.expired = True
    mock_creds.refresh_token = "refresh_token"
    mock_creds.refresh.side_effect = Exception("Refresh failed")
    mock_creds_cls.from_authorized_user_file.return_value = mock_creds

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
def test_get_oauth_credentials_interactive_flow(mock_exists):
    # token.json does not exist, credentials.json exists
    mock_exists.side_effect = lambda p: p == "credentials.json"

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


def test_share_calendar_with_email_httperror_logged():
    with patch.dict(os.environ, {}, clear=True):
        mock_service = MagicMock()
        acl_mock = mock_service.acl.return_value
        acl_mock.list.return_value.execute.return_value = {"items": []}

        # HttpError with non-duplicate error (e.g. 500 internal error)
        err_resp = MagicMock(status=500)
        acl_mock.insert.return_value.execute.side_effect = HttpError(resp=err_resp, content=b"Server Error")
        share_calendar_with_email(mock_service, "cal123", "error@example.com")


def test_upsert_single_event_insert():
    mock_service = MagicMock()
    event_body = {"summary": "Test"}

    is_updated = _upsert_single_event(mock_service, "cal123", event_body, None)
    assert is_updated is False
    mock_service.events.return_value.insert.assert_called_once_with(
        calendarId="cal123", body=event_body
    )


@patch("google_sync.time.sleep")
@patch("google_sync._fetch_existing_events_by_uid")
def test_sync_games_to_google_calendar_inserts_and_updates(mock_fetch_existing, mock_sleep):
    mock_service = MagicMock()
    # Mock existing events matching UID of Game 1
    mock_fetch_existing.return_value = {
        "backloggd-484196144837@backloggd-calendar": {"id": "evt_existing_id"}
    }

    games = [
        {
            "title": "Game 1",
            "url": "https://backloggd.com/games/g1/",
            "release_date": date(2026, 12, 1),
        },
        {
            "title": "Game 2",
            "url": "https://backloggd.com/games/g2/",
            "release_date": date(2026, 12, 2),
        },
        {
            "title": "Game 3",
            "release_date": None,  # should be skipped
        },
    ]

    with patch("google_sync._upsert_single_event") as mock_upsert:
        # First call updates existing event, second call creates new event
        mock_upsert.side_effect = [True, False]
        sync_games_to_google_calendar(mock_service, "cal123", games)
        assert mock_upsert.call_count == 2

