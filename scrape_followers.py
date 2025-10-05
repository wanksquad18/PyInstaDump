#!/usr/bin/env python3
"""
scrape_followers.py

- Reads cookies (same locations)
- Expects SINGLE_USERNAME env var or reads first username from usernames.txt
- Opens profile, clicks Followers, scrolls modal to collect followers usernames
- Saves CSV: data/followers_<username>.csv
- Saves debug artifacts on errors
"""

import os, sys, json, time, csv, traceback
from pathlib import Path
from playwright.sync_api import sync_playwright

COOKIE_PATHS = [
    "data/www.instagram.com.cookies.json",
    "www.instagram.com.cookies.json",
    "cookies/www.instagram.com.cookies.json",
]

def load_cookies():
    for p in COOKIE_PATHS:
        if os.path.exists(p):
            try:
                j = json.load(open(p, encoding="utf-8"))
                if isinstance(j, list):
                    print("Loaded cookies from", p, "entries:", len(j))
                    return j
            except Exception as e:
                print("Failed to parse", p, ":", e)
    raise RuntimeError("No cookie list found in: " + ", ".join(COOKIE_PATHS))

def cookies_to_playwright_format(cookie_list):
    out = []
    for c in cookie_list:
        if isinstance(c, dict) and "name" in c and "value" in c:
            cookie = {"name": c["name"], "value": str(c["value"]), "domain": c.get("domain", ".instagram.com")}
            if "path" in c:
                cookie["path"] = c["path"]
            out.append(cookie)
    return out

def get_target_username():
    su = os.environ.get("SINGLE_USERNAME")
    if su:
        return su.strip().lstrip("@")
    if os.path.exists("usernames.txt"):
        with open("usernames.txt","r",encoding="utf-8") as fh:
            for l in fh:
                s=l.strip()
                if s:
                    return s.lstrip("@")
    raise RuntimeError("No username provided. Set SINGLE_USERNAME or add usernames.txt")

def ensure_data_dir():
    os.makedirs("data", exist_ok=True)

def save_debug(page, name):
    ts=int(time.time())
    safe=name.replace("/","_")
    try:
        html = page.content()
        with open(f"data/debug_{safe}_{ts}.html","w",encoding="utf-8") as fh:
            fh.write(html)
    except Exception as e:
        print("Failed save html debug:", e)
    try:
        page.screenshot(path=f"data/debug_{safe}_{ts}.png", full_page=True)
    except Exception as e:
        print("Failed save png debug:", e)
    print("Saved debug artifacts for", name)

def scrape_followers_of(target, max_followers=1000):
    ensure_data_dir()
    cookies = load_cookies()
    playwright_cookies = cookies_to_playwright_format(cookies)
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        context = browser.new_context()
        try:
            context.add_cookies(playwright_cookies)
        except Exception as e:
            print("Warning: add_cookies failed:", e)
        page = context.new_page()
        page.set_default_navigation_timeout(60000)
        try:
            url = f"https://www.instagram.com/{target}/"
            print("Opening profile:", url)
            page.goto(url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            # Detect login page
            if "Log in" in page.title() or "Login" in page.title():
                print("Login page detected (cookies invalid or blocked). Saving debug and exiting.")
                save_debug(page, target)
                return 1
            # Find followers button/link in header
            # The count is often in a link: a[href="/<user>/followers/"]
            try:
                follow_link = page.locator(f'a[href="/{target}/followers/"]').first
                if follow_link.count() == 0:
                    # fallback: find link with text 'followers'
                    follow_link = page.locator('a').filter(has_text='followers').first
                if follow_link.count() == 0:
                    print("Could not find followers link/button. Saving debug.")
                    save_debug(page, target)
                    return 2
                print("Clicking followers link...")
                follow_link.click()
            except Exception as e:
                print("Exception clicking followers link:", e)
                save_debug(page, target)
                return 3

            # Wait for modal with role=dialog and inner ul list of followers
            modal_sel = 'div[role="dialog"] ul'
            try:
                page.wait_for_selector(modal_sel, timeout=10000)
            except Exception as e:
                print("Followers modal did not appear:", e)
                save_debug(page, target)
                return 4

            list_el = page.locator(modal_sel).first
            # Scroll the modal to load followers. Use evaluate to scroll inside the element.
            collected = set()
            prev_count = 0
            attempts = 0
            print("Start scrolling modal to collect followers (limit:", max_followers, ")")
            while len(collected) < max_followers and attempts < 400:
                # collect visible usernames in modal items: often li a[href="/<user>/"] with div > div > div > span
                items = list_el.locator('a').all_text_contents()
                # items as text may include full names etc; instead find anchors href -> extract username from href attribute
                anchors = list_el.locator('a')
                n = anchors.count()
                for i in range(n):
                    try:
                        href = anchors.nth(i).get_attribute("href") or ""
                        # href may be like "/username/"
                        if href.startswith("/"):
                            parts = href.strip("/").split("/")
                            if parts:
                                u = parts[0]
                                if u and u not in collected:
                                    collected.add(u)
                    except Exception:
                        continue

                # scroll
                page.evaluate("""(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return 0;
                    el.scrollTop = el.scrollTop + el.clientHeight;
                    return el.scrollTop;
                }""", modal_sel)
                page.wait_for_timeout(400)
                attempts += 1
                if len(collected) == prev_count:
                    # if no growth after some attempts, increase wait
                    if attempts % 10 == 0:
                        page.wait_for_timeout(800)
                prev_count = len(collected)
                if len(collected) >= max_followers:
                    break

            result_list = list(collected)[:max_followers]
            print("Collected", len(result_list), "followers (capped to max).")
            # write CSV
            out_csv = f"data/followers_{target}.csv"
            with open(out_csv, "w", encoding="utf-8", newline="") as fh:
                import csv
                w = csv.writer(fh)
                w.writerow(["username"])
                for u in result_list:
                    w.writerow([u])
            print("Saved", out_csv)
            return 0
        except Exception as e:
            print("Fatal exception:", e)
            traceback.print_exc()
            try:
                save_debug(page, target)
            except Exception:
                pass
            return 99
        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass

if __name__ == "__main__":
    target = get_target = None
    try:
        target = get_target_username()
    except Exception as e:
        print("Error getting target username:", e)
        sys.exit(2)
    try:
        maxf = int(os.environ.get("MAX_FOLLOWERS", "1000"))
    except:
        maxf = 1000
    rc = scrape_followers_of(target, maxf)
    sys.exit(rc if isinstance(rc, int) else 0)
