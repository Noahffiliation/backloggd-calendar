"""
Playwright-based client for scraping Backloggd wishlist games and release dates.
Handles Anubis proof-of-work anti-bot protection and pagination.
"""

import contextlib
import logging
import re
import time
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from dateutil import parser
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
BASE_URL = "https://backloggd.com"

CHALLENGE_TITLE_SUBSTRINGS = (
    "making sure you're not a bot",
    "making sure you are not a bot",
    "not a bot",
    "botstopper",
    "anubis",
    "loading http",
    "just a moment",
    "attention required",
    "checking your browser",
    "security check",
    "cloudflare",
    "ddos-guard",
    "verify you are human",
)

ACCESS_DENIED_TEXT = "access denied"

CHALLENGE_ERROR_SUBSTRINGS = (
    "oh noes!",
    ACCESS_DENIED_TEXT,
    "internal server error",
    "administrator has misconfigured anubis",
    "missing_feature",
    "calculation_error",
    "challenge_error",
)


def _normalize_text(text: Any) -> str:
    """Normalize text for consistent substring matching across quotes and whitespace."""
    if not isinstance(text, str):
        return ""
    return text.replace("\u2019", "'").replace("\u2018", "'").replace("`", "'").strip().lower()


def parse_release_date(date_str: str | None) -> date | None:
    """
    Parses various release date formats from Backloggd into a datetime.date object.
    Supports: 'Mar 27, 2025', '2026', 'Q3 2026', 'Dec 2026', '2026-03-27'.
    """
    if not date_str:
        return None

    s = date_str.strip()
    if not s or s.lower() in ("tba", "tbd", "unknown", "n/a"):
        return None

    today = date.today()

    # Year only (e.g. "2026")
    if re.match(r"^\d{4}$", s):
        y = int(s)
        # If current or future year, treat as tentative end-of-year date so it is included in future window
        if y >= today.year:
            return date(y, 12, 31)
        return date(y, 1, 1)

    # Quarter format (e.g. "Q3 2026" or "2026 Q3") -> 2026-07-01
    m_q1 = re.match(r"^Q([1-4])\s+(\d{4})$", s, re.IGNORECASE)
    if m_q1:
        q = int(m_q1.group(1))
        y = int(m_q1.group(2))
        month = (q - 1) * 3 + 1
        return date(y, month, 1)

    m_q2 = re.match(r"^(\d{4})\s+Q([1-4])$", s, re.IGNORECASE)
    if m_q2:
        y = int(m_q2.group(1))
        q = int(m_q2.group(2))
        month = (q - 1) * 3 + 1
        return date(y, month, 1)

    try:
        dt = parser.parse(s, default=datetime(2000, 1, 1))
        return dt.date()
    except Exception as e:
        logger.debug(f"Could not parse date string '{date_str}': {e}")
        return None


def _extract_game_entry(
    cover: Any, cutoff_date: date, category_type: str = "base"
) -> dict[str, Any] | None:
    """Extract game metadata from a single game cover HTML element."""
    img_tag = cover.find("img")
    title = img_tag["alt"] if img_tag and "alt" in img_tag.attrs else None
    if not title:
        text_div = cover.select_one(".game-text-centered")
        title = text_div.get_text(strip=True) if text_div else "Untitled Game"

    a_tag = cover.find("a")
    rel_url = a_tag["href"] if a_tag and "href" in a_tag.attrs else ""
    full_url = urljoin(BASE_URL, rel_url) if rel_url else ""

    cover_img_url = img_tag["src"] if img_tag and "src" in img_tag.attrs else ""

    parent = cover.parent
    release_div = parent.select_one(".release-below p") if parent else None
    release_date_raw = release_div.get_text(strip=True) if release_div else ""

    parsed_date = parse_release_date(release_date_raw)

    if parsed_date is not None and parsed_date < cutoff_date:
        logger.debug(f"Skipping '{title}' (released {parsed_date} before cutoff {cutoff_date})")
        return None

    return {
        "title": title,
        "url": full_url,
        "release_date": parsed_date,
        "release_date_raw": release_date_raw,
        "cover_url": cover_img_url,
        "category_type": category_type,
    }


