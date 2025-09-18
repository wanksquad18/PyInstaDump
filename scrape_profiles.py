# scrape_profiles.py
# Playwright-based Instagram profile scraper for GitHub Actions
# Expects secret COOKIES to be set in Actions (csrftoken=...; sessionid=...; etc.)
# Reads usernames from usernames.txt (one per line)
# Outputs CSV data/results.csv with columns: username, biography, is_private

import os
import csv
import asyncio
import json
from typing import Dict, Any
from playwright.async_api import async_playwright

COOKIE_ENV = os.environ.get("COOKIES", "")

def cookie_string_to_list(cookie_str: str):
    """
    Convert "name=value; name2=value2" into list of dicts for Playwright add_cookies.
    Host/domain for Instagram should be ".instagram.com"
    """
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

async def fetch_profile_data(page, username: str) -> Dict[str, Any]:
    """
    Try to fetch profile JSON using Instagram's web API endpoint from the browser context.
    If that fails, fall back to parsing ld+json on the page.
    """
    profile_url = f"https://www.instagram.com/{username}/"
    await page.goto(profile_url, timeout=60_000)
    await page.wait_for_load_state("domcontentloaded", timeout=30_000)

    # first attempt: fetch web_profile_info endpoint (works when cookies are valid)
    try:
        resp = await page.evaluate(
            """async (username) => {
                try {
                    const url = `/api/v1/users/web_profile_info/?username=${username}`;
                    const r = await fetch(url, { credentials: 'same-origin' });
                    if (!r.ok) return { ok: false, status: r.status, text: await r.text() };
                    const j = await r.json();
                    return { ok: true, data: j };
                } catch (e) {
                    return { ok: false, error: String(e) };
                }
            }""",
            username
        )
        if isinstance(resp, dict) and resp.get("ok"):
            user = resp["data"].get("data", {}).get("user")
            if user:
                return {
                    "username": user.get("username") or username,
                    "biography": user.get("biography") or "",
                    "is_private": bool(user.get("is_private")),
                }
    except Exception:
        # proceed to fallback
        pass

    # fallback: read ld+json script
    try:
        ld = await page.evaluate(
            """() => {
                const s = document.querySelector('script[type="application/ld+json"]');
                if (!s) return null;
                try { return JSON.parse(s.innerText); } catch(e) { return null; }
            }"""
        )
        if ld and isinstance(ld, dict):
            bio = ld.get("description") or ld.get("caption") or ""
            uname = ld.get("alternateName") or username
            # if page contains "This Account is Private" text, detect privacy
            is_private = False
            body_text = await page.evaluate("() => document.body.innerText")
            if "This Account is Private" in (body_text or ""):
                is_private = True
            return {"username": uname, "biography": bio, "is_private": bool(is_private)}
    except Exception:
        pass

    # final fallback: scan meta description
    try:
        meta = await page.evaluate(
            """() => {
                const m = document.querySelector('meta[name=\"description\"]');
                return m ? m.getAttribute('content') : null;
            }"""
        )
        is_private = False
        if meta and "This Account is Private" in meta:
            is_private = True
        bio = meta or ""
        return {"username": username, "biography": bio, "is_private": bool(is_private)}
    except Exception:
        return {"username": username, "biography": "", "is_private": False}

async def main():
    usernames_file = "usernames.txt"
    out_dir = "data"
    os.makedirs(out_dir, exist_ok=True)
    out_csv = os.path.join(out_dir, "results.csv")

    if not os.path.exists(usernames_file):
        print(f"ERROR: {usernames_file} not found. Add a file named usernames.txt with one username per line.")
        return 1

    with open(usernames_file, "r", encoding="utf-8") as fh:
        usernames = [l.strip() for l in fh if l.strip()]

    if not usernames:
        print("No usernames found in usernames.txt.")
        return 1

    cookies = cookie_string_to_list(COOKIE_ENV)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context()
        if cookies:
            # Playwright expects cookies with "expires" etc. But basic cookie setting works.
            await context.add_cookies(cookies)
        page = await context.new_page()

        results = []
        for uname in usernames:
            try:
                print(f"Scraping {uname} ...")
                data = await fetch_profile_data(page, uname)
                print(f" -> got: {data}")
                results.append(data)
                # small delay between profiles
                await asyncio.sleep(1.2)
            except Exception as e:
                print(f"Error scraping {uname}: {e}")
                results.append({"username": uname, "biography": "", "is_private": True})

        await browser.close()

    # write CSV
    with open(out_csv, "w", newline="", encoding="utf-8") as csvfile:
        fieldnames = ["username", "biography", "is_private"]
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "username": r.get("username", ""),
                "biography": (r.get("biography") or "").replace("\n", " ").strip(),
                "is_private": "Yes" if r.get("is_private") else "No"
            })
    print(f"Saved {len(results)} rows to {out_csv}")
    return 0

if __name__ == "__main__":
    asyncio.run(main())
