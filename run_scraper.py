#!/usr/bin/env python3
"""
run_scraper.py

- Reads COOKIES_SECRET from env (must be a JSON array string of cookies).
- Writes cookie JSON to:
    - www.instagram.com.cookies.json
    - data/www.instagram.com.cookies.json
    - cookies/www.instagram.com.cookies.json
  (some scrapers expect different locations)
- Validates JSON and prints cookie names for debug.
- Performs a lightweight diagnostic GET of one profile to ensure cookies work
  and writes data/debug_diagnose.html so you can inspect the HTML.
- Runs scrape_profiles.py (expects it to exist in repo root).
- Exits with the same exit code as the scraper.
"""

import os
import sys
import json
import time
import subprocess
from pathlib import Path

DEBUG_TEST_USERNAME = os.environ.get("DIAGNOSE_USERNAME") or "thepreetjohal"
COOKIE_OUT_PATHS = [
    "www.instagram.com.cookies.json",
    "data/www.instagram.com.cookies.json",
    "cookies/www.instagram.com.cookies.json",
]

def load_secret_and_write(secret_env_name="COOKIES_SECRET"):
    s = os.environ.get(secret_env_name)
    if not s:
        print(f"ERROR: Environment variable {secret_env_name} is empty. Set repository secret with that name.")
        return False

    # some flows put a JSON string with escaped newlines; try best-effort cleaning
    candidate = s.strip()
    # If someone pasted multi-line JSON into the secret, it will come through with newlines already.
    # If the secret looks like it's been JSON-encoded inside the secret (e.g. "\"[ {..} ]\""),
    # try to decode twice.
    parsed = None
    try:
        parsed = json.loads(candidate)
    except Exception:
        # try replacing escaped newline sequences and reparse
        try:
            fixed = candidate.replace('\\n', '\n').replace('\\t', '\t')
            parsed = json.loads(fixed)
        except Exception as e:
            print("Failed to parse COOKIES_SECRET as JSON; attempting to heuristically extract JSON array...")
            # attempt to find first '[' and last ']' and parse slice
            lb = candidate.find('[')
            rb = candidate.rfind(']')
            if lb != -1 and rb != -1 and rb > lb:
                try:
                    parsed = json.loads(candidate[lb:rb+1])
                except Exception as e2:
                    print("Heuristic parse failed:", e2)
                    parsed = None
            else:
                parsed = None

    if not parsed:
        print("ERROR: could not parse cookie JSON from secret. Please ensure the secret contains the cookie JSON array.")
        return False

    if not isinstance(parsed, list):
        print("ERROR: parsed cookie content is not a JSON array/list.")
        return False

    # Write to each path
    for p in COOKIE_OUT_PATHS:
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(parsed, fh, ensure_ascii=False, indent=2)
        print("Wrote cookie file:", p, "entries:", len(parsed))

    # quick print of cookie names
    names = [c.get("name") for c in parsed if isinstance(c, dict) and "name" in c]
    print("Cookie names (sample up to 40):", names[:40])
    return True

def diagnostic_fetch():
    import requests
    paths = COOKIE_OUT_PATHS
    cookie_list = None
    for p in paths:
        if Path(p).exists():
            try:
                cookie_list = json.load(open(p, encoding="utf-8"))
                print("Using cookie file:", p)
                break
            except Exception as e:
                print("Failed to parse", p, ":", e)
    if not cookie_list:
        print("No cookie file available for diagnostic.")
        return False

    # convert into requests cookie dict
    cookie_dict = {}
    for c in cookie_list:
        if not isinstance(c, dict):
            continue
        name = c.get("name"); val = c.get("value")
        if name and val is not None:
            cookie_dict[name] = val

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117 Safari/537.36"
    }

    url = f"https://www.instagram.com/{DEBUG_TEST_USERNAME}/"
    print("Diagnostic GET", url)
    try:
        r = requests.get(url, cookies=cookie_dict, headers=headers, timeout=30)
    except Exception as e:
        print("Diagnostic request failed:", e)
        return False

    print("HTTP status:", r.status_code)
    html = r.text
    os.makedirs("data", exist_ok=True)
    with open("data/debug_diagnose.html", "w", encoding="utf-8") as fh:
        fh.write(html)
    print("Saved data/debug_diagnose.html size:", len(html))

    # Heuristics
    if r.status_code in (403, 429):
        print("Diagnostic: blocked or rate limited (status code)", r.status_code)
        return False

    title_ok = False
    try:
        import re
        m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = m.group(1).strip() if m else ""
        print("Page title:", title[:200])
        if "Log in" in title or "Login" in title or "Sign up" in title:
            print("Diagnostic heuristic: looks like a login/landing page — cookies invalid or blocked.")
            return False
        title_ok = True
    except Exception:
        pass

    print("Diagnostic looks OK (profile page returned).")
    return title_ok

def run_scraper_wrapper():
    ok = load_secret_and_write = None
    ok = load_secret_and_write = load_secret_and_write_wrapper()

def load_secret_and_write_wrapper():
    return load_secret_and_write()

def main():
    # Step 1: create data folders
    os.makedirs("data", exist_ok=True)
    os.makedirs("cookies", exist_ok=True)

    print("Step: write/validate COOKIES_SECRET -> cookie files")
    if not load_secret_and_write_wrapper():
        print("Cookie write/validation failed. Aborting.")
        sys.exit(20)

    print("Step: run diagnostic fetch (profile page) -> data/debug_diagnose.html")
    diag_ok = False
    try:
        diag_ok = diagnostic_fetch()
    except Exception as e:
        print("Diagnostic fetch exception:", e)
        diag_ok = False

    if not diag_ok:
        print("Warning: diagnostic fetch did not pass. The scraper may still run but likely will be blocked or land on login page.")

    # Step: run the scraper script
    scraper_script = "scrape_profiles.py"
    if not Path(scraper_script).exists():
        print(f"ERROR: {scraper_script} not found in repo root. Aborting.")
        sys.exit(21)

    print("Running scraper:", scraper_script)
    # Run in the same environment so the scraper can pick up files we've written
    rc = subprocess.call([sys.executable, scraper_script])
    print("Scraper exited with code:", rc)
    sys.exit(rc if isinstance(rc, int) else 1)

if __name__ == "__main__":
    main()
