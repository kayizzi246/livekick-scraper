"""
LiveKick Scraper
================

Runs every 10 minutes. Pulls today's fixtures from API-Football, tries to
find a working stream/aggregator URL for each, and pushes them to your
WordPress site at /wp-json/livekick/v1/scraper/push.

Run locally:
    pip install requests
    python scraper.py

Run on a schedule (PythonAnywhere / Render / a $5 VPS):
    Use cron / scheduled task to run this every 10 minutes.

ENVIRONMENT VARIABLES (set these on your server):
    WP_URL            e.g. https://livekickscore.com
    WP_SCRAPER_KEY    must match LIVEKICK_SCRAPER_KEY in livekick-backend.php
    API_FOOTBALL_KEY  your API-Football key
"""

import os
import sys
import time
import requests
from datetime import datetime, timezone
from urllib.parse import quote_plus

# ----------------------------------------------------------------------
# CONFIG
# ----------------------------------------------------------------------
WP_URL           = os.environ.get("WP_URL", "https://livekickscore.com")
WP_SCRAPER_KEY   = os.environ.get("WP_SCRAPER_KEY", "CHANGE_ME_TO_A_LONG_RANDOM_STRING")
API_FOOTBALL_KEY = os.environ.get("API_FOOTBALL_KEY", "7894a63a80b9badfa32cae441c31fd40")

API_FOOTBALL_BASE = "https://v3.football.api-sports.io"
PUSH_ENDPOINT     = f"{WP_URL}/wp-json/livekick/v1/scraper/push"

# Premium competitions/teams list — mirrored from your Flutter code so the
# WordPress side stays in sync with the app's premium logic.
PREMIUM_COMPS = {
    "UEFA Champions League", "Champions League", "Copa Libertadores",
    "FIFA World Cup", "Euro Championship", "UEFA Europa League",
}
PREMIUM_TEAMS = {
    "real madrid", "barcelona", "atletico madrid",
    "manchester united", "manchester city", "liverpool",
    "arsenal", "chelsea", "tottenham",
    "bayern munich", "borussia dortmund",
    "paris saint germain", "paris sg", "psg",
    "juventus", "inter", "inter milan", "ac milan", "milan",
    "napoli", "roma", "lazio", "ajax", "psv eindhoven",
    "flamengo", "palmeiras",
}


def is_premium_match(home, away, competition):
    for c in PREMIUM_COMPS:
        if c in competition:
            return True
    h = home.lower()
    a = away.lower()
    n = 0
    for b in PREMIUM_TEAMS:
        if b in h or b in a:
            n += 1
            if n >= 2:
                return True
    return False


def find_stream_url(home, away):
    """
    Build a search URL on a free aggregator. In a real scraper you would
    visit the aggregator's homepage, parse its match list, and grab the
    .m3u8 link from the matching game's iframe. For a school project the
    aggregator search URL is enough — the FlutterFlow app already opens
    that URL in a WebView and the user picks the source.
    """
    query = quote_plus(f"{home} vs {away}")
    # SofaScore search is the safe default — it always works and never
    # needs takedown. Replace this with a stream aggregator URL if you
    # want a working iframe right away.
    return f"https://www.sofascore.com/search?q={query}"


def fetch_todays_fixtures():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    url = f"{API_FOOTBALL_BASE}/fixtures?date={today}"
    headers = {"x-apisports-key": API_FOOTBALL_KEY}
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json().get("response", [])


def push_to_wordpress(payload):
    headers = {
        "Content-Type":   "application/json",
        "X-Livekick-Key": WP_SCRAPER_KEY,
    }
    r = requests.post(PUSH_ENDPOINT, json=payload, headers=headers, timeout=20)
    return r.status_code, r.text


def transform(fixture):
    f = fixture.get("fixture", {}) or {}
    l = fixture.get("league", {})  or {}
    t = fixture.get("teams", {})   or {}
    g = fixture.get("goals", {})   or {}
    h = (t.get("home") or {})
    a = (t.get("away") or {})
    s = (f.get("status") or {})

    home_name = h.get("name") or "Home"
    away_name = a.get("name") or "Away"
    competition = l.get("name") or "Football"
    status_short = s.get("short") or "NS"
    is_live = status_short in {"1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT"}

    return {
        "home_team":        home_name,
        "away_team":        away_name,
        "home_logo":        h.get("logo") or "",
        "away_logo":        a.get("logo") or "",
        "home_score":       int(g.get("home") or 0),
        "away_score":       int(g.get("away") or 0),
        "competition":      competition,
        "competition_logo": l.get("logo") or "",
        "kickoff_utc":      f.get("date") or "",
        "minute":           int(s.get("elapsed") or 0),
        "status":           status_short,
        "venue":            ((f.get("venue") or {}).get("name")) or "",
        "stream_url":       find_stream_url(home_name, away_name),
        "is_live":          is_live,
        "is_premium":       is_premium_match(home_name, away_name, competition),
    }


def main():
    print(f"[{datetime.now()}] Starting LiveKick scraper run…")

    # Sanity-check that secrets/env vars are actually set. This makes
    # GitHub Actions failures obvious instead of cryptic.
    problems = []
    if WP_SCRAPER_KEY == "CHANGE_ME_TO_A_LONG_RANDOM_STRING":
        problems.append("WP_SCRAPER_KEY is still the default placeholder")
    if not WP_URL or not WP_URL.startswith("http"):
        problems.append(f"WP_URL looks invalid: {WP_URL!r}")
    if not API_FOOTBALL_KEY or len(API_FOOTBALL_KEY) < 20:
        problems.append("API_FOOTBALL_KEY is missing or too short")
    if problems:
        print("  CONFIG ERRORS:", file=sys.stderr)
        for p in problems:
            print(f"    - {p}", file=sys.stderr)
        return 1

    print(f"  Target: {WP_URL}")

    try:
        fixtures = fetch_todays_fixtures()
    except Exception as e:
        print(f"  ERROR fetching fixtures: {e}", file=sys.stderr)
        return 1

    print(f"  Found {len(fixtures)} fixtures for today.")
    ok = fail = 0
    for fx in fixtures:
        payload = transform(fx)
        try:
            code, text = push_to_wordpress(payload)
            if code == 200:
                ok += 1
            else:
                fail += 1
                print(f"  PUSH FAIL ({code}): {payload['home_team']} vs {payload['away_team']} → {text[:150]}")
        except Exception as e:
            fail += 1
            print(f"  PUSH ERROR: {e}", file=sys.stderr)
        time.sleep(0.2)  # be polite

    print(f"  Done. OK={ok} FAIL={fail}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
