#!/usr/bin/env python3
"""
scrape_profiles.py

- Reads cookies from env COOKIES_SECRET (raw JSON array like exported from browser)
- Reads usernames list from usernames.txt (one per line) OR env USERNAMES (comma separated)
- Visits each Instagram profile and extracts:
    username, full_name, biography, biography_with_entities (if possible),
    profile_pic_url, is_private, is_verified, followers_count, following_count, posts_count
- Writes results to data/results.csv
- Creates debug artifacts (data/debug_<username>_<ts>.png/.html) if a profile fails to load or parse
"""

import os
import json
import asyncio
import time
import csv
from typing import List, Dict, Any
from pathlib import Path
from playwright.async_api import async_playwright, Page, BrowserContext

OUT_DIR = Path("data")
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_CSV = OUT_DIR / "results.csv"


def load_cookies_from_env() -> List[Dict[str, Any]]:
    s = os.getenv("COOKIES_SECRET", "")
    if not s:
        # try file fallback (useful for local testing)
        fallback = Path("www.instagram.com.cookies.json")
        if fallback.exists():
            s = fallback.read_text(encoding="utf-8")
    if not s:
        raise RuntimeError("No cookies provided. Set COOKIES_SECRET env or place www.instagram.com.cookies.json")
    # try to parse JSON — some users paste escaped strings, so try a double-unescape
    try:
        cookies = json.loads(s)
    except Exception:
        # try un-escaping typical escaped newlines and quotes
        try:
            s2 = s.encode("utf-8").decode("unicode_escape")
            cookies = json.loads(s2)
        except Exception as e:
            raise RuntimeError(f"Failed to parse cookies secret: {e}")
    if not isinstance(cookies, list):
        raise RuntimeError("Cookies must be a JSON array of cookie objects.")
    # Playwright cookie shape: name, value, domain, path, expires (optional), httpOnly, secure, sameSite
    return cookies


def load_usernames() -> List[str]:
    # priority: usernames.txt -> USERNAMES env var (comma-separated) -> first CLI arg list
    if Path("usernames.txt").exists():
        lines = [l.strip() for l in Path("usernames.txt").read_text(encoding="utf-8").splitlines()]
        return [l for l in lines if l]
    env = os.getenv("USERNAMES", "").strip()
    if env:
        return [u.strip() for u in env.split(",") if u.strip()]
    # last fallback: CLI args
    import sys
    if len(sys.argv) > 1:
        return sys.argv[1:]
    raise RuntimeError("No usernames found. Provide usernames.txt, USERNAMES env, or command-line args.")


async def save_debug_artifacts(page: Page, target: str):
    ts = int(time.time())
    safe = target.replace("/", "_")
    png = OUT_DIR / f"debug_{safe}_{ts}.png"
    html_path = OUT_DIR / f"debug_{safe}_{ts}.html"
    try:
        await page.screenshot(path=str(png), full_page=True)
        html = await page.content()
        html_path.write_text(html, encoding="utf-8")
        print(f"Saved debug artifacts: {png} and {html_path}")
    except Exception as e:
        print("Failed saving debug artifacts:", e)


async def ensure_logged_in(page: Page) -> bool:
    # Quick check: if we land on login page, login is required
    url = page.url
    if "/accounts/login/" in url or "challenge" in url or "consent" in url:
        print("Session appears not logged in or blocked: page url =", url)
        return False
    # Another check: presence of profile root element / header
    try:
        await page.wait_for_selector("header", timeout=3000)
    except Exception:
        # Not fatal here; still could be OK
        pass
    return True


def parse_count(txt: str):
    if txt is None:
        return None
    t = txt.replace(",", "").replace(".", "").strip()
    # if contains 'k' or 'm' handle, but most of the time script will read numeric attribute
    try:
        if "k" in txt.lower():
            return int(float(txt.lower().replace("k", "")) * 1000)
        if "m" in txt.lower():
            return int(float(txt.lower().replace("m", "")) * 1000000)
        return int(''.join(ch for ch in txt if ch.isdigit()))
    except Exception:
        return None