def is_challenge_error_page(page: Any) -> bool:
    """Detect if the page is currently displaying an anti-bot error or rejection screen."""
    try:
        title = _normalize_text(page.title()) if hasattr(page, "title") else ""
        if any(sub in title for sub in CHALLENGE_ERROR_SUBSTRINGS):
            return True
        curr_url = getattr(page, "url", "")
        if "pass-challenge" in curr_url:
            return True
        if (
            "backloggd" not in title
            and hasattr(page, "locator")
            and page.locator("img[src*='reject.webp']").count() > 0
        ):
            return True
    except Exception:
        pass
    return False


def is_challenge_page(page: Any) -> bool:
    """Detect if the page is currently on an Anubis / Cloudflare / BotStopper challenge screen."""
    try:
        title = _normalize_text(page.title()) if hasattr(page, "title") else ""
        if any(sub in title for sub in CHALLENGE_ERROR_SUBSTRINGS):
            return False
        if any(sub in title for sub in CHALLENGE_TITLE_SUBSTRINGS):
            return True
        if hasattr(page, "locator"):
            challenge_selectors = (
                "#anubis_challenge, #anubis_version, script[src*='anubis'], "
                "#challenge-running, #cf-challenge-running, "
                ".ray_id, #challenge-form, .challenge-form, #progress[role='progressbar']"
            )
            ch_count = page.locator(challenge_selectors).count()
            if isinstance(ch_count, int) and ch_count > 0:
                return True
            nav_count = page.locator(
                ".navbar, nav, .game-cover, #main-container, .profile-header"
            ).count()
            if (
                isinstance(nav_count, int)
                and nav_count == 0
                and "backloggd" not in title
                and "404" not in title
            ):
                return True
    except Exception:
        pass
    return False


def _has_game_covers(page: Any) -> bool:
    """Check if game cover elements exist in the page DOM."""
    if not hasattr(page, "locator"):
        return False
    try:
        count = page.locator(".game-cover").count()
        return isinstance(count, int) and count > 0
    except Exception:
        return False


def _has_page_navigation(page: Any) -> bool:
    """Check if standard Backloggd navigation or container elements exist."""
    if not hasattr(page, "locator"):
        return True
    selectors = ".navbar, nav, #main-container, .profile-header, #user-games"
    try:
        count = page.locator(selectors).count()
        return isinstance(count, int) and count > 0
    except Exception:
        return False


def _is_target_page_ready(page: Any) -> bool:
    """Check if the target Backloggd page or 404 page content is loaded and ready."""
    try:
        if _has_game_covers(page):
            return True

        title = _normalize_text(page.title()) if hasattr(page, "title") else ""
        if ("backloggd" in title or "404" in title) and _has_page_navigation(page):
            with contextlib.suppress(Exception):
                page.wait_for_timeout(500)
            return True
    except Exception:
        pass
    return False


def _log_challenge_resolution(challenge_detected: bool, start_time: float) -> None:
    """Logs elapsed time if a challenge was previously detected and solved."""
    if challenge_detected:
        elapsed = time.monotonic() - start_time
        logger.info(f"Anti-bot challenge solved in {elapsed:.2f}s!")


def _wait_for_challenge_resolution(page: Any, url: str, timeout: int = 30) -> bool:
    """Wait for anti-bot / PoW challenge to complete and page content to be ready."""
    start_time = time.monotonic()
    challenge_detected = False

    while time.monotonic() - start_time < timeout:
        if is_challenge_error_page(page):
            logger.warning(f"Anti-bot challenge error/rejection detected on {url}.")
            return False

        if is_challenge_page(page):
            if not challenge_detected:
                logger.info(
                    f"Anti-bot/Anubis challenge detected on {url}. Waiting up to {timeout}s for PoW solution..."
                )
                challenge_detected = True
            with contextlib.suppress(Exception):
                page.wait_for_timeout(500)
            continue

        if _is_target_page_ready(page):
            _log_challenge_resolution(challenge_detected, start_time)
            return True

        with contextlib.suppress(Exception):
            page.wait_for_timeout(500)

    if challenge_detected or is_challenge_page(page):
        logger.error(f"Anti-bot challenge timed out after {timeout}s on {url}.")
        return False

    return _is_target_page_ready(page)


