#!/usr/bin/env python3
"""
Fetch Transfermarkt 'Periods of Absence' data for top-league clubs.

Scrapes the ausfallzeiten page across all competitions for each club in a given
season, parses the matchday-by-matchday grid, and saves one JSON per club.

Usage:
    source .venv/bin/activate
    python scripts/fetch-transfermarkt-absences.py                            # PL, 25/26
    python scripts/fetch-transfermarkt-absences.py --league FR1               # Ligue 1
    python scripts/fetch-transfermarkt-absences.py --league ES1,IT1,L1        # multiple leagues
    python scripts/fetch-transfermarkt-absences.py --league all               # all 6 leagues
    python scripts/fetch-transfermarkt-absences.py --season 2016-2025         # season range
    python scripts/fetch-transfermarkt-absences.py --club west-ham-united     # single club
"""

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

BASE_URL = "https://www.transfermarkt.com"
OUTPUT_DIR = Path("raw")
DELAY = 3  # seconds between requests — be respectful

# Supported leagues: code → (TM page slug, display name)
LEAGUES = {
    "GB1": ("premier-league", "Premier League"),
    "FR1": ("ligue-1", "Ligue 1"),
    "L1": ("1-bundesliga", "Bundesliga"),
    "ES1": ("laliga", "La Liga"),
    "IT1": ("serie-a", "Serie A"),
    "PO1": ("liga-portugal", "Liga Portugal"),
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

# ---------------------------------------------------------------------------
# Status classification
# ---------------------------------------------------------------------------

# Cell class suffix → status mapping
# TM encodes status as ausfallzeiten_{letter} in the cell's CSS class.
# Verified by cross-referencing raw HTML classes against JS-rendered inner spans.
STATUS_MAP = {
    "s": "starting",       # Starting XI (bg_gruen_65)
    "e": "sub_in",         # Substituted in (bg_gruen_50)
    "k": "bench",          # On the bench
    "v": "injured",        # Injured or ill (bg_rot_65, inner span: verletzt-table)
    "a": "absent",         # Suspended, national team, or visa (inner span varies)
    "r": "not_in_squad",   # Not included in matchday squad
}

# Single-letter suffix regex: ausfallzeiten_ followed by exactly one letter
# at a word boundary. This avoids matching ausfallzeiten_bg_rot_20.
SUFFIX_RE = re.compile(r"ausfallzeiten_([a-z])\b")


def classify_status(cell_cls: str) -> str:
    """Map a TM cell's CSS classes to a normalised status string."""
    # ausfallzeiten_bg_rot_20 = player was at a different club during this period
    # (transferred/loaned). Usually has "opacity" class too (greyed out).
    if "ausfallzeiten_bg_rot" in cell_cls:
        return "not_at_club"

    m = SUFFIX_RE.search(cell_cls)
    if m:
        return STATUS_MAP.get(m.group(1), "unknown")

    # ausfallzeiten_ with no letter suffix = not registered / youth / blank
    if "ausfallzeiten_" in cell_cls:
        return "not_included"

    return "unknown"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def fetch(url: str, client: httpx.Client) -> BeautifulSoup:
    """GET a URL, return parsed HTML. Raises on HTTP errors."""
    resp = client.get(url, follow_redirects=True)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "lxml")


def fetch_with_retry(url: str, client: httpx.Client, retries: int = 4) -> BeautifulSoup:
    """Fetch with retry on transient errors (timeouts, 5xx, connection resets)."""
    for attempt in range(retries + 1):
        try:
            return fetch(url, client)
        except (httpx.HTTPStatusError, httpx.TransportError) as e:
            if attempt == retries:
                raise
            wait = DELAY * (attempt + 2)  # 6s, 9s, 12s, 15s
            print(f"    Retry {attempt + 1}/{retries} after {wait}s ({e})")
            time.sleep(wait)
    raise RuntimeError("unreachable")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def discover_clubs(
    season: str, league_code: str, client: httpx.Client
) -> list[dict]:
    """Scrape a league page to get all clubs with TM slugs and IDs."""
    league_slug, league_name = LEAGUES[league_code]
    url = (
        f"{BASE_URL}/{league_slug}/startseite/wettbewerb/"
        f"{league_code}/plus/?saison_id={season}"
    )
    soup = fetch_with_retry(url, client)

    clubs: list[dict] = []
    seen_slugs: set[str] = set()

    for link in soup.select("table.items td.hauptlink a[href*='/verein/']"):
        href = link.get("href", "")
        m = re.search(r"/([^/]+)/startseite/verein/(\d+)", href)
        if m and m.group(1) not in seen_slugs:
            seen_slugs.add(m.group(1))
            clubs.append({
                "slug": m.group(1),
                "tmId": int(m.group(2)),
                "name": link.text.strip(),
            })

    if not clubs:
        raise ValueError(
            f"No clubs found for {league_name} ({league_code}) season {season}"
        )

    return clubs


