#!/usr/bin/env python3
"""
scrape_profiles.py
- Reads cookie JSON from data/www.instagram.com.cookies.json (or fallback paths)
- Reads usernames from usernames.txt (one username per line) or from env var SINGLE_USERNAME
- Visits each profile page with Playwright (headless), extracts:
    username, full_name, biography, is_private, is_verified, profile_pic_url
- Writes data/results.csv and data/results.jsonl (one JSON per line)
- On errors saves debug_<username>_timestamp.html and .png into data/
"""

import os, sys, json, time, csv, traceback
from pathlib import Path
from typing import List
from playwright.sync_api import sync_playwright

COOKIE_PATHS = [
    "data/www.instagram.com.cookies.json",
    "www.instagram.com.cookies.json",
    "cookies/www.instagram.com.cookies.json",
]

OUT_CSV = "data/results.csv"
OUT_JSONL = "data/results.jsonl"

def load_cookies():
    for p in COOKIE_PATHS:
        if os.path.exists(p):
            try:
                j = json.load(open(p, encoding="utf-8"))
                if isinstance(j, list):
                    print("Loaded cookies from", p, "entries:", len(j))
                    return j
                else:
                    print("Cookie file", p, "is not a list. type:", type(j))
            except Exception as e:
                print("Failed to parse cookie file", p, ":", e)
    raise RuntimeError("No cookie list file found in: " + ", ".join(COOKIE_PATHS))

def cookies_to_playwright_format(cookie_list):
    # Playwright expects dicts with 'name' and 'value' and optional domain/path
    out = []
    for c in cookie_list:
        if not isinstance(c, dict): continue
        if "name" in c and "value" in c:
            cookie = {"name": c["name"], "value": str(c["value"])}
            # ensure domain/path when present:
            if "domain" in c:
                cookie["domain"] = c["domain"]
            if "path" in c:
                cookie["path"] = c["path"]
            out.append(cookie)
    return out

def read_usernames():
    # 1) SINGLE_USERNAME env
    su = os.environ.get("SINGLE_USERNAME")
    if su:
        return [su.strip()]
    # 2) usernames.txt file
    if os.path.exists("usernames.txt"):
        with open("usernames.txt", "r", encoding="utf-8") as fh:
            lines = [l.strip() for l in fh if l.strip() and not l.strip().startswith("#")]
            if lines:
                return lines
    # 3) usernames in data/usernames.txt
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

def extract_profile_from_page(html, page):
    """
    Try several heuristics to extract profile fields from HTML / rendered DOM.
    We'll prefer JSON-LD script, then meta tags, then page DOM selectors.
    """
    data = {
        "username": None,
        "full_name": None,
        "biography": None,
        "is_private": None,
        "is_verified": None,
        "profile_pic_url": None,
    }
    # 1) try JSON-LD
    try:
        # This script tag often contains a JSON with name, description, image
        ld = page.query_selector('script[type="application/ld+json"]')
        if ld:
            raw = ld.inner_text()
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                data["full_name"] = parsed.get("name") or data["full_name"]
                desc = parsed.get("description")
                if desc:
                    data["biography"] = desc
                img = parsed.get("image")
                if img:
                    data["profile_pic_url"] = img
    except Exception:
        pass

    # 2) try meta property og:description (contains bio and follower counts)
    try:
        meta = page.query_selector('meta[property="og:description"]')
        if meta:
            cont = meta.get_attribute("content") or ""
            # content often: "X followers, Y following, Z posts - biography text"
            # Try to split on " - " to get bio
            if " - " in cont:
                parts = cont.split(" - ", 1)
                bio = parts[1].strip()
                if bio:
                    data["biography"] = bio
            else:
                # fallback: maybe the description equals bio
                if cont and len(cont) < 500:
                    data["biography"] = cont
    except Exception:
        pass

    # 3) extract username from page meta or canonical link
    try:
        canonical = page.query_selector('link[rel="canonical"]')
        if canonical:
            href = canonical.get_attribute("href") or ""
            # https://www.instagram.com/<username>/
            if href:
                parts = href.rstrip("/").split("/")
                if parts:
                    maybe = parts[-1]
                    if maybe:
                        data["username"] = maybe
    except Exception:
        pass

    # 4) explicit DOM selectors for name / bio
    try:
        # full name: typically h1 or header span
        el_full = page.query_selector('header section h1') or page.query_selector('header h1')
        if el_full:
            txt = el_full.inner_text().strip()
            if txt:
                data["full_name"] = txt
    except Exception:
        pass

    try:
        bio_sel = page.query_selector('header section div.-vDIg span') or page.query_selector('div.-vDIg span') or page.query_selector('div.Biography')
        if bio_sel:
            bio = bio_sel.inner_text().strip()
            if bio:
                data["biography"] = bio
    except Exception:
        pass

    # 5) profile picture
    try:
        pic = page.query_selector('img[data-testid="user-avatar"]') or page.query_selector('img[alt*="profile picture"]') or page.query_selector('header img')
        if pic:
            src = pic.get_attribute("src")
            if src:
                data["profile_pic_url"] = src
    except Exception:
        pass

    # 6) private / verified: look for elements
    try:
        is_private = bool(page.query_selector('h2:has-text("This Account is Private")') or page.query_selector('div:has-text("This Account is Private")'))
        data["is_private"] = is_private
    except Exception:
        data["is_private"] = None

    try:
        # verified badge often has svg title or aria-label "Verified"
        ver = bool(page.query_selector('svg[aria-label="Verified"]') or page.query_selector('span:has-text("Verified")'))
        data["is_verified"] = ver
    except Exception:
        data["is_verified"] = None

    return data

