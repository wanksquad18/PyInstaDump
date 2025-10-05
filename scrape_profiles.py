#!/usr/bin/env python3
# scrape_profiles.py
# Usage: expects cookie JSON at data/www.instagram.com.cookies.json (or env SINGLE_USERNAME / usernames.txt)
# Writes: data/results.csv and data/results.jsonl
# Saves debug artifacts on errors: data/debug_<username>_<ts>.html/.png

import os, sys, json, time, csv, traceback
from pathlib import Path
from typing import List
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

COOKIE_PATHS = [
    "data/www.instagram.com.cookies.json",
    "www.instagram.com.cookies.json",
    "cookies/www.instagram.com.cookies.json",
]

OUT_CSV = "data/results.csv"
OUT_JSONL = "data/results.jsonl"

def load_cookies_from_files():
    for p in COOKIE_PATHS:
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    j = json.load(fh)
                if isinstance(j, list):
                    print("Loaded cookies from", p, "entries:", len(j))
                    return j
                else:
                    print("Cookie file", p, "is not a list (type {}).".format(type(j)))
            except Exception as e:
                print("Failed to parse cookie file", p, ":", e)
    return None

def load_cookies_from_env():
    # Some workflows set COOKIES_SECRET env to raw JSON string
    raw = os.environ.get("COOKIES_SECRET") or os.environ.get("COOKIES")
    if not raw:
        return None
    try:
        j = json.loads(raw)
        if isinstance(j, list):
            print("Loaded cookies from COOKIES_SECRET env, entries:", len(j))
            return j
    except Exception as e:
        print("Failed to parse COOKIES_SECRET env as JSON:", e)
    return None

def ensure_cookie_domains(cookie_list):
    # Some cookie dumps are missing domain for each cookie; Playwright needs domain key
    out = []
    for c in cookie_list:
        if not isinstance(c, dict):
            continue
        cc = dict(c)  # copy
        # If domain missing, set to .instagram.com
        if "domain" not in cc or not cc["domain"]:
            cc["domain"] = ".instagram.com"
        # Playwright wants 'path' too (default '/')
        if "path" not in cc or not cc["path"]:
            cc["path"] = "/"
        # convert expiry to int if present
        if "expiry" in cc and not isinstance(cc["expiry"], int):
            try:
                cc["expiry"] = int(cc["expiry"])
            except Exception:
                del cc["expiry"]
        out.append(cc)
    return out

def cookies_to_playwright_format(cookie_list):
    # Playwright expects dicts with 'name' and 'value' (domain/path helpful)
    out = []
    for c in cookie_list:
        if not isinstance(c, dict):
            continue
        if "name" in c and "value" in c:
            cookie = {"name": c["name"], "value": str(c["value"])}
            if "domain" in c:
                cookie["domain"] = c["domain"]
            if "path" in c:
                cookie["path"] = c["path"]
            if "expiry" in c:
                cookie["expires"] = c["expiry"]
            out.append(cookie)
    return out

def read_usernames():
    su = os.environ.get("SINGLE_USERNAME")
    if su:
        return [su.strip()]
    if os.path.exists("usernames.txt"):
        with open("usernames.txt", "r", encoding="utf-8") as fh:
            lines = [l.strip() for l in fh if l.strip() and not l.strip().startswith("#")]
            if lines:
                return lines
    if os.path.exists("data/usernames.txt"):
        with open("data/usernames.txt", "r", encoding="utf-8") as fh:
            return [l.strip() for l in fh if l.strip()]
    raise RuntimeError("No usernames found. Provide usernames.txt or set SINGLE_USERNAME env var.")

def ensure_data_dir():
    os.makedirs("data", exist_ok=True)

def save_debug(name, page):
    ts = int(time.time())
    safe = name.replace("/", "_")
    try:
        html = page.content()
        with open(f"data/debug_{safe}_{ts}.html", "w", encoding="utf-8") as fh:
            fh.write(html)
    except Exception as e:
        print("Failed to save debug html:", e)
    try:
        page.screenshot(path=f"data/debug_{safe}_{ts}.png", full_page=True)
    except Exception as e:
        print("Failed to save debug png:", e)
    print("Saved debug artifacts for", name, "-> data/debug_%s_%d.{html,png}" % (safe, ts))