def discover_competitions(soup: BeautifulSoup, season: str) -> list[dict]:
    """Parse the season/competition dropdown on an ausfallzeiten page."""
    select = soup.select_one("select")
    if not select:
        return []

    comps: list[dict] = []
    for opt in select.select("option"):
        val = opt.get("value", "")
        # Values look like "GB1&2025", "CGB&2025", "CL&2025"
        if f"&{season}" in val:
            code = val.split("&")[0]
            comps.append({
                "code": code,
                "label": opt.text.strip(),
            })
    return comps


def parse_grid(soup: BeautifulSoup) -> list[dict]:
    """Parse the ausfallzeiten table into structured player data."""
    table = soup.select_one("table.ausfallzeiten-table")
    if not table:
        return []

    # Matchday/round labels from header row.
    # PL uses numbers ("1", "2", ...), cups use round codes ("2R", "3R", "QF", "SF", "F").
    matchdays: list[str] = []
    for th in table.select("thead th"):
        text = th.text.strip()
        if text:  # skip the empty first column header
            matchdays.append(text)

    players: list[dict] = []

    # TM's HTML has no <tbody> — rows are direct children of <table>.
    # Filter to rows that contain <td> (skip the <thead> row).
    data_rows = [r for r in table.select("tr") if r.select("td")]

    for row in data_rows:
        all_cells = row.select("td")
        if len(all_cells) < 4:
            continue

        # cells[0] = position group colour, cells[1] = name link, cells[2] = position abbr
        name_link = all_cells[1].select_one("a[href*='spieler']")
        if not name_link:
            continue

        name = name_link.text.strip()
        href = name_link.get("href", "")
        tm_id_match = re.search(r"spieler/(\d+)", href)
        tm_id = int(tm_id_match.group(1)) if tm_id_match else None
        position = all_cells[2].text.strip()

        # Matchday cells start at index 3. TM duplicates each cell: one with
        # class "hide" (has full detail — inner spans with injury/suspension text)
        # and one visible (populated by JS client-side). We want the "hide" cells
        # because they contain the data in the raw HTML.
        md_cells = [
            c for c in all_cells[3:]
            if "hide" in (c.get("class") or [])
        ]

        matches: list[dict] = []

        for i, cell in enumerate(md_cells):
            if i >= len(matchdays):
                break

            cell_cls = " ".join(cell.get("class", []))
            status = classify_status(cell_cls)

            # Detail text lives in the inner span (span > span) — e.g.
            # <span id="pid/mid/min"><span title="Back injury - Return unknown" class="verletzt-table">
            inner_span = cell.select_one("span span[title]")
            detail = (inner_span.get("title", "") if inner_span else "").strip()
            inner_cls = " ".join(inner_span.get("class", [])) if inner_span else ""

            # Refine the generic "absent" status using inner span class.
            # TM uses different icon classes for each absence type:
            #   verletzt-table = injury/illness
            #   gesperrt-table / suspension-icon = suspension
            #   national-table / absence-icon = national team / other
            if status == "absent" and inner_cls:
                if "verletzt" in inner_cls:
                    status = "injured"
                elif "gesperrt" in inner_cls or "suspension" in inner_cls:
                    status = "suspended"
                elif "national" in inner_cls or "absence" in inner_cls:
                    status = "national_team"
            elif status == "absent" and detail:
                # Fallback: classify from detail text if no inner class
                dl = detail.lower()
                if "suspension" in dl or "suspended" in dl or "card" in dl:
                    status = "suspended"
                elif "national team" in dl:
                    status = "national_team"
                elif "visa" in dl or "leave" in dl:
                    status = "other_absence"

            entry: dict = {"round": matchdays[i], "status": status}

            # Only attach detail for non-playing statuses where it's informative
            if detail and status not in ("starting", "sub_in", "bench"):
                entry["detail"] = detail

            matches.append(entry)

        players.append({
            "name": name,
            "tmId": tm_id,
            "position": position,
            "matches": matches,
        })

    return players


# ---------------------------------------------------------------------------
# Main scrape loop
# ---------------------------------------------------------------------------

