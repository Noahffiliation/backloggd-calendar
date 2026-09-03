# Backloggd Wishlist Calendar Sync

Synchronize your [Backloggd](https://backloggd.com) game wishlist with Google Calendar and generate `.ics` (iCal) calendar feeds for game release dates.

Features games with release dates from **30 days ago (1 month ago)** through **all known future release dates**.

## Features

- 🎮 **Comprehensive Backloggd Scraping**: Automatically fetches all items from your wishlist/backlog, including **Base Games** and **Extras** (DLCs, Expansions, Standalone Expansions, Editions, and Updates).
- 🛡️ **Anubis Anti-Bot Bypass**: Uses Playwright headless Chromium to bypass Backloggd's Anubis proof-of-work challenge seamlessly.
- 📅 **iCal File Generation**: Builds standard `.ics` calendar files containing release events, descriptions, categories, and game URLs.
- 🔄 **Direct Google Calendar Sync**: Automatically creates/updates events on a dedicated Google Calendar via Service Account or OAuth credentials.
- 🤖 **GitHub Actions Automation**: Includes a workflow to automatically run the sync on a schedule and upload calendar `.ics` artifacts.

## Installation & Setup

1. **Clone & Environment Setup**:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate  # On Windows
   # source .venv/bin/activate  # On Linux/macOS
   pip install -r requirements.txt
   playwright install chromium
   ```

2. **Configure Environment Variables**:
   Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
   Set your Backloggd username and preferences:
   ```env
   BACKLOGGD_USERNAME=papaver_
   DAYS_BACK=30
   OUTPUT_FILE=backloggd_wishlist.ics
   INCLUDE_EXTRAS=true
   BACKLOGGD_LIST_TYPES=wishlist
   ```

## How to Add to Google Calendar ("Other Calendars")

You can add **Backloggd Wishlist Releases** as a secondary calendar under **"Other calendars"** in Google Calendar using either of two methods:

### Method 1: Direct Google Calendar Sync (Recommended)

When using `SYNC_GOOGLE=true` with a Google Service Account (`SERVICE_ACCOUNT_JSON`):
1. The sync script automatically creates a separate calendar named **Backloggd Wishlist Releases**.
2. It automatically shares the calendar with your primary email (`GOOGLE_SHARE_EMAIL`).
3. Open [Google Calendar](https://calendar.google.com) on your computer or phone. Under **"Other calendars"** in the left sidebar, you will see **Backloggd Wishlist Releases**.

### Method 2: Subscribe via iCal (.ics) URL

If hosting the `backloggd_wishlist.ics` file on GitHub Pages, GitHub Releases, or a personal server:
1. Open [Google Calendar](https://calendar.google.com).
2. Next to **"Other calendars"** in the left sidebar, click the **`+`** button and select **From URL**.
3. Enter the public URL to your `backloggd_wishlist.ics` file.
4. Click **Add calendar**. It will appear under **"Other calendars"** and update automatically.

---

## Usage

### Generate iCal Calendar File
Run the main script to fetch all wishlist items (base games + DLCs/expansions) and generate the `.ics` file:
```bash
python generate_ical.py --username papaver_
```

Additional CLI Options:
- `--include-extras` / `--no-extras`: Enable or disable fetching Extras/DLCs (default: enabled).
- `--list-types`: Comma-separated Backloggd lists to sync (default: `wishlist`, e.g., `--list-types wishlist,backlog`).
- `--days-back`: Number of days in the past to include (default: 30).

### Sync to Google Calendar
To sync directly to Google Calendar, provide a `service_account.json` or `credentials.json` file in the project directory, then run:
```bash
python generate_ical.py --username papaver_ --sync-google
```

Optional Google Sync Environment Variables:
```env
SYNC_GOOGLE=true
GOOGLE_SHARE_EMAIL=your_email@gmail.com
GOOGLE_CALENDAR_ID=your_custom_calendar_id
INCLUDE_EXTRAS=true
BACKLOGGD_LIST_TYPES=wishlist
```

## GitHub Actions Automated Sync

The repository includes a GitHub Actions workflow in `.github/workflows/sync.yml` that runs every 8 hours.

### Setting Up Secrets in GitHub:
Add the following Repository Secrets under **Settings > Secrets and variables > Actions**:
- `BACKLOGGD_USERNAME`: Your Backloggd username.
- `SERVICE_ACCOUNT_JSON`: (Optional) Full JSON string of your Google Service Account key file.
- `SYNC_GOOGLE`: (Optional) Set to `true` to enable direct Google Calendar sync.
- `GOOGLE_SHARE_EMAIL`: (Optional) Your Google email to auto-share the calendar with.

## Project Structure

- `generate_ical.py`: CLI entry point coordinating scraping, iCal generation, and Google Calendar sync.
- `backloggd_client.py`: Playwright scraper with Anubis anti-bot bypass and release date parser.
- `ical_builder.py`: iCalendar (`.ics`) builder and exporter.
- `google_sync.py`: Google Calendar API integration module.
- `.github/workflows/sync.yml`: GitHub Actions automated workflow.
