#!/usr/bin/env python3
"""
scrape_followers.py
- Reads cookies from env COOKIES_SECRET (JSON array string).
- Opens Instagram profile, opens followers modal, scrolls to collect follower usernames.
- Visits each follower profile to extract biography and whether account is private.
- Writes results to data/results.csv
- Saves debug artifacts (screenshot + html) in data/ when something fails opening followers modal.
Usage:
  - locally: export COOKIES_SECRET="$(cat www.instagram.com.cookies.json)"
  - on GH Actions: set secret COOKIES_SECRET to the entire cookies JSON string
"""

import os
import sys
import json
import time
import csv
import asyncio
from typing import List, Dict, Any, Optional
from pathlib import Path

from playwright.async_api import async_playwright, Browser, Page, Error as PWError

# CONFIG: adjust via env if needed
FOLLOWERS_LIMIT = int(os.getenv("FOLLOWERS_LIMIT", "1000"))  # cap how many followers to collect
SCROLL_PAUSE = float(os.getenv("SCROLL_PAUSE", "0.6"))       # pause between scrolls
PROFILE_DELAY = float(os.getenv("PROFILE_DELAY", "0.8"))     # delay between visiting profile pages
HEADER_TIMEOUT_MS = int(os.getenv("HEADER_TIMEOUT_MS", "45000"))
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

COOKIES_ENV_NAME = "COOKIES_SECRET"  # user told me this is the secret name

# Helper: write CSV
def write_csv(rows: List[Dict[str, Any]], path: Path):
    if not rows:
        print("No rows to write.")
        return
    keys = ["username", "biography", "is_private"]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys)
        writer.writeheader()
        for r in rows:
            writer.writerow({k: r.get(k, "") for k in keys})
    print(f"Wrote {len(rows)} rows to {path}")


async def save_debug_artifacts(page: Page, safe_name: str):
    timestamp = int(time.time())
    png_path = DATA_DIR / f"debug_{safe_name}_{timestamp}.png"
    html_path = DATA_DIR / f"debug_{safe_name}_{timestamp}.html"
    try:
        await page.screenshot(path=str(png_path), full_page=True)
        html = await page.content()
        html_path.write_text(html, encoding="utf-8")
        print(f"Saved debug screenshot and html: {png_path} / {html_path}")
    except Exception as e:
        print("Failed to save debug artifacts:", e)


def normalize_cookies_input(cookie_env_value: str) -> Optional[List[Dict[str, Any]]]:
    """Try to parse cookies from env. Accepts either raw JSON array OR newline-escaped string."""
    if not cookie_env_value:
        return None
    try:
        parsed = json.loads(cookie_env_value)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    # fallback: sometimes secret stores newline-escaped JSON. Try replacing escaped newlines and parse again
    try:
        cleaned = cookie_env_value.replace("\\n", "\n")
        parsed = json.loads(cleaned)
        if isinstance(parsed, list):
            return parsed
    except Exception:
        pass
    # last resort: try to find first '[' and last ']' and parse substring
    try:
        start = cookie_env_value.find("[")
        end = cookie_env_value.rfind("]") + 1
        if start != -1 and end != -1:
            parsed = json.loads(cookie_env_value[start:end])
            if isinstance(parsed, list):
                return parsed
    except Exception:
        pass
    return None


async def add_cookies_to_context(context, cookies: List[Dict[str, Any]]):
    """Playwright expects cookies shaped with 'name','value','domain' etc. Convert if needed."""
    prepared = []
    for c in cookies:
        # Ensure required keys exist
        if "name" not in c or "value" not in c:
            continue
        cookie_obj = {
            "name": c["name"],
            "value": str(c["value"]),
            "domain": c.get("domain", ".instagram.com"),
            "path": c.get("path", "/"),
            # expires must be int (seconds) or omitted
        }
        if "expires" in c and isinstance(c["expires"], (int, float)):
            try:
                cookie_obj["expires"] = int(c["expires"])
            except Exception:
                pass
        # secure / httpOnly
        if "secure" in c:
            cookie_obj["secure"] = bool(c["secure"])
        if "httpOnly" in c:
            cookie_obj["httpOnly"] = bool(c["httpOnly"])
        prepared.append(cookie_obj)
    if not prepared:
        return
    await context.add_cookies(prepared)
    print(f"Injected {len(prepared)} cookies into browser context.")