def run():
    ensure_data_dir()
    cookie_list = load_cookies()
    playwright_cookies = cookies_to_playwright_format(cookie_list)
    usernames = read_usernames()
    print("Will scrape", len(usernames), "user(s)")

    # Prepare outputs
    csv_fh = open(OUT_CSV, "w", encoding="utf-8", newline="")
    csv_writer = csv.writer(csv_fh)
    csv_writer.writerow(["username","full_name","biography","is_private","is_verified","profile_pic_url"])

    jsonl_fh = open(OUT_JSONL, "w", encoding="utf-8")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        # set cookies domain if absent: some cookies may lack domain; set domain to ".instagram.com"
        for c in playwright_cookies:
            if "domain" not in c:
                c["domain"] = ".instagram.com"
        try:
            context.add_cookies(playwright_cookies)
        except Exception as e:
            print("Warning: context.add_cookies failed:", e)
        page = context.new_page()
        page.set_default_navigation_timeout(60000)

        for username in usernames:
            try:
                target = username.strip().lstrip("@")
                url = f"https://www.instagram.com/{target}/"
                print("Visiting", url)
                page.goto(url, wait_until="domcontentloaded")
                # give the page a little time to render API-driven content
                page.wait_for_timeout(1400)
                # check if we landed on login page
                title = page.title()
                if "Log in" in title or "Login" in title or "Sign up" in title:
                    print("Looks like a login page for", target, "- cookies invalid or blocked. Saving debug and skipping.")
                    save_debug(target, page)
                    continue
                # extract
                profile = extract_profile_from_page(page.content(), page)
                # ensure username present
                if not profile["username"]:
                    profile["username"] = target
                print("Extracted:", profile)
                csv_writer.writerow([
                    profile.get("username") or "",
                    profile.get("full_name") or "",
                    profile.get("biography") or "",
                    str(profile.get("is_private")),
                    str(profile.get("is_verified")),
                    profile.get("profile_pic_url") or ""
                ])
                jsonl_fh.write(json.dumps(profile, ensure_ascii=False) + "\n")
                jsonl_fh.flush()
            except Exception as e:
                print("Error scraping", username, ":", e)
                traceback.print_exc()
                try:
                    save_debug(username, page)
                except Exception as ee:
                    print("Failed to save debug for", username, ee)
                continue

        try:
            context.close()
            browser.close()
        except Exception:
            pass

    csv_fh.close()
    jsonl_fh.close()
    print("Done. Wrote:", OUT_CSV, OUT_JSONL)

if __name__ == "__main__":
    run()
