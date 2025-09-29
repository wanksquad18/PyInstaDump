#!/usr/bin/env python3
"""
scrape_followers.py

Playwright-based follower scraper which:
 - loads Instagram session cookies (from env COOKIES or COOKIES_SECRET or data/www.instagram.com.cookies.json)
 - opens target profile (env TARGET_USERNAME)
 - opens followers modal, scrolls and extracts follower usernames (and optionally visits profile for bio)
 - saves results to data/results.csv
 - saves debug screenshot/html if followers modal opening fails
"""

import os
import asyncio
import json
import csv
import time
from typing import List, Dict, Optional
from pathlib import Path

from playwright.async_api import async_playwright, Page, Browser, TimeoutError as PWTimeout

# ----- Config (controlled via env) -----
TARGET = os.environ.get("TARGET_USERNAME") or os.environ.get("target_username") or "thepreetjohal"
FOLLOWERS_LIMIT = int(os.environ.get("FOLLOWERS_LIMIT", "500"))  # how many followers to fetch
# Script will try COOKIES, then COOKIES_SECRET, then data/www.instagram.com.cookies.json
COOKIES_ENV = os.environ.get("COOKIES") or os.environ.get("COOKIES_SECRET")
COOKIES_FILE = Path("data/www.instagram.com.cookies.json")  # optional file fallback
HEADLESS = os.environ.get("HEADLESS", "true").lower() not in ("false", "0", "no")
PROFILE_DELAY = float(os.environ.get("PROFILE_DELAY", "0.8"))  # delay between profile visits (secs)
SCROLL_PAUSE = float(os.environ.get("SCROLL_PAUSE", "0.6"))
RESULTS_PATH = Path("data/results.csv")
DEBUG_DIR = Path("data")
TIMEOUT = int(os.environ.get("HEADER_TIMEOUT_MS", "45000"))  # ms for wait_for_selector

# ensure folders
DEBUG_DIR.mkdir(parents=True, exist_ok=True)
Path("data").mkdir(parents=True, exist_ok=True)


# ----- Helpers -----


def load_cookies() -> Optional[List[Dict]]:
    """Load cookies from COOKIES env (or COOKIES_SECRET) or cookie file. Return list or None."""
    # Try environment first
    raw = COOKIES_ENV
    if raw:
        try:
            parsed = json.loads(raw)
            # cookie array
            if isinstance(parsed, list):
                return parsed
            # maybe an object with 'cookies' field
            if isinstance(parsed, dict) and "cookies" in parsed:
                return parsed["cookies"]
            # if it's a dict of cookie-name:value, convert to Playwright format? unlikely; we skip
            print("COOKIES env parsed but shape unexpected (not list).")
        except Exception as e:
            print("Failed to parse COOKIES env as JSON:", e)
    # Try cookie file
    if COOKIES_FILE.exists():
        try:
            with COOKIES_FILE.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "cookies" in data:
                    return data["cookies"]
                print("Cookie file parsed but shape unexpected.")
        except Exception as e:
            print("Failed to read cookies file:", e)
    return None


async def save_debug_artifacts(page: Page, target: str):
    ts = int(time.time())
    safe_name = target.replace("/", "_")
    try:
        png_path = DEBUG_DIR / f"debug_{safe_name}_{ts}.png"
        html_path = DEBUG_DIR / f"debug_{safe_name}_{ts}.html"
        await page.screenshot(path=str(png_path), full_page=True)
        html = await page.content()
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"Saved debug screenshot and html: {png_path} / {html_path}")
    except Exception as e:
        print("Failed to save debug artifacts:", e)


async def apply_cookies(context, cookies):
    # cookies expected in Playwright format: list of dicts including 'name','value','domain','path'
    try:
        await context.add_cookies(cookies)
        print(f"Applied {len(cookies)} cookies to context")
    except Exception as e:
        print("Failed to add cookies to context:", e)


def write_results_csv(rows: List[Dict], path: Path):
    if not rows:
        print("No rows to write")
        return
    keys = ["username", "bio", "is_private", "is_verified", "profile_pic_url"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in keys})
    print(f"Wrote {len(rows)} rows to {path}")


# ----- Scraping logic -----


async def open_followers_modal(page: Page, target: str):
    """Open the followers modal for the target. Returns the modal element handle or raises."""
    profile_url = f"https://www.instagram.com/{target}/"
    await page.goto(profile_url, wait_until="networkidle")
    # Wait for follower link to be present
    try:
        follower_selector_options = [
            f'a[href="/{target}/followers/"]',
            'a[href$="/followers/"]',
            'li a[href*="/followers"]',
            'section a[href*="/followers"]',
            'button:has-text("followers")',
        ]
        btn = None
        for sel in follower_selector_options:
            try:
                btn = await page.wait_for_selector(sel, timeout=TIMEOUT)
                if btn:
                    break
            except Exception:
                btn = None
        if not btn:
            raise Exception("Could not find followers link/button on profile")
        await btn.click()
        # wait for modal dialog to appear
        await page.wait_for_selector('div[role="dialog"] ul', timeout=TIMEOUT)
        modal = await page.query_selector('div[role="dialog"] ul')
        return modal
    except Exception as e:
        raise e


