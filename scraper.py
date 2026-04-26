#!/usr/bin/env python3
"""
LiveKick Scraper
================
Runs every 10 minutes via GitHub Actions.

Pipeline:
  1. Fetch live + today's fixtures from API-Football.
  2. For each match, build candidate stream/aggregator URLs.
     We don't scrape pirate sites (unreliable + risky) — we hand the
     app aggregator-search URLs that point straight at the match.
  3. POST every match to WordPress at /wp-json/livekick/v1/scraper/push
     using the x-livekick-key header for auth.

Required env vars (set as GitHub Actions secrets):
  WP_URL            e.g. https://livekickscore.com
  WP_SCRAPER_KEY    must match LIVEKICK_SCRAPER_KEY in the WP plugin
  API_FOOTBALL_KEY  your api-sports.io key

Exits with code 0 on success so the workflow stays green even when
individual matches fail to push.
"""

import os
import sys
import time
import json
import urllib.parse
from datetime import datetime, timezone

import requests

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

WP_URL = os.environ.get("WP_URL", "").rstrip("/")
WP_KEY = os.environ.get("WP_SCRAPER_KEY", "")
AF_KEY = os.environ.get("API_FOOTBALL_KEY", "")

API_FOOTBALL_BASE = "https://v3.football.api-sports.io"

# Competitions we always want to push (by API-Football league name).
# Everything else is pushed only if currently LIVE, to keep the WP DB lean.
PRIORITY_LEAGUES = {
    "UEFA Champions League",
    "UEFA Europa League",
    "UEFA Europa Conference League",
    "Premier League",
    "La Liga",
    "Serie A",
    "Bundesliga",
    "Ligue 1",
    "FA Cup",
    "Copa del Rey",
    "Coppa Italia",
    "DFB Pokal",
    "Coupe de France",
    "FIFA Club World Cup",
    "FIFA World Cup",
    "Euro Championship",
    "Copa America",
    "AFCON",
    "Africa Cup of Nations",
    "CAF Champions League",
    "Uganda Premier League",
}

LIVE_STATUSES = {"1H", "2H", "HT", "ET", "BT", "P", "LIVE", "INT"}
SCHEDULED_STATUSES = {"TBD", "NS"}


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