def extract_profile_from_dom(page):
    """
    Primary extraction: wait for header DOM and read biography from header.
    Common Instagram DOM: header -> section -> div -> span (the bio)
    """
    data = {
        "username": None,
        "full_name": None,
        "biography": None,
        "is_private": None,
        "is_verified": None,
        "profile_pic_url": None,
    }
    try:
        # Wait for header section where profile info appears
        page.wait_for_selector("header", timeout=8000)
        # username
        username_el = page.query_selector("header h2") or page.query_selector("header h1") or page.query_selector("header ._aa_c")
        if username_el:
            data["username"] = (username_el.inner_text() or "").strip()
        # full name
        full_el = page.query_selector("header section h1") or page.query_selector("header section div.-vDIg span")
        if full_el:
            data["full_name"] = (full_el.inner_text() or "").strip()
        # biography: usually inside header section: header section div.-vDIg > span (first or second)
        bio_el = page.query_selector("header section div.-vDIg span") or page.query_selector("header section div.-vDIg") or page.query_selector('div.-vDIg > span')
        if bio_el:
            data["biography"] = (bio_el.inner_text() or "").strip()
        # private / verified
        # private flag: if there's text like 'This Account is Private'
        if page.query_selector('h2:has-text("This Account is Private")') or "This Account is Private" in page.content():
            data["is_private"] = True
        else:
            data["is_private"] = False
        # verified: presence of svg with aria-label 'Verified' or a span with verified
        if page.query_selector('svg[aria-label="Verified"]') or page.query_selector('header span[title*="Verified"]'):
            data["is_verified"] = True
        else:
            data["is_verified"] = False
        # profile pic - og:image / meta property or header img
        og_img = page.query_selector('meta[property="og:image"]')
        if og_img:
            data["profile_pic_url"] = og_img.get_attribute("content")
        else:
            img = page.query_selector("header img")
            if img:
                data["profile_pic_url"] = img.get_attribute("src")
    except PWTimeout:
        print("Timeout waiting for header DOM.")
    except Exception as e:
        print("DOM extraction error:", e)
    return data

def fallback_extract_from_page_source(page):
    # fallback: try application/ld+json or meta description
    data = {}
    try:
        ld = page.query_selector('script[type="application/ld+json"]')
        if ld:
            raw = ld.inner_text()
            parsed = json.loads(raw)
            data["full_name"] = parsed.get("name")
            data["biography"] = parsed.get("description")
            data["profile_pic_url"] = parsed.get("image")
    except Exception:
        pass
    try:
        meta_desc = page.query_selector('meta[name="description"]') or page.query_selector('meta[property="og:description"]')
        if meta_desc:
            data["biography"] = data.get("biography") or meta_desc.get_attribute("content")
    except Exception:
        pass
    return data

def write_results_row(row):
    # write CSV header first if not exists
    header = ["username","full_name","biography","is_private","is_verified","profile_pic_url"]
    exists = os.path.exists(OUT_CSV)
    with open(OUT_CSV, "a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        if not exists:
            writer.writerow(header)
        writer.writerow([row.get(h) for h in header])
    with open(OUT_JSONL, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")

def main():
    ensure_data_dir()
    cookie_list = load_cookies_from_files() or load_cookies_from_env()
    if not cookie_list:
        raise RuntimeError("No cookies provided. Create data/www.instagram.com.cookies.json or set COOKIES_SECRET env.")

    cookie_list = ensure_cookie_domains(cookie_list)
    playwright_cookies = cookies_to_playwright_format(cookie_list)

    usernames = read_usernames()
    print("Will scrape {} user(s)".format(len(usernames)))

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-setuid-sandbox"])
        context = browser.new_context(viewport={"width":1280,"height":800})
        # add cookies to context
        try:
            context.add_cookies(playwright_cookies)
            print("Added {} cookies to browser context".format(len(playwright_cookies)))
        except Exception as e:
            print("Failed to add cookies to context:", e)

        page = context.new_page()
        # set a common UA
        page.set_extra_http_headers({"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117 Safari/537.36"})

        for target in usernames:
            url = f"https://www.instagram.com/{target}/"
            print("Visiting", url)
            try:
                page.goto(url, timeout=30000)
                # wait a bit for JS to populate
                try:
                    page.wait_for_timeout(1200)
                except Exception:
                    pass

                # Primary attempt: read from DOM header
                profile = extract_profile_from_dom(page)
                # If biography is short or None, try fallback
                bio = (profile.get("biography") or "").strip()
                if not bio or len(bio) < 10:
                    fallback = fallback_extract_from_page_source(page)
                    for k,v in fallback.items():
                        if v and not profile.get(k):
                            profile[k] = v

                # if username missing, set from target
                if not profile.get("username"):
                    profile["username"] = target

                print("Extracted:", {k: profile.get(k) for k in ["username","full_name","biography","is_private","is_verified","profile_pic_url"]})
                write_results_row(profile)

            except Exception as e:
                print("Error scraping", target, ":", e)
                try:
                    save_debug(target, page)
                except Exception as dbg_e:
                    print("Failed to save debug artifacts:", dbg_e)
            finally:
                # small polite delay (env-controlled)
                delay = float(os.environ.get("PROFILE_DELAY", "0.8"))
                time.sleep(delay)

        try:
            context.close()
        except Exception:
            pass
        try:
            browser.close()
        except Exception:
            pass

if __name__ == "__main__":
    try:
        main()
        print("Done. Wrote:", OUT_CSV, OUT_JSONL)
    except Exception as e:
        print("Fatal error:", e)
        traceback.print_exc()
        sys.exit(1)
