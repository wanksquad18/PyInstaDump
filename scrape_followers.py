# scrape_followers.py
# Scrapes followers of a target Instagram account, then scrapes each follower's bio.
# Usage on GitHub Actions:
#  - set secret COOKIES (cookie string like "csrftoken=...; sessionid=...; ...")
#  - either set environment var TARGET_USERNAME or create usernames.txt with the target username on the first line
# Output: data/results.csv with columns: username, biography, is_private, profile_url

import os
import csv
import asyncio
import time
from typing import List, Dict, Any
from playwright.async_api import async_playwright, TimeoutError as PWTimeout

# CONFIG
USERNAMES_FILE = "usernames.txt"      # optional: put target username here (first line)
OUT_DIR = "data"
OUT_FILE = os.path.join(OUT_DIR, "results.csv")
COOKIES_ENV = os.environ.get("COOKIES", "")
TARGET_USERNAME_ENV = os.environ.get("TARGET_USERNAME", "").strip()
FOLLOWERS_LIMIT = int(os.environ.get("FOLLOWERS_LIMIT", "1000"))  # how many followers to fetch (max)
SCROLL_PAUSE = float(os.environ.get("SCROLL_PAUSE", "0.6"))     # pause between scrolls in seconds
PROFILE_DELAY = float(os.environ.get("PROFILE_DELAY", "0.8"))    # delay between visiting follower profiles

# Helpers
def cookie_string_to_list(cookie_str: str):
    parts = [p.strip() for p in cookie_str.split(";") if p.strip()]
    cookies = []
    for p in parts:
        if "=" not in p:
            continue
        name, value = p.split("=", 1)
        cookies.append({
            "name": name,
            "value": value,
            "domain": ".instagram.com",
            "path": "/",
            "httpOnly": False,
            "secure": True
        })
    return cookies

async def open_followers_modal(page, target_username: str) -> None:
    profile_url = f"https://www.instagram.com/{target_username}/"
    await page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
    try:
        await page.wait_for_selector("header", timeout=20000)
    except PWTimeout:
        print("Timeout waiting for profile header.")
    try:
        btn = await page.query_selector('header a[href$="/followers/"]')
        if not btn:
            btn = await page.query_selector('//header//a[contains(translate(., "FOLLOWERS", "followers"), "followers")]')
        if not btn:
            btns = await page.query_selector_all("header ul li a")
            if len(btns) >= 2:
                btn = btns[1]
        if not btn:
            raise Exception("Followers link/button not found on profile page.")
        await btn.click()
    except Exception as e:
        raise Exception(f"Failed to open followers modal: {e}")

async def scrape_followers_from_modal(page, limit:int) -> List[str]:
    await page.wait_for_selector('div[role="dialog"] ul', timeout=15000)
    dialog = await page.query_selector('div[role="dialog"]')
    if not dialog:
        raise Exception("Followers dialog not found.")
    scrollable = await dialog.query_selector('ul')
    if not scrollable:
        scrollable = await dialog.query_selector('div[role="dialog"] div:nth-child(2)')
    if not scrollable:
        raise Exception("Scrollable followers list not found.")
    usernames = []
    previous_count = 0
    attempts = 0
    while len(usernames) < limit:
        items = await scrollable.query_selector_all('li')
        for it in items:
            try:
                a = await it.query_selector('a')
                if not a:
                    continue
                href = await a.get_attribute('href')
                if not href:
                    continue
                uname = href.strip("/").split("/")[-1]
                if uname and (not usernames or uname != usernames[-1]) and uname not in usernames:
                    usernames.append(uname)
            except Exception:
                continue
            if len(usernames) >= limit:
                break
        if len(usernames) == previous_count:
            attempts += 1
        else:
            attempts = 0
        if attempts >= 5:
            break
        previous_count = len(usernames)
        await page.evaluate('(el) => { el.scrollTop = el.scrollHeight; }', scrollable)
        await asyncio.sleep(SCROLL_PAUSE)
    return usernames[:limit]