def scrape_club(club: dict, season: str, client: httpx.Client) -> dict:
    """Scrape all competitions for one club in a season."""
    print(f"\n{'=' * 60}")
    print(f"  {club['name']} ({club['slug']}, id={club['tmId']})")
    print(f"{'=' * 60}")

    base_url = f"{BASE_URL}/{club['slug']}/ausfallzeiten/verein/{club['tmId']}"

    # 1. Fetch default page to discover available competitions
    soup = fetch_with_retry(base_url, client)
    time.sleep(DELAY)

    comps = discover_competitions(soup, season)
    if not comps:
        print("  WARNING: No competitions found for this season")
        return {
            "club": club["name"],
            "tmSlug": club["slug"],
            "tmId": club["tmId"],
            "season": season,
            "scrapedAt": datetime.now(timezone.utc).isoformat(),
            "competitions": [],
        }

    print(f"  Competitions: {', '.join(c['label'] for c in comps)}")

    # 2. Fetch each competition page and parse the grid
    competitions_data: list[dict] = []

    for comp in comps:
        print(f"  Fetching {comp['label']}...")

        # reldata param: "GB1&2025" — httpx encodes the & correctly as %26 in the value
        comp_url = f"{base_url}?reldata={comp['code']}%26{season}"

        try:
            comp_soup = fetch_with_retry(comp_url, client)
            players = parse_grid(comp_soup)

            total_entries = sum(len(p["matches"]) for p in players)
            competitions_data.append({
                "code": comp["code"],
                "name": comp["label"],
                "players": players,
            })
            print(f"    -> {len(players)} players, {total_entries} matchday entries")
        except Exception as e:
            print(f"    ERROR: {e}")

        time.sleep(DELAY)

    return {
        "club": club["name"],
        "tmSlug": club["slug"],
        "tmId": club["tmId"],
        "season": season,
        "scrapedAt": datetime.now(timezone.utc).isoformat(),
        "competitions": competitions_data,
    }


def scrape_season(
    season: str,
    league_code: str,
    club_filter: str | None,
    client: httpx.Client,
) -> None:
    """Scrape all clubs for a single league and season."""
    league_slug, league_name = LEAGUES[league_code]
    season_dir = OUTPUT_DIR / league_code / season
    season_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nDiscovering {league_name} clubs for {season}/{int(season) + 1}...")
    clubs = discover_clubs(season, league_code, client)
    print(f"Found {len(clubs)} clubs")
    time.sleep(DELAY)

    if club_filter:
        matching = [c for c in clubs if c["slug"] == club_filter]
        if not matching:
            print(f"\nClub '{club_filter}' not found for {season}. Available slugs:")
            for c in sorted(clubs, key=lambda x: x["slug"]):
                print(f"  {c['slug']}")
            return
        clubs = matching

    for i, club in enumerate(clubs):
        out_file = season_dir / f"{club['slug']}.json"

        # Skip if already scraped (allows resuming interrupted runs)
        if out_file.exists():
            print(f"\n[{i + 1}/{len(clubs)}] {club['name']} — already exists, skipping")
            continue

        print(f"\n[{i + 1}/{len(clubs)}]", end="")
        data = scrape_club(club, season, client)
        data["league"] = league_code

        out_file.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        print(f"  Saved: {out_file}")

    print(f"\n{league_name} {season}/{int(season) + 1} done! Files in {season_dir}/")


def main() -> None:
    league_codes = ", ".join(f"{k} ({v[1]})" for k, v in LEAGUES.items())
    parser = argparse.ArgumentParser(
        description="Fetch Transfermarkt absence data for European league clubs"
    )
    parser.add_argument(
        "--season",
        default="2025",
        help=(
            "Season start year or range. Examples: "
            "'2025' for 25/26, '2016-2025' for 10 seasons (default: 2025)"
        ),
    )
    parser.add_argument(
        "--league",
        default="GB1",
        help=(
            f"League code(s), comma-separated, or 'all'. "
            f"Available: {league_codes} (default: GB1)"
        ),
    )
    parser.add_argument(
        "--club",
        help="Single club TM slug to fetch (default: all clubs in the league)",
    )
    args = parser.parse_args()

    # Parse season range
    if "-" in args.season:
        start, end = args.season.split("-", 1)
        seasons = [str(y) for y in range(int(start), int(end) + 1)]
    else:
        seasons = [args.season]

    # Parse league codes
    if args.league.lower() == "all":
        league_codes_list = list(LEAGUES.keys())
    else:
        league_codes_list = [c.strip().upper() for c in args.league.split(",")]
        for code in league_codes_list:
            if code not in LEAGUES:
                print(f"Unknown league '{code}'. Available: {league_codes}")
                sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    client = httpx.Client(headers=HEADERS, timeout=60)

    try:
        total_jobs = len(league_codes_list) * len(seasons)
        job = 0
        for code in league_codes_list:
            for season in seasons:
                job += 1
                _, league_name = LEAGUES[code]
                print(f"\n{'#' * 60}")
                print(f"  [{job}/{total_jobs}] {league_name} ({code}) — {season}/{int(season) + 1}")
                print(f"{'#' * 60}")
                scrape_season(season, code, args.club, client)

        print(f"\nAll done! {total_jobs} league-season(s) saved to {OUTPUT_DIR}/")

    finally:
        client.close()


if __name__ == "__main__":
    main()
