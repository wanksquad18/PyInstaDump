# diagnose_cookies.py
# Load COOKIES_SECRET from data/www.instagram.com.cookies.json and try to GET a profile page
import json, sys, re, requests, os

COOKIES_PATHS = [
    "data/www.instagram.com.cookies.json",
    "www.instagram.com.cookies.json",
    "cookies/www.instagram.com.cookies.json",
]

def load_cookies():
    for p in COOKIES_PATHS:
        if os.path.exists(p):
            try:
                j=json.load(open(p,"r",encoding="utf-8"))
                if isinstance(j,list):
                    return j, p
            except Exception as e:
                print("Failed to parse JSON at", p, ":", e)
    print("No cookie file found in any known path:", COOKIES_PATHS)
    return None, None

def cookies_to_dict(cookie_list):
    d = {}
    for c in cookie_list:
        name = c.get("name")
        val = c.get("value")
        if name and val:
            d[name] = val
    return d

def page_title(html):
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I|re.S)
    return m.group(1).strip() if m else ""

if __name__ == "__main__":
    username = "thepreetjohal"  # quick test profile; change if you want
    cookies, used_path = load_cookies()
    if not cookies:
        print("ERROR: cookies not found or invalid JSON.")
        sys.exit(2)
    print("Found cookie file:", used_path)
    print("Cookie names (sample up to 20):", [c.get("name") for c in cookies[:20]])
    cookie_dict = cookies_to_dict(cookies)
    # check required cookies
    for k in ("sessionid","ds_user_id","csrftoken"):
        print(k, "present:", k in cookie_dict)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    url = f"https://www.instagram.com/{username}/"
    print("GET", url)
    try:
        r = requests.get(url, cookies=cookie_dict, headers=headers, timeout=30)
    except Exception as e:
        print("Request failed:", e)
        sys.exit(3)
    print("HTTP status code:", r.status_code)
    t = page_title(r.text)
    print("Page title:", repr(t)[:200])
    # Save the HTML for debugging artifact
    os.makedirs("data", exist_ok=True)
    with open("data/debug_diagnose.html","w",encoding="utf-8") as fh:
        fh.write(r.text)
    print("Saved data/debug_diagnose.html (size: %d bytes)" % len(r.text))
    # Quick heuristic: login page title contains "Log in" or "Login"
    if "Log in" in t or "Login" in t or "Sign up" in t or r.status_code == 403 or r.status_code == 429:
        print("Heuristic: response looks like a login or blocked page.")
        sys.exit(4)
    print("Looks like a profile page (not a login page).")
    # Optionally print short snippet of HTML
    print("First 400 chars of page body:")
    print(r.text[:400].replace("\n"," "))
    sys.exit(0)