async def open_followers_modal(page: Page, target_username: str) -> Optional[str]:
    """Open the followers modal for the given profile. Return the selector of the scrollable element or None on failure."""
    profile_url = f"https://www.instagram.com/{target_username}/"
    print("Navigating to", profile_url)
    try:
        await page.goto(profile_url, timeout=HEADER_TIMEOUT_MS)
        await page.wait_for_load_state("domcontentloaded", timeout=HEADER_TIMEOUT_MS)
    except PWError as e:
        print("Error loading profile page:", e)
        return None

    # Detect if Instagram redirected to login (common if cookies invalid)
    cur_url = page.url
    if "/accounts/login" in cur_url or "login" in cur_url:
        print("It looks like we were redirected to login page - cookies may be invalid.")
        return None

    # Try to click the followers link/button
    try:
        # search for <a> elements that lead to followers
        loc = None
        # first try an anchor whose href contains '/followers/'
        anchors = page.locator('a[href*="/followers/"]')
        if await anchors.count() > 0:
            loc = anchors.first()
        else:
            # fallback: find by text "followers" (case-insensitive)
            anchors_by_text = page.locator('a', has_text="followers")
            if await anchors_by_text.count() > 0:
                loc = anchors_by_text.first()
            else:
                # some UI shows as button: try "button" with "followers" text
                btns = page.locator('button', has_text="followers")
                if await btns.count() > 0:
                    loc = btns.first()

        if loc is None:
            print("Could not find followers link/button - page structure may have changed.")
            return None

        await loc.click()
        # Wait for the dialog
        dialog = page.locator('div[role="dialog"]')
        await dialog.wait_for(timeout=HEADER_TIMEOUT_MS)
        # Now find scrollable UL inside dialog
        # Many instagram versions have: div[role="dialog"] ul
        sel = 'div[role="dialog"] ul'
        ul = page.locator(sel)
        if await ul.count() == 0:
            # fallback: find div[role="dialog"] div that is scrollable
            possible = page.locator('div[role="dialog"] div')
            # pick the one with overflow-y style
            for i in range(await possible.count()):
                el = possible.nth(i)
                style = await el.get_attribute("style") or ""
                if "overflow" in style or "height" in style:
                    sel = f'div[role="dialog"] div >> nth={i}'
                    break
        print("Opened followers modal; using selector:", sel)
        return sel
    except Exception as e:
        print("Exception while trying to open followers modal:", e)
        await save_debug_artifacts(page, target_username.replace("/", "_"))
        return None


async def collect_usernames_from_followers_dialog(page: Page, scrollable_selector: str, limit: int) -> List[str]:
    """Scroll the followers dialog and return list of usernames found (max limit)."""
    usernames = []
    seen = set()

    # perform scrolling in page context to gather usernames from dialog
    prev_count = -1
    max_attempts_no_change = 5
    attempts_no_change = 0

    while len(usernames) < limit:
        # extract
        found = await page.evaluate(
            """(sel) => {
                const root = document.querySelector(sel);
                if (!root) return [];
                // find anchor tags inside list items
                const anchors = Array.from(root.querySelectorAll('a'));
                const res = [];
                for (const a of anchors) {
                    const href = a.getAttribute('href') || '';
                    // typical follower link is '/username/' - avoid non-user links
                    if (href && href.startsWith('/') && href.split('/').length >= 2) {
                        const uname = href.split('/')[1];
                        if (uname && uname.trim()) res.push(uname.trim());
                    } else if (a.innerText && a.innerText.trim() && !a.innerText.includes('followers')) {
                        // fallback: sometimes username is in anchor text
                        res.push(a.innerText.trim());
                    }
                }
                return Array.from(new Set(res));
            }""",
            scrollable_selector,
        )

        for u in found:
            if u not in seen:
                seen.add(u)
                usernames.append(u)
                if len(usernames) >= limit:
                    break

        # if no new were found, increment attempts_no_change
        if len(usernames) == prev_count:
            attempts_no_change += 1
        else:
            attempts_no_change = 0

        prev_count = len(usernames)
        if attempts_no_change >= max_attempts_no_change:
            print("No change after multiple scroll attempts, stopping.")
            break

        if len(usernames) >= limit:
            break

        # scroll down the dialog
        scrolled = await page.evaluate(
            """(sel) => {
                const root = document.querySelector(sel);
                if (!root) return false;
                root.scrollTop = root.scrollHeight;
                return true;
            }""",
            scrollable_selector,
        )
        if not scrolled:
            print("Could not scroll the dialog (selector not found) - stopping.")
            break

        await asyncio.sleep(SCROLL_PAUSE)

    print(f"Collected {len(usernames)} usernames (limit {limit}).")
    return usernames[:limit]


