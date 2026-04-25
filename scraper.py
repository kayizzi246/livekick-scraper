name: LiveKick Scraper

# This workflow runs the scraper every 10 minutes, 24/7, for free.
# GitHub Actions gives public repositories 2,000 minutes/month for free,
# and unlimited minutes for public repos. Each scraper run takes about
# 30 seconds, so 6 runs per hour × 24 hours × 30 days ≈ 72 hours of
# compute per month. Comfortably within the free quota.

on:
  # Run on a schedule.
  schedule:
    # cron: minute hour day month day-of-week
    # "*/10 * * * *" = every 10 minutes
    - cron: "*/10 * * * *"

  # Also let you trigger it manually from the Actions tab in GitHub.
  # Useful for your school demo: click "Run workflow" and the audience
  # sees WordPress fill up live.
  workflow_dispatch:

jobs:
  scrape:
    runs-on: ubuntu-latest
    timeout-minutes: 5

    steps:
      - name: Check out the repository
        uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install requests

      - name: Run the scraper
        env:
          WP_URL:           ${{ secrets.WP_URL }}
          WP_SCRAPER_KEY:   ${{ secrets.WP_SCRAPER_KEY }}
          API_FOOTBALL_KEY: ${{ secrets.API_FOOTBALL_KEY }}
        run: python scraper.py
