"""
Unit tests for backloggd_client.py
"""

import time
from datetime import date
from unittest.mock import MagicMock, patch

from bs4 import BeautifulSoup

from backloggd_client import (
    BASE_URL,
    CHALLENGE_ERROR_SUBSTRINGS,
    CHALLENGE_TITLE_SUBSTRINGS,
    _extract_game_entry,
    _fetch_page_content,
    _has_game_covers,
    _has_page_navigation,
    _is_target_page_ready,
    _log_challenge_resolution,
    _normalize_text,
    _wait_for_challenge_resolution,
    fetch_backloggd_wishlist,
    is_challenge_error_page,
    is_challenge_page,
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

    assert parse_release_date("2026 Q1") == date(2026, 1, 1)
    assert parse_release_date("2026 Q2") == date(2026, 4, 1)
    assert parse_release_date("2026 Q3") == date(2026, 7, 1)
    assert parse_release_date("2026 Q4") == date(2026, 10, 1)


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


@patch("backloggd_client._wait_for_challenge_resolution", return_value=True)
def test_fetch_page_content_success(mock_wait):
    mock_page = MagicMock()
    mock_page.content.return_value = "<html><body>Test</body></html>"

    result = _fetch_page_content(mock_page, "https://example.com")
    assert result == "<html><body>Test</body></html>"
    mock_page.goto.assert_called_once_with(
        "https://example.com", wait_until="domcontentloaded", timeout=30000
    )


@patch("backloggd_client._wait_for_challenge_resolution", return_value=True)
def test_fetch_page_content_exception(mock_wait):
    mock_page = MagicMock()
    mock_page.goto.side_effect = Exception("Network Error")
    mock_page.content.side_effect = Exception("No content")

    result = _fetch_page_content(mock_page, "https://example.com")
    assert result is None


@patch("backloggd_client._wait_for_challenge_resolution", return_value=True)
def test_fetch_page_content_timeout_fallback(mock_wait):
    mock_page = MagicMock()
    mock_page.goto.side_effect = Exception("Timeout 30000ms exceeded")
    mock_page.content.return_value = "<html><body>Partial content</body></html>"

    result = _fetch_page_content(mock_page, "https://example.com")
    assert result == "<html><body>Partial content</body></html>"


@patch("backloggd_client._wait_for_challenge_resolution", return_value=True)
def test_fetch_page_content_navigating_retry(mock_wait):
    mock_page = MagicMock()
    mock_page.content.side_effect = [
        Exception(
            "Unable to retrieve content because the page is navigating and changing the content."
        ),
        "<html><body>Navigated Content</body></html>",
    ]

    result = _fetch_page_content(mock_page, "https://example.com")
    assert result == "<html><body>Navigated Content</body></html>"
    mock_page.wait_for_load_state.assert_called_once_with("domcontentloaded", timeout=10000)


@patch("backloggd_client._wait_for_challenge_resolution", return_value=True)
def test_fetch_page_content_navigating_wait_error(mock_wait):
    mock_page = MagicMock()
    mock_page.wait_for_timeout.side_effect = Exception("Wait error")
    mock_page.wait_for_load_state.side_effect = Exception("Wait load state error")
    mock_page.content.side_effect = [
        Exception(
            "Unable to retrieve content because the page is navigating and changing the content."
        ),
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

    mock_page.content.return_value = (
        "<html><head><title>Oh noes!</title></head><body>Access Denied</body></html>"
    )

    games = fetch_backloggd_wishlist("testuser", days_back=30, challenge_timeout=0)
    assert games == []


@patch("backloggd_client.sync_playwright")
def test_fetch_backloggd_wishlist_success(mock_playwright):
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()

    mock_playwright.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    mock_page.title.return_value = "User's Games | Backloggd"
    mock_page.locator.side_effect = lambda sel: MagicMock(
        count=MagicMock(return_value=1 if ".game-cover" in sel or ".navbar" in sel else 0)
    )

    # Base page 1 returns 1 valid future game and 1 game before cutoff date
    base_page1_html = """
    <html><head><title>Wishlist</title></head><body>
    <div>
        <div class="game-cover">
            <a href="/games/game-1/"><img alt="Game 1" src="cover1.jpg" /></a>
        </div>
        <div class="release-below"><p>Dec 31, 2026</p></div>
    </div>
    <div>
        <div class="game-cover">
            <a href="/games/old-game/"><img alt="Old Game" src="cover_old.jpg" /></a>
        </div>
        <div class="release-below"><p>Jan 1, 2010</p></div>
    </div>
    </body></html>
    """
    extra_page1_html = """
    <html><head><title>Wishlist</title></head><body>
    <div>
        <div class="game-cover">
            <a href="/games/game-1-dlc/"><img alt="Game 1: Story DLC" src="cover_dlc.jpg" /></a>
        </div>
        <div class="release-below"><p>Nov 15, 2026</p></div>
    </div>
    </body></html>
    """

    mock_page.content.side_effect = [
        base_page1_html,
        extra_page1_html,
    ]

    games = fetch_backloggd_wishlist("testuser", days_back=30, max_pages=5, include_extras=True)
    assert len(games) == 2
    assert games[0]["title"] == "Game 1"
    assert games[0]["release_date"] == date(2026, 12, 31)
    assert games[0]["category_type"] == "base"
    assert games[1]["title"] == "Game 1: Story DLC"
    assert games[1]["release_date"] == date(2026, 11, 15)
    assert games[1]["category_type"] == "extra"


@patch("backloggd_client.sync_playwright")
def test_fetch_backloggd_wishlist_no_extras(mock_playwright):
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()

    mock_playwright.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    mock_page.title.return_value = "User's Games | Backloggd"
    mock_page.locator.side_effect = lambda sel: MagicMock(
        count=MagicMock(return_value=1 if ".game-cover" in sel or ".navbar" in sel else 0)
    )

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

    mock_page.content.side_effect = [page1_html]

    games = fetch_backloggd_wishlist("testuser", days_back=30, max_pages=5, include_extras=False)
    assert len(games) == 1
    assert games[0]["title"] == "Game 1"


@patch("backloggd_client.sync_playwright")
def test_fetch_backloggd_wishlist_deduplication(mock_playwright):
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()

    mock_playwright.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    mock_page.title.return_value = "User's Games | Backloggd"
    mock_page.locator.side_effect = lambda sel: MagicMock(
        count=MagicMock(return_value=1 if ".game-cover" in sel or ".navbar" in sel else 0)
    )

    # Duplicate game returned in both base and extra query
    duplicate_html = """
    <html><head><title>Wishlist</title></head><body>
    <div>
        <div class="game-cover">
            <a href="/games/duplicate-game/"><img alt="Duplicate Game" src="cover.jpg" /></a>
        </div>
        <div class="release-below"><p>Dec 31, 2026</p></div>
    </div>
    </body></html>
    """

    mock_page.content.side_effect = [duplicate_html, duplicate_html]

    games = fetch_backloggd_wishlist("testuser", days_back=30, include_extras=True)
    assert len(games) == 1
    assert games[0]["title"] == "Duplicate Game"


@patch("backloggd_client.sync_playwright")
def test_fetch_backloggd_wishlist_multiple_list_types(mock_playwright):
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()

    mock_playwright.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    mock_page.title.return_value = "User's Games | Backloggd"
    mock_page.locator.side_effect = lambda sel: MagicMock(
        count=MagicMock(return_value=1 if ".game-cover" in sel or ".navbar" in sel else 0)
    )

    wishlist_html = """
    <html><body>
        <div class="game-cover">
            <a href="/games/wishlist-game/"><img alt="Wishlist Game" src="w.jpg" /></a>
        </div>
        <div class="release-below"><p>Dec 31, 2026</p></div>
    </body></html>
    """
    backlog_html = """
    <html><body>
        <div class="game-cover">
            <a href="/games/backlog-game/"><img alt="Backlog Game" src="b.jpg" /></a>
        </div>
        <div class="release-below"><p>Dec 31, 2026</p></div>
    </body></html>
    """
    empty_html = "<html><body></body></html>"

    mock_page.content.side_effect = [
        wishlist_html,  # wishlist base (1 cover < 40 -> breaks)
        empty_html,  # wishlist extra (0 covers -> breaks)
        backlog_html,  # backlog base (1 cover < 40 -> breaks)
        empty_html,  # backlog extra (0 covers -> breaks)
    ]

    games = fetch_backloggd_wishlist(
        "testuser", days_back=30, list_types=["wishlist", "backlog"], include_extras=True
    )
    assert len(games) == 2
    titles = [g["title"] for g in games]
    assert "Wishlist Game" in titles
    assert "Backlog Game" in titles


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


def test_normalize_text():
    assert (
        _normalize_text("  Making sure you\u2019re not a bot!  ") == "making sure you're not a bot!"
    )
    assert _normalize_text("\u2018HELLO`") == "'hello'"


def test_is_challenge_page_titles():
    mock_page = MagicMock()
    for sub in CHALLENGE_TITLE_SUBSTRINGS:
        mock_page.title.return_value = f"Prefix {sub} Suffix"
        assert is_challenge_page(mock_page) is True

    # Test unicode curly quote in title
    mock_page.title.return_value = "Making sure you\u2019re not a bot!"
    assert is_challenge_page(mock_page) is True

    mock_page.title.return_value = "User Profile | Backloggd"
    mock_page.locator.return_value.count.return_value = 0
    assert is_challenge_page(mock_page) is False


def test_is_challenge_page_locator():
    mock_page = MagicMock()
    mock_page.title.return_value = "Backloggd"
    mock_page.locator.return_value.count.return_value = 1
    assert is_challenge_page(mock_page) is True

    # Absent navbar and non-backloggd title
    mock_page.title.return_value = "Unknown Blank Page"
    mock_page.locator.return_value.count.return_value = 0
    assert is_challenge_page(mock_page) is True


def test_is_challenge_page_exception():
    mock_page = MagicMock()
    mock_page.title.side_effect = Exception("Browser crashed")
    assert is_challenge_page(mock_page) is False


def test_has_game_covers():
    mock_page = MagicMock()
    mock_page.locator.return_value.count.return_value = 5
    assert _has_game_covers(mock_page) is True

    mock_page.locator.return_value.count.return_value = 0
    assert _has_game_covers(mock_page) is False

    mock_no_locator = MagicMock(spec=[])
    assert _has_game_covers(mock_no_locator) is False

    mock_err = MagicMock()
    mock_err.locator.side_effect = Exception("Locator error")
    assert _has_game_covers(mock_err) is False


def test_has_page_navigation():
    mock_page = MagicMock()
    mock_page.locator.return_value.count.return_value = 1
    assert _has_page_navigation(mock_page) is True

    mock_page.locator.return_value.count.return_value = 0
    assert _has_page_navigation(mock_page) is False

    mock_no_locator = MagicMock(spec=[])
    assert _has_page_navigation(mock_no_locator) is True

    mock_err = MagicMock()
    mock_err.locator.side_effect = Exception("Locator error")
    assert _has_page_navigation(mock_err) is False


def test_is_target_page_ready():
    # 1. Game covers present
    mock_page = MagicMock()
    mock_page.locator.side_effect = lambda sel: MagicMock(
        count=MagicMock(return_value=10 if ".game-cover" in sel else 0)
    )
    assert _is_target_page_ready(mock_page) is True

    # 2. Backloggd title + nav present
    mock_page.locator.side_effect = lambda sel: MagicMock(
        count=MagicMock(return_value=1 if ".navbar" in sel else 0)
    )
    mock_page.title.return_value = "Noahffiliation's games | Backloggd"
    assert _is_target_page_ready(mock_page) is True

    # 3. 404 title + nav present
    mock_page.title.return_value = "404 Page Not Found"
    assert _is_target_page_ready(mock_page) is True

    # 4. Unknown title
    mock_page.title.return_value = "Random Page"
    assert _is_target_page_ready(mock_page) is False

    # 5. Exception raised
    mock_page.title.side_effect = Exception("Page error")
    mock_page.locator.side_effect = Exception("Locator error")
    assert _is_target_page_ready(mock_page) is False


def test_log_challenge_resolution(caplog):
    # Challenge not detected -> no log
    _log_challenge_resolution(False, 100.0)

    # Challenge detected -> logs info
    with caplog.at_level("INFO"):
        _log_challenge_resolution(True, time.monotonic() - 1.5)
    assert "Anti-bot challenge solved in" in caplog.text


def test_wait_for_challenge_resolution_covers_immediately_ready():
    mock_page = MagicMock()
    mock_page.title.return_value = "User's Games | Backloggd"
    # Challenge selectors -> 0, .game-cover -> 40
    mock_page.locator.side_effect = lambda sel: MagicMock(
        count=MagicMock(return_value=40 if ".game-cover" in sel else 0)
    )

    assert _wait_for_challenge_resolution(mock_page, "https://example.com", timeout=5) is True


def test_wait_for_challenge_resolution_empty_page_ready():
    mock_page = MagicMock()
    mock_page.title.return_value = "User's Games | Backloggd"

    # Challenge selectors -> 0, .game-cover -> 0, .navbar -> 1
    def locator_side_effect(sel):
        if ".navbar" in sel:
            return MagicMock(count=MagicMock(return_value=1))
        return MagicMock(count=MagicMock(return_value=0))

    mock_page.locator.side_effect = locator_side_effect
    assert _wait_for_challenge_resolution(mock_page, "https://example.com", timeout=5) is True
    mock_page.wait_for_timeout.assert_called_with(500)


@patch("backloggd_client.time.monotonic")
def test_wait_for_challenge_resolution_page_ready_after_delay(mock_monotonic):
    mock_page = MagicMock()
    mock_page.title.return_value = "User's Games | Backloggd"

    # First pass: no covers, no navbar. Second pass: 40 covers
    def locator_side_effect(sel):
        if ".game-cover" in sel:
            return MagicMock(
                count=MagicMock(return_value=40 if mock_monotonic.call_count > 2 else 0)
            )
        return MagicMock(count=MagicMock(return_value=0))

    mock_page.locator.side_effect = locator_side_effect
    mock_monotonic.side_effect = [100.0, 100.5, 101.0, 101.5]

    assert _wait_for_challenge_resolution(mock_page, "https://example.com", timeout=5) is True
    mock_page.wait_for_timeout.assert_called_with(500)


@patch("backloggd_client.time.monotonic")
def test_wait_for_challenge_resolution_success(mock_monotonic):
    mock_page = MagicMock()
    mock_page.title.side_effect = [
        "Making sure you\u2019re not a bot!",
        "Making sure you\u2019re not a bot!",
        "User's Games | Backloggd",
        "User's Games | Backloggd",
    ]

    # Initially locator returns 1 for challenge, then 40 for game covers
    def locator_side_effect(sel):
        if "#anubis_challenge" in sel:
            return MagicMock(
                count=MagicMock(return_value=1 if mock_monotonic.call_count <= 2 else 0)
            )
        return MagicMock(count=MagicMock(return_value=40 if mock_monotonic.call_count > 2 else 0))

    mock_page.locator.side_effect = locator_side_effect
    mock_monotonic.side_effect = [100.0, 100.5, 101.0, 101.5, 102.0, 102.5]

    assert _wait_for_challenge_resolution(mock_page, "https://example.com", timeout=10) is True


@patch("backloggd_client.time.monotonic")
def test_wait_for_challenge_resolution_timeout(mock_monotonic):
    mock_page = MagicMock()
    mock_page.title.return_value = "Making sure you're not a bot!"

    def locator_side_effect(sel):
        if "#anubis_challenge" in sel:
            return MagicMock(count=MagicMock(return_value=1))
        return MagicMock(count=MagicMock(return_value=0))

    mock_page.locator.side_effect = locator_side_effect

    # Simulate time passing beyond timeout
    mock_monotonic.side_effect = [100.0, 100.5, 105.5, 106.0]

    assert _wait_for_challenge_resolution(mock_page, "https://example.com", timeout=5) is False


@patch("backloggd_client._wait_for_challenge_resolution")
def test_fetch_page_content_challenge_failed(mock_wait):
    mock_page = MagicMock()
    mock_wait.return_value = False

    result = _fetch_page_content(mock_page, "https://example.com", max_retries=1)
    assert result is None


def test_is_challenge_error_page_titles():
    mock_page = MagicMock()
    for sub in CHALLENGE_ERROR_SUBSTRINGS:
        mock_page.title.return_value = f"Prefix {sub} Suffix"
        assert is_challenge_error_page(mock_page) is True

    mock_page.title.return_value = "Normal Page | Backloggd"
    mock_page.url = "https://backloggd.com/u/user"
    mock_page.locator.return_value.count.return_value = 0
    assert is_challenge_error_page(mock_page) is False


def test_is_challenge_error_page_pass_challenge_url():
    mock_page = MagicMock()
    mock_page.title.return_value = ""
    mock_page.url = "https://backloggd.com/.within.website/x/cmd/anubis/api/pass-challenge?id=123"
    assert is_challenge_error_page(mock_page) is True


def test_is_challenge_error_page_reject_image():
    mock_page = MagicMock()
    mock_page.title.return_value = "Unknown Page"
    mock_page.url = "https://backloggd.com/challenge"
    mock_page.locator.return_value.count.return_value = 1
    assert is_challenge_error_page(mock_page) is True

    # When title contains 'backloggd', reject.webp locator check is skipped
    mock_page.title.return_value = "Games | Backloggd"
    assert is_challenge_error_page(mock_page) is False


def test_is_challenge_error_page_exception():
    mock_page = MagicMock()
    mock_page.title.side_effect = Exception("Browser error")
    assert is_challenge_error_page(mock_page) is False


def test_is_challenge_page_error_titles():
    mock_page = MagicMock()
    for sub in CHALLENGE_ERROR_SUBSTRINGS:
        mock_page.title.return_value = f"Prefix {sub} Suffix"
        assert is_challenge_page(mock_page) is False


def test_wait_for_challenge_resolution_error_fast_fail(caplog):
    mock_page = MagicMock()
    mock_page.title.return_value = "Oh noes!"
    with caplog.at_level("WARNING"):
        result = _wait_for_challenge_resolution(mock_page, "https://example.com", timeout=30)
    assert result is False
    assert "Anti-bot challenge error/rejection detected" in caplog.text


@patch("backloggd_client._wait_for_challenge_resolution")
def test_fetch_page_content_retry_success(mock_wait):
    mock_page = MagicMock()
    # Fails first attempt, succeeds on second attempt
    mock_wait.side_effect = [False, True]
    mock_page.content.return_value = "<html><head><title>Games</title></head><body>OK</body></html>"

    result = _fetch_page_content(mock_page, "https://example.com", max_retries=2)
    assert result == "<html><head><title>Games</title></head><body>OK</body></html>"
    assert mock_wait.call_count == 2


@patch("backloggd_client._wait_for_challenge_resolution")
def test_fetch_page_content_retry_challenge_error_content(mock_wait):
    mock_page = MagicMock()
    mock_wait.return_value = True
    # Attempt 1 returns challenge/error HTML, Attempt 2 returns valid content
    error_html = "<html><head><title>Oh noes!</title></head><body>Error</body></html>"
    valid_html = "<html><head><title>Games | Backloggd</title></head><body>OK</body></html>"
    mock_page.content.side_effect = [error_html, valid_html]

    result = _fetch_page_content(mock_page, "https://example.com", max_retries=2)
    assert result == valid_html


@patch("backloggd_client._wait_for_challenge_resolution")
def test_fetch_page_content_goto_exception_retries(mock_wait):
    mock_page = MagicMock()
    mock_page.goto.side_effect = [Exception("Network error"), MagicMock()]
    mock_wait.side_effect = [False, True]
    mock_page.content.return_value = "<html><head><title>Games</title></head><body>OK</body></html>"

    result = _fetch_page_content(mock_page, "https://example.com", max_retries=2)
    assert result == "<html><head><title>Games</title></head><body>OK</body></html>"
    assert mock_page.goto.call_count == 2


@patch("backloggd_client._wait_for_challenge_resolution")
def test_fetch_page_content_navigating_state(mock_wait):
    mock_page = MagicMock()
    mock_wait.return_value = True

    # 1st read: 'navigating' error, wait succeeds; 2nd read: returns content
    mock_page.content.side_effect = [
        Exception("Execution context was destroyed, most likely because of a navigation."),
        "<html><head><title>Games</title></head><body>Navigated</body></html>",
    ]

    result = _fetch_page_content(mock_page, "https://example.com")
    assert result == "<html><head><title>Games</title></head><body>Navigated</body></html>"
    mock_page.wait_for_load_state.assert_called_once_with("domcontentloaded", timeout=10000)


@patch("backloggd_client._wait_for_challenge_resolution")
def test_fetch_page_content_navigating_wait_exception_still_recovers(mock_wait):
    mock_page = MagicMock()
    mock_wait.return_value = True

    mock_page.content.side_effect = [
        Exception("Execution context was destroyed, most likely because of a navigation."),
        "<html><head><title>Games</title></head><body>Navigated</body></html>",
    ]
    mock_page.wait_for_load_state.side_effect = Exception("Load state timed out")

    result = _fetch_page_content(mock_page, "https://example.com")
    assert result == "<html><head><title>Games</title></head><body>Navigated</body></html>"


@patch("backloggd_client._wait_for_challenge_resolution")
def test_fetch_page_content_non_navigating_error_breaks(mock_wait):
    mock_page = MagicMock()
    mock_wait.return_value = True

    mock_page.content.side_effect = Exception("Fatal browser error")

    result = _fetch_page_content(mock_page, "https://example.com", max_retries=1)
    assert result is None


@patch("backloggd_client._wait_for_challenge_resolution")
def test_fetch_page_content_navigating_exhausted(mock_wait):
    mock_page = MagicMock()
    mock_wait.return_value = True

    mock_page.content.side_effect = Exception("Page is navigating")

    result = _fetch_page_content(mock_page, "https://example.com", max_retries=1)
    assert result is None


@patch("backloggd_client._fetch_page_content")
@patch("backloggd_client.sync_playwright")
def test_fetch_backloggd_wishlist_404_not_found(mock_playwright, mock_fetch):
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()

    mock_playwright.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    mock_fetch.return_value = (
        "<html><head><title>404 Page Not Found</title></head><body><h1>UH-OH!</h1></body></html>"
    )

    games = fetch_backloggd_wishlist("nonexistent_user", days_back=30)
    assert games == []


@patch("backloggd_client._fetch_page_content")
@patch("backloggd_client.sync_playwright")
def test_fetch_backloggd_wishlist_anubis_challenge_unresolved(mock_playwright, mock_fetch):
    mock_browser = MagicMock()
    mock_context = MagicMock()
    mock_page = MagicMock()

    mock_playwright.return_value.__enter__.return_value.chromium.launch.return_value = mock_browser
    mock_browser.new_context.return_value = mock_context
    mock_context.new_page.return_value = mock_page

    mock_fetch.return_value = (
        "<html><head><title>Making sure you're not a bot!</title></head>"
        "<body><div id='anubis_challenge'></div></body></html>"
    )

    games = fetch_backloggd_wishlist("testuser", days_back=30)
    assert games == []