async def scrape_profile(page: Page, username: str) -> Dict[str, Any]:
    result = {
        "username": username,
        "full_name": None,
        "biography": None,
        "biography_with_entities": None,
        "profile_pic_url": None,
        "is_private": None,
        "is_verified": None,
        "followers_count": None,
        "following_count": None,
        "posts_count": None,
        "error": None,
    }
    target = f"https://www.instagram.com/{username}/"
    try:
        print("Opening", target)
        await page.goto(target, timeout=45000)
        # quick logged-in check
        cur = page.url
        if "/accounts/login/" in cur or "challenge" in cur:
            result["error"] = f"Login required or blocked, page url: {cur}"
            await save_debug_artifacts(page, username)
            return result

        # wait for the main info block
        try:
            await page.wait_for_selector("header", timeout=10000)
        except Exception:
            # continue; some pages are fast enough but selectors may vary
            pass

        # Many profile details are present in sharedData JSON or meta tags; try layered approach

        # 1) Try to read window._sharedData or script[type="application/ld+json"]
        try:
            ld = await page.query_selector('script[type="application/ld+json"]')
            if ld:
                text = await ld.inner_text()
                obj = json.loads(text)
                # typical ld structure contains name/description/image
                result["full_name"] = obj.get("name") or result["full_name"]
                if "description" in obj:
                    result["biography"] = obj.get("description")
                if "image" in obj:
                    result["profile_pic_url"] = obj.get("image")
        except Exception as e:
            # not fatal
            pass

        # 2) Try meta tags
        try:
            meta_desc = await page.get_attribute('meta[property="og:description"]', "content")
            if meta_desc:
                # meta contains: "X (@x) • Instagram photos and videos" sometimes
                # But profile meta often includes counts and bio; keep for debugging
                if not result["biography"]:
                    result["biography"] = meta_desc
        except Exception:
            pass

        # 3) Try JSON in sharedData GraphQL initial script (robust)
        try:
            js_text = await page.locator("script").first.inner_text()
            # not always the shared data; we will search smaller script nodes
            # Instead try to evaluate window.__initialState or window._sharedData via page.evaluate
            shared = await page.evaluate("() => { return window.__initialData || window._sharedData || null }")
            if shared:
                # drill down into shared data to find profile info
                # different Instagram builds: find profile object by username
                def find_profile(obj):
                    if not obj or isinstance(obj, (str, int, float)):
                        return None
                    if isinstance(obj, dict):
                        # search for user-like dict: has biography or edge_owner_to_timeline_media key
                        if "biography" in obj and "username" in obj:
                            return obj
                        for v in obj.values():
                            r = find_profile(v)
                            if r:
                                return r
                    elif isinstance(obj, list):
                        for item in obj:
                            r = find_profile(item)
                            if r:
                                return r
                    return None

                # convert shared to python object if needed (it is already)
                profile_obj = None
                try:
                    profile_obj = find_profile(shared)
                except Exception:
                    profile_obj = None
                if profile_obj:
                    result["full_name"] = profile_obj.get("full_name") or result["full_name"]
                    result["biography"] = profile_obj.get("biography") or result["biography"]
                    result["profile_pic_url"] = profile_obj.get("profile_pic_url") or result["profile_pic_url"]
                    result["is_private"] = profile_obj.get("is_private")
                    result["is_verified"] = profile_obj.get("is_verified")
                    # counts may be in nested edges
                    if profile_obj.get("edge_followed_by") and profile_obj["edge_followed_by"].get("count") is not None:
                        result["followers_count"] = profile_obj["edge_followed_by"]["count"]
                    if profile_obj.get("edge_follow") and profile_obj["edge_follow"].get("count") is not None:
                        result["following_count"] = profile_obj["edge_follow"]["count"]
                    if profile_obj.get("edge_owner_to_timeline_media") and profile_obj["edge_owner_to_timeline_media"].get("count") is not None:
                        result["posts_count"] = profile_obj["edge_owner_to_timeline_media"]["count"]
        except Exception:
            pass

        # 4) If not found, try DOM queries - current Instagram markup (subject to change)
        try:
            # full name selector
            try:
                full_name = await page.text_content("header section div:nth-of-type(1) h1")
                if full_name:
                    result["full_name"] = full_name.strip()
            except Exception:
                pass

            # biography selector
            try:
                bio_el = await page.query_selector('header section div:nth-of-type(1) div.-vDIg')
                if bio_el:
                    bio_text = await bio_el.text_content()
                    if bio_text:
                        result["biography"] = bio_text.strip()
            except Exception:
                pass

            # profile pic
            try:
                pic = await page.get_attribute('header img', 'src')
                if pic:
                    result["profile_pic_url"] = pic
            except Exception:
                pass

            # verification/private flags - try mirrored attributes
            try:
                # private accounts show "This Account is Private" text somewhere
                private_text = await page.query_selector('//h2[text()="This Account is Private"]')
                if private_text:
                    result["is_private"] = True
            except Exception:
                pass

            # counts (followers / following / posts) - try to read from header numbers
            try:
                counts = await page.locator('header li').all_text_contents()
                if counts:
                    # sometimes counts are [posts, followers, following] in that order
                    if len(counts) >= 3:
                        # counts items might contain newline and label; extract numeric prefix
                        def numeric_from_text(t):
                            s = t.split()[0].replace(",", "").replace(".", "")
                            try:
                                if "k" in t.lower():
                                    return int(float(t.lower().replace("k", "")) * 1000)
                                if "m" in t.lower():
                                    return int(float(t.lower().replace("m", "")) * 1000000)
                                return int(''.join(ch for ch in s if ch.isdigit()) or 0)
                            except Exception:
                                return None

                        posts_c = numeric_from_text(counts[0])
                        followers_c = numeric_from_text(counts[1])
                        following_c = numeric_from_text(counts[2])
                        result["posts_count"] = posts_c or result["posts_count"]
                        result["followers_count"] = followers_c or result["followers_count"]
                        result["following_count"] = following_c or result["following_count"]
            except Exception:
                pass

        except Exception as e:
            print("DOM extraction failed:", e)

        # Final small normalization
        if isinstance(result["is_private"], bool):
            pass
        elif result["biography"] and "This Account is Private" in result["biography"]:
            result["is_private"] = True

        return result

    except Exception as e:
        result["error"] = f"Exception: {e}"
        try:
            await save_debug_artifacts(page, username)
        except Exception:
            pass
        return result