async def fetch_profile_info(page, username:str) -> Dict[str,Any]:
    url = f"https://www.instagram.com/{username}/"
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except PWTimeout:
        pass
    try:
        resp = await page.evaluate(
            """async (u) => {
                try {
                    const url = `/api/v1/users/web_profile_info/?username=${u}`;
                    const r = await fetch(url, { credentials: 'same-origin' });
                    if (!r.ok) return { ok: false, status: r.status, text: await r.text() };
                    return { ok: true, data: await r.json() };
                } catch (e) {
                    return { ok: false, error: String(e) };
                }
            }""",
            username
        )
        # resp in JS structure; map to python dict if possible
        if isinstance(resp, dict) and resp.get("ok") and resp.get("data"):
            user = resp["data"].get("data", {}).get("user")
            if user:
                return {
                    "username": user.get("username") or username,
                    "biography": user.get("biography") or "",
                    "is_private": bool(user.get("is_private")),
                    "profile_url": url
                }
    except Exception:
        pass
    try:
        ld = await page.query_selector('script[type="application/ld+json"]')
        if ld:
            txt = await ld.inner_text()
            import json as _json
            try:
                j = _json.loads(txt)
                bio = j.get("description") or ""
                uname = j.get("alternateName") or username
                body_text = await page.inner_text("body")
                is_private = "This Account is Private" in (body_text or "")
                return {"username": uname, "biography": bio, "is_private": bool(is_private), "profile_url": url}
            except Exception:
                pass
    except Exception:
        pass
    try:
        meta = await page.query_selector('meta[name="description"]')
        if meta:
            content = await meta.get_attribute('content') or ""
            is_private = "This Account is Private" in content
            return {"username": username, "biography": content, "is_private": bool(is_private), "profile_url": url}
    except Exception:
        pass
    return {"username": username, "biography": "", "is_private": False, "profile_url": url}

async def main():
    target = TARGET_USERNAME_ENV
    if not target:
        if os.path.exists(USERNAMES_FILE):
            with open(USERNAMES_FILE, "r", encoding="utf-8") as f:
                first = f.readline().strip()
                if first:
                    target = first
    if not target:
        print("ERROR: No target username provided. Set TARGET_USERNAME env or add first line to usernames.txt")
        return 1

    print(f"Target username: {target}")
    if not COOKIES_ENV:
        print("WARNING: COOKIES env var is empty. You may be logged out or hit login redirects.")

    cookies = cookie_string_to_list(COOKIES_ENV)

    os.makedirs(OUT_DIR, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(viewport={"width":1200,"height":900})
        if cookies:
            try:
                await context.add_cookies([{
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c.get("domain", ".instagram.com").lstrip("."),
                    "path": c.get("path", "/"),
                    "httpOnly": c.get("httpOnly", False),
                    "secure": c.get("secure", True)
                } for c in cookies])
                print(f"Added {len(cookies)} cookies.")
            except Exception as e:
                print("Failed to add cookies:", e)
        page = await context.new_page()

        try:
            await open_followers_modal(page, target)
        except Exception as e:
            print("Failed to open followers modal:", e)
            await browser.close()
            return 1

        await asyncio.sleep(1.0)
        print("Collecting follower usernames (this may take a while)...")
        follower_usernames = await scrape_followers_from_modal(page, FOLLOWERS_LIMIT)
        print(f"Collected {len(follower_usernames)} follower usernames (limit={FOLLOWERS_LIMIT})")

        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass

        results = []
        for idx, uname in enumerate(follower_usernames, start=1):
            try:
                info = await fetch_profile_info(page, uname)
                print(f"[{idx}/{len(follower_usernames)}] {uname} private={info.get('is_private')}")
                results.append(info)
            except Exception as e:
                print(f"Error fetching profile for {uname}: {e}")
                results.append({"username": uname, "biography": "", "is_private": True, "profile_url": f"https://www.instagram.com/{uname}/"})
            await asyncio.sleep(PROFILE_DELAY)

        await browser.close()

    with open(OUT_FILE, "w", newline="", encoding="utf-8") as csvfile:
        fieldnames = ["username", "biography", "is_private", "profile_url"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "username": r.get("username", ""),
                "biography": (r.get("biography") or "").replace("\n", " ").strip(),
                "is_private": "Yes" if r.get("is_private") else "No",
                "profile_url": r.get("profile_url", "")
            })
    print(f"Saved {len(results)} rows to {OUT_FILE}")
    return 0

if __name__ == "__main__":
    asyncio.run(main())
