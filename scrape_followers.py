# --- robust followers modal opener (copy/paste) ---
import time, json, os
from playwright.async_api import TimeoutError as PlaywrightTimeout

async def open_followers_modal(page, browser, target, debug_dir="data", header_timeout_ms=45000):
    """Tries multiple selectors to open followers modal. Saves debug artifacts on failure."""
    safe_name = target.replace("/", "_")
    ts = int(time.time())
    try:
        # Wait for profile header (username). This ensures page loaded to profile.
        await page.wait_for_selector('header, main, article, [role="main"]', timeout=header_timeout_ms)
    except PlaywrightTimeout:
        # Save debug artifacts and bail
        try:
            os.makedirs(debug_dir, exist_ok=True)
            await page.screenshot(path=f"{debug_dir}/debug_{safe_name}_{ts}.png", full_page=True)
            html = await page.content()
            with open(f"{debug_dir}/debug_{safe_name}_{ts}.html", "w", encoding="utf-8") as fh:
                fh.write(html)
            print(f"Saved debug screenshot and html: {debug_dir}/debug_{safe_name}_{ts}.png / .html")
        except Exception as e:
            print("Failed to save debug artifacts:", e)
        print("Timeout waiting for profile header.")
        return False

    # Candidate selectors / link patterns that indicate followers link
    selectors = [
        'a[href$="/followers/"]',                    # typical link
        'a[href*="/followers/?"]',
        'a:has-text("followers")',
        'a:has-text("Followers")',
        'a:has-text("followers") span',              # some layouts wrap count
        'a[href*="/followers"]',                     # fallback
        'button:has-text("followers")',
        'div[role="button"] a[href*="/followers"]'
    ]

    for sel in selectors:
        try:
            el = await page.query_selector(sel)
            if el:
                # scroll into view + click
                await el.scroll_into_view_if_needed()
                await el.click()
                # wait for followers modal/list to appear
                await page.wait_for_selector('div[role="dialog"], div[role="presentation"], ul[role="list"]', timeout=10000)
                return True
        except Exception as e:
            # try next selector
            continue

    # If we reach here the followers button wasn't found -> capture debug artifacts
    try:
        os.makedirs(debug_dir, exist_ok=True)
        await page.screenshot(path=f"{debug_dir}/debug_{safe_name}_{ts}.png", full_page=True)
        html = await page.content()
        with open(f"{debug_dir}/debug_{safe_name}_{ts}.html", "w", encoding="utf-8") as fh:
            fh.write(html)
        print(f"Saved debug screenshot and html: {debug_dir}/debug_{safe_name}_{ts}.png / .html")
    except Exception as inner_e:
        print("Failed to save debug artifacts:", inner_e)

    print("Failed to open followers modal: Followers link/button not found on profile page.")
    return False
# --- end function ---