def _is_challenge_content(html: str) -> bool:
    """Check if the rendered HTML content is an anti-bot challenge or error screen."""
    soup = BeautifulSoup(html, "html.parser")
    title_text = _normalize_text(soup.title.get_text(strip=True)) if soup.title else ""
    return (
        any(sub in title_text for sub in CHALLENGE_ERROR_SUBSTRINGS)
        or ACCESS_DENIED_TEXT in html.lower()
        or soup.select_one("#anubis_challenge") is not None
    )


def _handle_navigation_wait(page: Any, url: str) -> None:
    """Wait for page load state when a navigation transition is detected during content retrieval."""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=10000)
        page.wait_for_timeout(1000)
    except Exception as wait_err:
        logger.warning(f"Error waiting for load state on {url}: {wait_err}")


def _read_page_content(page: Any, url: str, max_read_attempts: int = 3) -> str | None:
    """Attempts to read and validate HTML content from the page, handling navigating states."""
    for attempt in range(max_read_attempts):
        try:
            html = page.content()
            if _is_challenge_content(html):
                logger.warning(f"Retrieved content is still challenge/error on {url}.")
                return None
            return html
        except Exception as e:
            if "navigat" in str(e).lower():
                logger.info(
                    f"Page {url} is navigating (attempt {attempt + 1}/{max_read_attempts}), waiting for load state..."
                )
                _handle_navigation_wait(page, url)
            else:
                logger.warning(f"Error retrieving content for page {url}: {e}")
                return None
    return None


def _retry_delay(page: Any) -> None:
    """Wait for 1s between retries."""
    with contextlib.suppress(Exception):
        page.wait_for_timeout(1000)


def _fetch_page_content(
    page: Any, url: str, challenge_timeout: int = 30, max_retries: int = 3
) -> str | None:
    """Navigates to URL and returns page content HTML, or None on error."""
    for attempt in range(1, max_retries + 1):
        try:
            _ = page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            logger.warning(
                f"Timeout/error fetching page {url} (attempt {attempt}/{max_retries}): {e}"
            )

        if not _wait_for_challenge_resolution(page, url, timeout=challenge_timeout):
            if attempt < max_retries:
                logger.warning(
                    f"Challenge resolution failed on {url} (attempt {attempt}/{max_retries}). Retrying in 1s..."
                )
                _retry_delay(page)
                continue
            logger.error(f"Failed to resolve challenge on {url} after {max_retries} attempts.")
            return None

        html = _read_page_content(page, url)
        if html is not None:
            return html

        if attempt < max_retries:
            logger.warning(
                f"Page content retrieval failed on {url} (attempt {attempt}/{max_retries}). Retrying..."
            )
            _retry_delay(page)

    return None


def _process_page_covers(
    covers: list[Any],
    cutoff_date: date,
    cat_type: str,
    seen_urls: set[str],
    seen_titles: set[tuple[str, date | None]],
) -> list[dict[str, Any]]:
    """Extracts valid, non-duplicate game entries from a list of cover elements."""
    new_games = []
    for c in covers:
        game = _extract_game_entry(c, cutoff_date, category_type=cat_type)
        if not game:
            continue

        url_key = game["url"]
        title_key = (game["title"].strip().lower(), game["release_date"])

        if (url_key and url_key in seen_urls) or (title_key in seen_titles):
            logger.debug(f"Skipping duplicate entry '{game['title']}' ({url_key})")
            continue

        if url_key:
            seen_urls.add(url_key)
        seen_titles.add(title_key)

        new_games.append(game)
        logger.info(
            f"Added {cat_type}: {game['title']} | Release: {game['release_date_raw']} ({game['release_date']})"
        )
    return new_games


