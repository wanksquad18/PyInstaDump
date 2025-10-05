# diagnose_cookies.py
# Simple cookie file validator used by the workflow.
import json, sys, os

paths = [
    "data/www.instagram.com.cookies.json",
    "www.instagram.com.cookies.json",
    "cookies/www.instagram.com.cookies.json",
]

for p in paths:
    if not os.path.exists(p):
        continue
    try:
        with open(p, encoding="utf-8") as fh:
            obj = json.load(fh)
        if isinstance(obj, list):
            print(f"Parsed cookie file: {p} (entries: {len(obj)})")
            names = [c.get("name") for c in obj[:20]]
            print("Cookie names (sample up to 20):", names)
            # quick checks
            keys = {c.get("name") for c in obj if isinstance(c, dict)}
            for required in ("sessionid", "ds_user_id", "csrftoken"):
                print(required, "present:", required in keys)
            sys.exit(0)
        else:
            print(f"Found JSON but not a list at {p}, type={type(obj)}")
    except Exception as e:
        print("Failed to parse", p, ":", e)

print("ERROR: could not parse any cookie file in data/ or repo root (expected JSON array).")
sys.exit(2)
