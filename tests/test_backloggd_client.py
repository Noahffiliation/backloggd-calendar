"""
Unit tests for backloggd_client.py
"""

from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup
import pytest

from backloggd_client import (
    BASE_URL,
    _extract_game_entry,
    _fetch_page_content,
    fetch_backloggd_wishlist,
    parse_release_date,
)


def test_parse_release_date_empty_and_invalid():
    assert parse_release_date("") is None
    assert parse_release_date(None) is None
    assert parse_release_date("TBA") is None
    assert parse_release_date("tbd") is None
    assert parse_release_date("unknown") is None
    assert parse_release_date("N/A") is None
    assert parse_release_date("invalid_date_str_12345") is None


def test_parse_release_date_year_only():
    today_year = date.today().year
    # Future or current year
    future_year = today_year + 1
    assert parse_release_date(str(future_year)) == date(future_year, 12, 31)

    # Past year
    past_year = 2015
    assert parse_release_date(str(past_year)) == date(past_year, 1, 1)


def test_parse_release_date_quarter():
    assert parse_release_date("Q1 2026") == date(2026, 1, 1)
    assert parse_release_date("Q2 2026") == date(2026, 4, 1)
    assert parse_release_date("Q3 2026") == date(2026, 7, 1)
    assert parse_release_date("Q4 2026") == date(2026, 10, 1)


def test_parse_release_date_standard_formats():
    assert parse_release_date("Mar 27, 2025") == date(2025, 3, 27)
    assert parse_release_date("2026-03-27") == date(2026, 3, 27)


def test_extract_game_entry_valid():
    html = """
    <div>
        <div class="game-cover">
            <a href="/games/hades-ii/">
                <img alt="Hades II" src="https://images.backloggd.com/cover.jpg" />
            </a>
        </div>
        <div class="release-below">
            <p>May 6, 2024</p>
        </div>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    cover = soup.select_one(".game-cover")
    cutoff = date(2020, 1, 1)

    entry = _extract_game_entry(cover, cutoff)
    assert entry is not None
    assert entry["title"] == "Hades II"
    assert entry["url"] == f"{BASE_URL}/games/hades-ii/"
    assert entry["release_date"] == date(2024, 5, 6)
    assert entry["release_date_raw"] == "May 6, 2024"
    assert entry["cover_url"] == "https://images.backloggd.com/cover.jpg"


def test_extract_game_entry_text_fallback_and_before_cutoff():
    html = """
    <div>
        <div class="game-cover">
            <a href="/games/old-game/"></a>
            <div class="game-text-centered">Old Game Title</div>
        </div>
        <div class="release-below">
            <p>Jan 1, 2010</p>
        </div>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    cover = soup.select_one(".game-cover")
    cutoff = date(2020, 1, 1)

    # Should return None because 2010 is before cutoff date 2020
    entry = _extract_game_entry(cover, cutoff)
    assert entry is None


def test_fetch_page_content_success():
    mock_page = MagicMock()
    mock_page.content.return_value = "<html><body>Test</body></html>"

    result = _fetch_page_content(mock_page, "https://example.com")
    assert result == "<html><body>Test</body></html>"
    mock_page.goto.assert_called_once_with("https://example.com", wait_until="domcontentloaded", timeout=30000)


def test_fetch_page_content_exception():
    mock_page = MagicMock()
    mock_page.goto.side_effect = Exception("Network Error")
    mock_page.content.side_effect = Exception("No content")

    result = _fetch_page_content(mock_page, "https://example.com")
    assert result is None


def test_fetch_page_content_timeout_fallback():
    mock_page = MagicMock()
    mock_page.goto.side_effect = Exception("Timeout 30000ms exceeded")
    mock_page.content.return_value = "<html><body>Partial content</body></html>"

    result = _fetch_page_content(mock_page, "https://example.com")
    assert result == "<html><body>Partial content</body></html>"


def test_fetch_page_content_navigating_retry():
    mock_page = MagicMock()
    mock_page.content.side_effect = [
        Exception("Unable to retrieve content because the page is navigating and changing the content."),
        "<html><body>Navigated Content</body></html>",
    ]

    result = _fetch_page_content(mock_page, "https://example.com")
    assert result == "<html><body>Navigated Content</body></html>"
    mock_page.wait_for_load_state.assert_called_once_with("domcontentloaded", timeout=10000)


def test_fetch_page_content_navigating_wait_error():
    mock_page = MagicMock()
    mock_page.wait_for_timeout.side_effect = Exception("Wait error")
    mock_page.wait_for_load_state.side_effect = Exception("Wait load state error")
    mock_page.content.side_effect = [
        Exception("Unable to retrieve content because the page is navigating and changing the content."),
        Exception("Still navigating"),
        Exception("Failed"),
    ]

    result = _fetch_page_content(mock_page, "https://example.com")
    assert result is None


@patch("backloggd_client.sync_playwright")
def test_fetch_backloggd_wishlist_anti_bot_blocked(mock_playwright):
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()

    mock_playwright.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    mock_page.content.return_value = "<html><head><title>Oh noes!</title></head><body>Access Denied</body></html>"

    games = fetch_backloggd_wishlist("testuser", days_back=30)
    assert games == []


@patch("backloggd_client.sync_playwright")
def test_fetch_backloggd_wishlist_success(mock_playwright):
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()

    mock_playwright.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    # Page 1 returns 1 game, Page 2 returns no covers
    page1_html = """
    <html><head><title>Wishlist</title></head><body>
    <div>
        <div class="game-cover">
            <a href="/games/game-1/"><img alt="Game 1" src="cover1.jpg" /></a>
        </div>
        <div class="release-below"><p>Dec 31, 2026</p></div>
    </div>
    </body></html>
    """
    page2_html = "<html><head><title>Wishlist</title></head><body>No games</body></html>"

    mock_page.content.side_effect = [page1_html, page2_html]

    games = fetch_backloggd_wishlist("testuser", days_back=30, max_pages=5)
    assert len(games) == 1
    assert games[0]["title"] == "Game 1"
    assert games[0]["release_date"] == date(2026, 12, 31)


@patch("backloggd_client.sync_playwright")
@patch("backloggd_client._fetch_page_content")
def test_fetch_backloggd_wishlist_empty_html(mock_fetch_content, mock_playwright):
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()

    mock_playwright.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    mock_fetch_content.return_value = None

    games = fetch_backloggd_wishlist("testuser", days_back=30)
    assert games == []

