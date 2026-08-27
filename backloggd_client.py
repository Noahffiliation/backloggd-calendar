"""
Playwright-based client for scraping Backloggd wishlist games and release dates.
Handles Anubis proof-of-work anti-bot protection and pagination.
"""

import contextlib
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from dateutil import parser
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
BASE_URL = "https://backloggd.com"


def parse_release_date(date_str: str) -> date | None:
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


def _fetch_page_content(page: Any, url: str) -> str | None:
    """Navigates to URL and returns page content HTML, or None on error."""
    try:
        _ = page.goto(url, wait_until="domcontentloaded", timeout=30000)
    except Exception as e:
        logger.warning(f"Timeout/error fetching page {url}: {e}")

    with contextlib.suppress(Exception):
        page.wait_for_timeout(2000)

    for attempt in range(3):
        try:
            return page.content()
        except Exception as e:
            err_msg = str(e).lower()
            if "navigating" in err_msg:
                logger.info(
                    f"Page {url} is navigating (attempt {attempt + 1}/3), waiting for load state..."
                )
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=10000)
                    page.wait_for_timeout(1000)
                except Exception as wait_err:
                    logger.warning(f"Error waiting for load state on {url}: {wait_err}")
            else:
                logger.warning(f"Error retrieving content for page {url}: {e}")
                break

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
) -> list[dict[str, Any]]:
    """Paginates and scrapes games for a single list type and category bucket."""
    logger.info(f"Fetching list '{list_type}' (category: {cat_type}) for user '{username}'...")
    category_games = []

    for page_num in range(1, max_pages + 1):
        url = f"{BASE_URL}/u/{username}/games/release/type:{list_type}{cat_suffix}?page={page_num}"
        logger.info(f"Fetching Backloggd page {page_num} [{list_type}/{cat_type}]: {url}")

        html = _fetch_page_content(page, url)
        if not html:
            break

        soup = BeautifulSoup(html, "html.parser")
        title_text = soup.title.get_text(strip=True) if soup.title else ""
        if "Oh noes!" in title_text or "Access Denied" in html:
            logger.error("Anubis Anti-Bot challenge blocked access to Backloggd.")
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
                )
                scraped_games.extend(games)

        browser.close()

    return scraped_games
