name: Run Instagram Profile Scraper

on:
  workflow_dispatch:

jobs:
  scrape:
    runs-on: ubuntu-latest
    timeout-minutes: 180

    steps:
      - name: Checkout repo
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: "3.10"

      - name: Install system dependencies (for Playwright / browsers)
        run: |
          sudo apt-get update -y
          sudo apt-get install -y --no-install-recommends \
            ca-certificates \
            libnss3 \
            libatk-bridge2.0-0 \
            libgtk-3-0 \
            libxss1 \
            libasound2t64 \
            libasound2-data \
            libgbm1 \
            libglib2.0-0 \
            libx11-6 \
            libxcomposite1 \
            libxcursor1 \
            libxdamage1 \
            libxrandr2 \
            libxinerama1 \
            libpangocairo-1.0-0 \
            libpango-1.0-0 \
            libatk1.0-0 \
            libcups2 \
            libnspr4 \
            libxext6 \
            libffi-dev \
            libx264-dev \
            ffmpeg \
            wget \
            unzip || true
          # (some packages may already be present; allow non-fatal errors)

      - name: Install Python packages
        run: |
          python -m pip install --upgrade pip
          if [ -f requirements.txt ]; then pip install -r requirements.txt; else pip install playwright aiohttp; fi
          # Install Playwright browsers and any OS deps it needs
          python -m playwright install --with-deps

      - name: Run scraper
        env:
          COOKIES: ${{ secrets.COOKIES }}
        run: |
          mkdir -p data
          # If your script expects the cookies file rather than env string:
          # echo "$COOKIES" > data/www.instagram.com.cookies.json
          python scrape_profiles.py

      - name: Upload results
        uses: actions/upload-artifact@v4
        with:
          name: ig-profile-results
          path: data/results.csv