def _scrape_category_pages(
    page: Any,
    username: str,
    list_type: str,
    cat_type: str,
    cat_suffix: str,
    cutoff_date: date,
    max_pages: int,
    seen_urls: set[str],
    seen_titles: set[tuple[str, date | None]],
    challenge_timeout: int = 30,
) -> list[dict[str, Any]]:
    """Paginates and scrapes games for a single list type and category bucket."""
    logger.info(f"Fetching list '{list_type}' (category: {cat_type}) for user '{username}'...")
    category_games = []

    for page_num in range(1, max_pages + 1):
        url = f"{BASE_URL}/u/{username}/games/release/type:{list_type}{cat_suffix}?page={page_num}"
        logger.info(f"Fetching Backloggd page {page_num} [{list_type}/{cat_type}]: {url}")

        html = _fetch_page_content(page, url, challenge_timeout=challenge_timeout)
        if not html:
            break

        soup = BeautifulSoup(html, "html.parser")
        title_text = _normalize_text(soup.title.get_text(strip=True)) if soup.title else ""
        if (
            any(sub in title_text for sub in CHALLENGE_ERROR_SUBSTRINGS)
            or ACCESS_DENIED_TEXT in html.lower()
            or any(sub in title_text for sub in CHALLENGE_TITLE_SUBSTRINGS)
            or soup.select_one("#anubis_challenge") is not None
            or soup.select_one("script[src*='anubis']") is not None
        ):
            logger.error(f"Anti-Bot challenge blocked access to Backloggd (title: '{title_text}').")
            break

        if "404" in title_text or "uh-oh!" in html.lower():
            logger.warning(
                f"Backloggd page returned 404 Not Found for user '{username}' ({url}). Please verify that the username is correct."
            )
            break

        covers = soup.select(".game-cover")
        if not covers:
            logger.info(
                f"No more entries found for [{list_type}/{cat_type}] on page {page_num}. Ending pagination."
            )
            break

        logger.info(f"Page {page_num} [{list_type}/{cat_type}] contains {len(covers)} entries.")

        page_games = _process_page_covers(
            covers=covers,
            cutoff_date=cutoff_date,
            cat_type=cat_type,
            seen_urls=seen_urls,
            seen_titles=seen_titles,
        )
        category_games.extend(page_games)

        if len(covers) < 40:
            break

    return category_games


def fetch_backloggd_wishlist(
    username: str,
    days_back: int = 30,
    headless: bool = True,
    max_pages: int = 50,
    include_extras: bool = True,
    list_types: list[str] | str = "wishlist",
    challenge_timeout: int = 30,
) -> list[dict[str, Any]]:
    """
    Fetches games from a user's Backloggd lists (default wishlist) sorted by release date.
    Fetches both Base Games and Extras (DLC, Expansions, etc.) to ensure all items are included.
    Filters games to include those released from `days_back` days ago up to all future dates.
    """
    cutoff_date = date.today() - timedelta(days=days_back)
    logger.info(
        f"Filtering Backloggd items with release dates >= {cutoff_date} ({days_back} days ago)"
    )

    if isinstance(list_types, str):
        types_to_scrape = [t.strip() for t in list_types.split(",") if t.strip()]
    else:
        types_to_scrape = list(list_types)

    categories_to_scrape = [("base", "")]
    if include_extras:
        categories_to_scrape.append(("extra", ";categories:extras"))

    scraped_games = []
    seen_urls: set[str] = set()
    seen_titles: set[tuple[str, date | None]] = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
        )

        page = context.new_page()
        page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        for list_type in types_to_scrape:
            for cat_type, cat_suffix in categories_to_scrape:
                games = _scrape_category_pages(
                    page=page,
                    username=username,
                    list_type=list_type,
                    cat_type=cat_type,
                    cat_suffix=cat_suffix,
                    cutoff_date=cutoff_date,
                    max_pages=max_pages,
                    seen_urls=seen_urls,
                    seen_titles=seen_titles,
                    challenge_timeout=challenge_timeout,
                )
                scraped_games.extend(games)

        browser.close()

    return scraped_games
