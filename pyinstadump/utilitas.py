def konversi_cookie_string(cookie_string: str) -> dict:
    """
    Mengonversi cookie dalam format string mentah ke format JSON
    yang dapat digunakan oleh Playwright.

    Args:
        cookie_string: Cookie yang disalin dari browser dalam bentuk string.

    Returns:
        Sebuah dictionary yang berisi daftar cookies untuk state browser.
    """
    cookies = []
    if not cookie_string:
        return {"cookies": [], "origins": []}
    
    pasangan_cookie = cookie_string.split(";")

    for pasangan in pasangan_cookie:
        if "=" in pasangan:
            nama, nilai = pasangan.strip().split("=", 1)

            cookie = {
                "name": nama.strip(),
                "value": nilai.strip(),
                "domain": ".instagram.com",
                "path": "/",
                "expires": -1,
                "httpOnly": False,
                "secure": True,
                "sameSite": "None"
            }

            if nama.strip() in ['sessionid', 'csrftoken']:
                cookie["httpOnly"] = True
            
            cookies.append(cookie)
    
    return {
        "cookies": cookies,
        "origins": [
            
        ]
    }