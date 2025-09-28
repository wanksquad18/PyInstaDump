#!/usr/bin/env python3
"""
scrape_followers.py

Playwright-based follower scraper which:
 - loads Instagram session cookies (from env COOKIES or data/www.instagram.com.cookies.json)
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
COOKIES_ENV = os.environ.get("COOKIES")  # optional: stringified JSON of cookies
COOKIES_FILE = Path("data/www.instagram.com.cookies.json")  # optional file
HEADLESS = os.environ.get("HEADLESS", "true").lower() not in ("false", "0", "no")
PROFILE_DELAY = float(os.environ.get("PROFILE_DELAY", "0.8"))  # delay between profile visits (secs)
SCROLL_PAUSE = float(os.environ.get("SCROLL_PAUSE", "0.6"))
RESULTS_PATH = Path("data/results.csv")
DEBUG_DIR = Path("data")
TIMEOUT = int(os.environ.get("HEADER_TIMEOUT_MS", "45000"))  # ms for wait_for_selector

DEBUG_DIR.mkdir(parents=True, exist_ok=True)
Path("data").mkdir(parents=True, exist_ok=True)

# ----- Helpers -----


def load_cookies() -> Optional[List[Dict]]:
    """Load cookies from COOKIES env or cookie file. Return list or None."""
    if COOKIES_ENV:
        try:
            val = COOKIES_ENV.strip()
            # If it's a JSON array string, parse it
            cookies = json.loads(val)
            if isinstance(cookies, list):
                return cookies
            # maybe env contains object with field 'cookies'
            if isinstance(cookies, dict) and "cookies" in cookies:
                return cookies["cookies"]
        except Exception as e:
            print("Failed to parse COOKIES env as JSON:", e)
    if COOKIES_FILE.exists():
        try:
            with COOKIES_FILE.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
                if isinstance(data, list):
                    return data
                if isinstance(data, dict) and "cookies" in data:
                    return data["cookies"]
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
    # cookies expected in Playwright format: list of dicts including 'name','value','domain','path' optionally expires/httpOnly/secure/sameSite
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
    # follower link has text like "followers" under profile area; we target anchor with href ending with /followers/
    try:
        # try find button/anchor that leads to followers dialog
        # multiple selector strategies because IG changes UI frequently
        follower_selector_options = [
            f'a[href="/{target}/followers/"]',
            'a[href$="/followers/"]',  # fallback
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
        # click it
        await btn.click()
        # wait for modal dialog to appear (a div with role=dialog or ul with role=listbox). Use timeout
        await page.wait_for_selector('div[role="dialog"] ul', timeout=TIMEOUT)
        # return modal root
        modal = await page.query_selector('div[role="dialog"] ul')
        return modal
    except Exception as e:
        raise e


async def scroll_followers_modal(page: Page, modal, limit: int) -> List[str]:
    """Scroll the followers modal to collect follower usernames (strings)."""
    usernames = []
    prev_count = 0
    scroll_retries = 0
    MAX_RETRIES = 6
    # modal is the ul element that contains li elements; we will repeatedly evaluate JS to scroll it
    while len(usernames) < limit and scroll_retries < MAX_RETRIES:
        # read currently visible usernames
        try:
            items = await page.query_selector_all('div[role="dialog"] ul li')
            for item in items:
                # username is anchor text in li -> a
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
        # scroll modal down
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
        # wait a bit to let new items load
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
        # read simple selectors
        # bio: within <div> with role presentation and contains span (IG structure changes frequently)
        # attempt a few strategies:
        bio = ""
        try:
            el = await page.query_selector('div[data-testid="user-bio"]')
            if el:
                bio = (await (await el.get_property("textContent")).json_value()).strip()
        except Exception:
            bio = ""
        if not bio:
            # fallback selectors:
            try:
                bio_el = await page.query_selector('header section div div span')
                if bio_el:
                    bio = (await (await bio_el.get_property("textContent")).json_value()).strip()
            except Exception:
                bio = ""
        data["bio"] = bio or ""
        # private / verified / profile pic
        try:
            # private - often appears as "This Account is Private" text, or private profiles have a locked icon
            private_el = await page.query_selector('article header div:has(svg[aria-label="Private"])')
            data["is_private"] = "Yes" if private_el else "No"
        except Exception:
            data["is_private"] = ""
        # verified - check for verified badge (svg with title or aria-label)
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
        # apply cookies if present
        if cookies:
            # ensure cookie domains are compatible with Playwright (must include url or domain/path)
            await apply_cookies(context, cookies)
        page = await context.new_page()
        # accept standard navigation timeouts
        page.set_default_timeout(TIMEOUT)

        # go to instagram and ensure base content is reachable
        try:
            await page.goto("https://www.instagram.com/", wait_until="networkidle")
            print("Reached instagram.com")
        except Exception as e:
            print("Failed to reach Instagram homepage:", e)

        # open target profile and followers modal
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

        # scroll modal to collect usernames
        print("Collecting usernames from followers modal...")
        usernames = await scroll_followers_modal(page, modal, FOLLOWERS_LIMIT)
        print(f"Collected {len(usernames)} usernames from modal (limit {FOLLOWERS_LIMIT})")

        # Optionally visit each profile to collect bio + flags
        results = []
        for i, uname in enumerate(usernames, 1):
            # light delay to reduce suspicious load
            try:
                profile_data = await fetch_profile_data(page, uname)
            except Exception as e:
                print(f"Error fetching profile for {uname}: {e}")
                profile_data = {"username": uname, "bio": "", "is_private": "", "is_verified": "", "profile_pic_url": ""}
            results.append(profile_data)
            if i % 10 == 0:
                print(f"Visited {i}/{len(usernames)} profiles")
            await asyncio.sleep(PROFILE_DELAY)

        # write CSV results
        write_results_csv(results, RESULTS_PATH)

        await browser.close()
        return 0


if __name__ == "__main__":
    code = asyncio.run(run())
    # exit code 0 or 1 helps CI interpret results
    exit(code)