def log(msg: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def fail(msg: str) -> None:
    log(f"FATAL: {msg}")
    sys.exit(1)


# -----------------------------------------------------------------------------
# Validation
# -----------------------------------------------------------------------------

def validate_env() -> None:
    missing = []
    if not WP_URL:
        missing.append("WP_URL")
    if not WP_KEY:
        missing.append("WP_SCRAPER_KEY")
    if not AF_KEY:
        missing.append("API_FOOTBALL_KEY")
    if missing:
        fail(f"Missing env vars: {', '.join(missing)}")
    if not WP_URL.startswith(("http://", "https://")):
        fail(f"WP_URL must start with http:// or https:// — got: {WP_URL}")


# -----------------------------------------------------------------------------
# API-Football
# -----------------------------------------------------------------------------

def api_football_get(path: str, params: dict | None = None) -> dict:
    headers = {"x-apisports-key": AF_KEY}
    url = f"{API_FOOTBALL_BASE}{path}"
    try:
        r = requests.get(url, headers=headers, params=params or {}, timeout=20)
    except requests.RequestException as e:
        log(f"API-Football request failed: {e}")
        return {}
    if r.status_code != 200:
        log(f"API-Football returned {r.status_code} for {path}: {r.text[:200]}")
        return {}
    try:
        return r.json()
    except ValueError:
        log("API-Football returned non-JSON")
        return {}


def fetch_live_fixtures() -> list[dict]:
    data = api_football_get("/fixtures", {"live": "all"})
    return data.get("response", []) or []


def fetch_today_fixtures() -> list[dict]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = api_football_get("/fixtures", {"date": today})
    return data.get("response", []) or []


# -----------------------------------------------------------------------------
# Stream URL builder
# -----------------------------------------------------------------------------

def build_stream_url(home: str, away: str, status: str) -> str:
    """
    Build the best-effort match URL the app should open.

    For LIVE matches we point at TotalSportek's homepage (which lists
    every live game), since deep-linking to a specific match on these
    aggregators is unreliable — they change slugs constantly.

    For non-live matches we return an empty string and let the app fall
    back to its hard-coded aggregator list.
    """
    if status in LIVE_STATUSES:
        # TotalSportek is the most reliable free aggregator for live
        # links right now. Users land on the listing and tap their match.
        q = urllib.parse.quote(f"{home} vs {away}")
        return f"https://www.totalsportek.click/?s={q}"
    return ""


def is_priority(league_name: str) -> bool:
    name = (league_name or "").lower()
    for p in PRIORITY_LEAGUES:
        if p.lower() in name:
            return True
    return False


def is_premium_match(league_name: str, home: str, away: str) -> bool:
    premium_comps = {
        "uefa champions league",
        "champions league",
        "copa libertadores",
        "fifa world cup",
        "euro championship",
        "uefa europa league",
    }
    big_clubs = {
        "real madrid", "barcelona", "atletico madrid",
        "manchester united", "manchester city", "liverpool", "arsenal",
        "chelsea", "tottenham", "bayern munich", "borussia dortmund",
        "paris saint germain", "psg", "juventus", "inter", "ac milan",
        "milan", "napoli", "roma", "lazio", "ajax",
    }
    lname = (league_name or "").lower()
    for c in premium_comps:
        if c in lname:
            return True
    h = (home or "").lower()
    a = (away or "").lower()
    matches = sum(1 for b in big_clubs if b in h or b in a)
    return matches >= 2


# -----------------------------------------------------------------------------
# Transform API-Football → WordPress payload
# -----------------------------------------------------------------------------

def transform_fixture(fx: dict) -> dict | None:
    fixture = fx.get("fixture") or {}
    league = fx.get("league") or {}
    teams = fx.get("teams") or {}
    home = teams.get("home") or {}
    away = teams.get("away") or {}
    goals = fx.get("goals") or {}
    status_obj = fixture.get("status") or {}

    home_name = (home.get("name") or "").strip()
    away_name = (away.get("name") or "").strip()
    if not home_name or not away_name:
        return None

    status = (status_obj.get("short") or "NS").strip()
    is_live = status in LIVE_STATUSES

    return {
        "id": int(fixture.get("id") or 0),
        "home_team": home_name,
        "away_team": away_name,
        "home_logo": home.get("logo") or "",
        "away_logo": away.get("logo") or "",
        "home_score": int(goals.get("home") or 0),
        "away_score": int(goals.get("away") or 0),
        "competition": league.get("name") or "Football",
        "competition_logo": league.get("logo") or "",
        "kickoff_utc": fixture.get("date") or "",
        "minute": int(status_obj.get("elapsed") or 0),
        "status": status,
        "venue": ((fixture.get("venue") or {}).get("name") or ""),
        "stream_url": build_stream_url(home_name, away_name, status),
        "is_live": is_live,
        "is_premium": is_premium_match(league.get("name") or "", home_name, away_name),
    }


# -----------------------------------------------------------------------------
# Push to WordPress
# -----------------------------------------------------------------------------

def push_to_wp(payload: dict) -> bool:
    url = f"{WP_URL}/wp-json/livekick/v1/scraper/push"
    headers = {
        "Content-Type": "application/json",
        "x-livekick-key": WP_KEY,
        "User-Agent": "LiveKick-Scraper/1.0",
    }
    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
    except requests.RequestException as e:
        log(f"  push failed (network): {e}")
        return False

    if r.status_code == 200:
        return True

    # Surface the most useful debugging info — this is what made the
    # GitHub Actions runs fail silently before.
    body = r.text[:300] if r.text else "(empty)"
    log(f"  push failed: HTTP {r.status_code} — {body}")
    return False


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main() -> int:
    log("LiveKick scraper starting")
    validate_env()
    log(f"Target: {WP_URL}")

    # 1. Fetch live games — these are always pushed.
    live = fetch_live_fixtures()
    log(f"Live fixtures from API-Football: {len(live)}")

    # 2. Fetch today's full slate, filtered to priority leagues.
    today = fetch_today_fixtures()
    log(f"Today's fixtures from API-Football: {len(today)}")

    # Build the set we care about — dedupe by fixture id.
    seen: set[int] = set()
    to_push: list[dict] = []

    for fx in live:
        fid = ((fx.get("fixture") or {}).get("id") or 0)
        if fid and fid not in seen:
            seen.add(fid)
            payload = transform_fixture(fx)
            if payload:
                to_push.append(payload)

    for fx in today:
        fid = ((fx.get("fixture") or {}).get("id") or 0)
        if fid in seen:
            continue
        league_name = ((fx.get("league") or {}).get("name") or "")
        if not is_priority(league_name):
            continue
        seen.add(fid)
        payload = transform_fixture(fx)
        if payload:
            to_push.append(payload)

    log(f"Pushing {len(to_push)} matches to WordPress")
    if not to_push:
        log("Nothing to push. Exiting cleanly.")
        return 0

    ok = 0
    fail_count = 0
    for i, payload in enumerate(to_push, 1):
        label = f"{payload['home_team']} vs {payload['away_team']}"
        live_tag = " [LIVE]" if payload["is_live"] else ""
        log(f"[{i}/{len(to_push)}] {label}{live_tag}")
        if push_to_wp(payload):
            ok += 1
        else:
            fail_count += 1
        # Be gentle on the WP server — small pause between writes.
        time.sleep(0.15)

    log(f"Done. Success: {ok}, Failed: {fail_count}")
    # Always exit 0 so the workflow stays green; partial failures are
    # logged and visible in the run output.
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log(f"Unhandled error: {e}")
        # Still exit 0 so transient failures don't spam your inbox.
        sys.exit(0)
