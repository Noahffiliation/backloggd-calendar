"""
Unit tests for generate_ical.py
"""

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from generate_ical import main, parse_args, validate_safe_path


def test_parse_args_defaults():
    with patch.dict(os.environ, {}, clear=True):
        with patch("sys.argv", ["generate_ical.py"]):
            args = parse_args()
            assert args.username is None
            assert args.output == "backloggd_wishlist.ics"
            assert args.days_back == 30
            assert args.sync_google is False
            assert args.no_headless is False
            assert args.verbose is False


def test_validate_safe_path_valid():
    base = Path("/workspace").resolve()
    target = validate_safe_path("output.ics", base)
    assert target == base / "output.ics"


def test_validate_safe_path_invalid():
    base = Path("/workspace").resolve()
    with pytest.raises(ValueError):
        validate_safe_path("../../etc/passwd", base)


@patch("generate_ical.sys.exit")
def test_main_missing_username(mock_exit):
    with patch("generate_ical.parse_args") as mock_parse:
        args = MagicMock()
        args.username = None
        mock_parse.return_value = args

        main()
        mock_exit.assert_called_once_with(1)


@patch("generate_ical.sys.exit")
@patch("generate_ical.fetch_backloggd_wishlist")
def test_main_fetch_error(mock_fetch, mock_exit):
    with patch("generate_ical.parse_args") as mock_parse:
        args = MagicMock()
        args.username = "testuser"
        args.verbose = True
        args.output = "test.ics"
        args.days_back = 30
        args.no_headless = False
        args.sync_google = False
        mock_parse.return_value = args

        mock_fetch.side_effect = Exception("Scraping error")

        main()
        mock_exit.assert_called_once_with(1)


@patch("generate_ical.get_google_calendar_service")
@patch("generate_ical.get_or_create_calendar")
@patch("generate_ical.sync_games_to_google_calendar")
@patch("generate_ical.export_calendar_to_file")
@patch("generate_ical.build_wishlist_calendar")
@patch("generate_ical.fetch_backloggd_wishlist")
def test_main_success_with_google_sync(
    mock_fetch,
    mock_build_cal,
    mock_export,
    mock_sync_gcal,
    mock_get_or_create,
    mock_get_service
):
    with patch("generate_ical.parse_args") as mock_parse:
        args = MagicMock()
        args.username = "testuser"
        args.verbose = False
        args.output = "test.ics"
        args.days_back = 30
        args.no_headless = False
        args.sync_google = True
        mock_parse.return_value = args

        mock_fetch.return_value = [{"title": "Test Game"}]

        main()

        mock_fetch.assert_called_once_with(username="testuser", days_back=30, headless=True)
        mock_build_cal.assert_called_once()
        mock_export.assert_called_once()
        mock_get_service.assert_called_once()
        mock_get_or_create.assert_called_once()
        mock_sync_gcal.assert_called_once()


@patch("generate_ical.sys.exit")
@patch("generate_ical.get_google_calendar_service")
def test_main_google_sync_error(mock_get_service, mock_exit):
    with patch("generate_ical.parse_args") as mock_parse:
        with patch("generate_ical.fetch_backloggd_wishlist") as mock_fetch:
            with patch("generate_ical.build_wishlist_calendar"):
                with patch("generate_ical.export_calendar_to_file"):
                    args = MagicMock()
                    args.username = "testuser"
                    args.verbose = False
                    args.output = "test.ics"
                    args.days_back = 30
                    args.no_headless = False
                    args.sync_google = True
                    mock_parse.return_value = args

                    mock_fetch.return_value = []
                    mock_get_service.side_effect = Exception("GCal Auth Failed")

                    main()
                    mock_exit.assert_called_once_with(1)