async def scroll_followers_modal(page: Page, modal, limit: int) -> List[str]:
    """Scroll the followers modal to collect follower usernames (strings)."""
    usernames = []
    prev_count = 0
    scroll_retries = 0
    MAX_RETRIES = 8
    while len(usernames) < limit and scroll_retries < MAX_RETRIES:
        try:
            items = await page.query_selector_all('div[role="dialog"] ul li')
            for item in items:
                a = await item.query_selector('a[href^="/"]')
                if not a:
                    continue
                username = (await (await a.get_property("textContent")).json_value()).strip()
                if username and username not in usernames:
                    usernames.append(username)
                    if len(usernames) >= limit:
                        break
        except Exception as e:
            print("Error while extracting usernames from modal:", e)
        # scroll modal
        try:
            await page.evaluate(
                """() => {
                    const dlg = document.querySelector('div[role="dialog"] ul');
                    if (!dlg) return false;
                    dlg.parentElement.scrollTop = dlg.parentElement.scrollTop + 1000;
                    return true;
                }"""
            )
        except Exception as e:
            print("Scroll JS failed:", e)
        await asyncio.sleep(SCROLL_PAUSE)
        if len(usernames) == prev_count:
            scroll_retries += 1
        else:
            scroll_retries = 0
            prev_count = len(usernames)
    return usernames[:limit]


async def fetch_profile_data(page: Page, username: str) -> Dict:
    """Visit profile page briefly and parse biography and other metadata."""
    profile_url = f"https://www.instagram.com/{username}/"
    data = {"username": username, "bio": "", "is_private": "", "is_verified": "", "profile_pic_url": ""}
    try:
        await page.goto(profile_url, wait_until="networkidle")
        bio = ""
        try:
            # heuristic selectors for bio
            el = await page.query_selector('div[data-testid="user-bio"]')
            if el:
                bio = (await (await el.get_property("textContent")).json_value()).strip()
        except Exception:
            bio = ""
        if not bio:
            try:
                bio_el = await page.query_selector('header section div div span')
                if bio_el:
                    bio = (await (await bio_el.get_property("textContent")).json_value()).strip()
            except Exception:
                bio = ""
        data["bio"] = bio or ""
        # private detection (best-effort)
        try:
            private_text = await page.query_selector('//main//div[contains(text(), "This Account is Private")]')
            data["is_private"] = "Yes" if private_text else "No"
        except Exception:
            data["is_private"] = ""
        # verified detection (badge)
        try:
            verified = await page.query_selector('header svg[aria-label="Verified"]')
            data["is_verified"] = "Yes" if verified else "No"
        except Exception:
            data["is_verified"] = ""
        try:
            img = await page.query_selector('header img')
            if img:
                src = await (await img.get_property("src")).json_value()
                data["profile_pic_url"] = src or ""
        except Exception:
            data["profile_pic_url"] = ""
    except Exception as e:
        print(f"Profile visit failed for {username}: {e}")
    return data


async def run():
    cookies = load_cookies()
    if not cookies:
        print("Warning: no cookies found. Some profiles may be restricted or Instagram may block access.")
    print(f"Target: {TARGET}, limit: {FOLLOWERS_LIMIT}, headless: {HEADLESS}")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = await browser.new_context()
        if cookies:
            await apply_cookies(context, cookies)
        page = await context.new_page()
        page.set_default_timeout(TIMEOUT)

        # try homepage
        try:
            await page.goto("https://www.instagram.com/", wait_until="networkidle")
            print("Reached instagram.com")
        except Exception as e:
            print("Failed to reach Instagram homepage:", e)

        # open modal
        try:
            modal = await open_followers_modal(page, TARGET)
        except Exception as e:
            print("Failed to open followers modal:", e)
            # debug capture
            try:
                await save_debug_artifacts(page, TARGET)
            except Exception as inner:
                print("Failed saving debug artifacts:", inner)
            await browser.close()
            return 1

        print("Collecting usernames from followers modal...")
        usernames = await scroll_followers_modal(page, modal, FOLLOWERS_LIMIT)
        print(f"Collected {len(usernames)} usernames from modal (limit {FOLLOWERS_LIMIT})")

        results = []
        for i, uname in enumerate(usernames, 1):
            try:
                profile_data = await fetch_profile_data(page, uname)
            except Exception as e:
                print(f"Error fetching profile for {uname}: {e}")
                profile_data = {"username": uname, "bio": "", "is_private": "", "is_verified": "", "profile_pic_url": ""}
            results.append(profile_data)
            if i % 10 == 0:
                print(f"Visited {i}/{len(usernames)} profiles")
            await asyncio.sleep(PROFILE_DELAY)

        write_results_csv(results, RESULTS_PATH)

        await browser.close()
        return 0


if __name__ == "__main__":
    code = asyncio.run(run())
    exit(code)