async def main():
    cookies = load_cookies_from_env()
    usernames = load_usernames()
    print("Loaded", len(cookies), "cookies. Usernames to scrape:", len(usernames))

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context: BrowserContext = await browser.new_context()
        # ensure cookies' domain values are fine for Playwright (domain must not include protocol)
        # Playwright accepts the same format as exported, so we add cookies directly
        try:
            await context.add_cookies(cookies)
        except Exception as e:
            print("Warning: adding cookies failed:", e)
            # proceed - script may still work if cookies set via other means
        page = await context.new_page()

        # write header CSV
        with OUT_CSV.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=[
                "username", "full_name", "biography", "biography_with_entities",
                "profile_pic_url", "is_private", "is_verified",
                "followers_count", "following_count", "posts_count", "error"
            ])
            writer.writeheader()

            for uname in usernames:
                try:
                    res = await scrape_profile(page, uname)
                    writer.writerow({
                        k: ("" if res.get(k) is None else res.get(k)) for k in writer.fieldnames
                    })
                    print("Scraped", uname, "->", "error" in res and res["error"] or "OK")
                except Exception as e:
                    print("Unhandled error for", uname, e)
                    await save_debug_artifacts(page, uname)
                    writer.writerow({
                        "username": uname,
                        "error": f"Unhandled error: {e}"
                    })

        await browser.close()
    print("Done. Results saved to", OUT_CSV)


if __name__ == "__main__":
    asyncio.run(main())