async def extract_profile_bio_and_private(page: Page, username: str) -> Dict[str, Any]:
    """Visit the profile page, extract biography (if present) and whether private."""
    url = f"https://www.instagram.com/{username}/"
    bio = ""
    is_private = False
    try:
        await page.goto(url, timeout=HEADER_TIMEOUT_MS)
        await page.wait_for_load_state("domcontentloaded", timeout=HEADER_TIMEOUT_MS)
        # if redirected to login
        if "/accounts/login" in page.url or "login" in page.url:
            # cannot retrieve info because cookies/session invalid
            print(f"Visiting {username} redirected to login; cookies/session may be invalid.")
            return {"username": username, "biography": "", "is_private": False}

        # detect private account: look for text "This Account is Private"
        # We'll check the page body text for that exact phrase
        body_text = await page.content()
        if "This Account is Private" in body_text or "is private" in body_text and "This Account" in body_text:
            is_private = True

        # Try meta description first (works often)
        try:
            meta_desc = await page.locator('meta[name="description"]').get_attribute("content")
            if meta_desc:
                # meta content often contains "Full Name (@username) • 123 posts, 456 Followers, 789 Following. Bio text"
                # We'll attempt to extract the bio part heuristically: after the "Followers" numbers may come bio text.
                # But often the format includes something like "123 Followers, 45 Following, Bio text"
                bio_candidate = meta_desc
                # crude: if 'Followers,' appears then take substring after last 'Followers,' or 'Following,' etc
                for token in ["Followers,", "Followers ·", "Followers"]:
                    if token in bio_candidate:
                        parts = bio_candidate.split(token, 1)
                        if len(parts) > 1:
                            candidate = parts[1].strip()
                            # remove trailing 'Instagram' phrases
                            candidate = candidate.replace("Instagram", "").strip()
                            if candidate:
                                bio = candidate
                                break
                if not bio:
                    # As fallback, use entire meta content but strip counts by slicing first sentence
                    bio = meta_desc
        except Exception:
            pass

        # If still empty, try to select page selectors that often contain bio
        if not bio:
            # try common selectors - these vary across IG versions
            locs = [
                'div.-vDIg span',       # older layout
                'section .-vDIg span',  # alternative
                'div[data-testid="user-bio"]',  # some versions
                'h1 + div > span',      # generic
            ]
            for sel in locs:
                try:
                    cnt = await page.locator(sel).all_text_contents()
                    join_text = " ".join([c.strip() for c in cnt if c and c.strip()])
                    if join_text:
                        bio = join_text.strip()
                        break
                except Exception:
                    continue

        # final safe trim
        bio = (bio or "").strip()
    except Exception as e:
        print(f"Error extracting profile for {username}:", e)
        # do not crash for a single profile
    await asyncio.sleep(PROFILE_DELAY)
    return {"username": username, "biography": bio, "is_private": bool(is_private)}


async def run_scrape(target_username: str):
    cookies_str = os.environ.get(COOKIES_ENV_NAME, "")
    cookies_list = normalize_cookies_input(cookies_str)
    if cookies_list is None:
        print(f"Warning: No cookies found in env {COOKIES_ENV_NAME} or could not parse them. You may be redirected to login.")
    else:
        print(f"Found {len(cookies_list)} cookies from env.")

    # Playwright
    async with async_playwright() as p:
        # Use chromium for best compatibility; enable args for running in GH Actions
        browser: Browser = await p.chromium.launch(headless=True, args=[
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--disable-gpu"
        ])
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"),
        )

        # inject cookies if provided
        if cookies_list:
            try:
                await add_cookies_to_context(context, cookies_list)
            except Exception as e:
                print("Failed to add cookies to context:", e)

        page = await context.new_page()
        page.set_default_navigation_timeout(HEADER_TIMEOUT_MS)
        page.set_default_timeout(HEADER_TIMEOUT_MS)

        # try opening followers modal
        scrollable_selector = await open_followers_modal(page, target_username)
        if not scrollable_selector:
            print("Failed to open followers modal. Saving debug artifacts and exiting.")
            await save_debug_artifacts(page, target_username.replace("/", "_"))
            await browser.close()
            return 2

        # collect usernames
        usernames = await collect_usernames_from_followers_dialog(page, scrollable_selector, FOLLOWERS_LIMIT)
        if not usernames:
            print("No usernames collected from followers modal.")
            await save_debug_artifacts(page, target_username.replace("/", "_"))
            await browser.close()
            return 3

        print(f"Collected {len(usernames)} usernames. Now extracting bios (this will visit each profile).")

        results = []
        # we will reuse a single page instance for visiting each profile to save resources
        for i, uname in enumerate(usernames, start=1):
            try:
                rec = await extract_profile_bio_and_private(page, uname)
                # only keep non-private profiles (if you want only public profiles, uncomment)
                # if not rec["is_private"]:
                results.append(rec)
                if i % 50 == 0:
                    print(f"Processed {i}/{len(usernames)} profiles...")
            except Exception as e:
                print("Error processing", uname, e)
                # continue

        # Write results
        out_path = DATA_DIR / "results.csv"
        write_csv(results, out_path)

        await browser.close()
        print("Done.")
        return 0


def usage_and_exit():
    print("Usage: set env COOKIES_SECRET to cookies JSON string, and set TARGET_USERNAME env var (or pass as arg).")
    print("Example:")
    print('  COOKIES_SECRET="$(cat www.instagram.com.cookies.json)" TARGET_USERNAME=someprofile python scrape_followers.py')
    sys.exit(1)


if __name__ == "__main__":
    # Accept target username from env or arg
    target = os.getenv("TARGET_USERNAME", "")
    if not target and len(sys.argv) > 1:
        target = sys.argv[1]
    if not target:
        print("Error: no target username supplied.")
        usage_and_exit()

    # run
    ret = asyncio.run(run_scrape(target))
    sys.exit(ret if isinstance(ret, int) else 0)
