#!/usr/bin/env python3
"""
Build a cleaned panel CSV from the raw absence data.

Computes per-club-season injury burden and match status counts.
Does not include squad values or standings (those come from other sources).

Output: cleaned/panel.csv
"""

import json
from pathlib import Path

import pandas as pd

RAW_DIR = Path("raw")
OUTPUT = Path("cleaned/panel.csv")

TOP_LEAGUES = ["GB1", "FR1", "L1", "ES1", "IT1", "PO1"]
LEAGUE_LABELS = {
    "GB1": "Premier League", "FR1": "Ligue 1", "L1": "Bundesliga",
    "ES1": "La Liga", "IT1": "Serie A", "PO1": "Liga Portugal",
}

IN_SQUAD = {
    "starting", "sub_in", "bench", "injured", "suspended",
    "national_team", "other_absence", "absent", "not_in_squad",
}
ABSENCE = {"injured", "suspended"}


def classify_detail(status: str, detail: str) -> str:
    if status != "absent":
        return status
    dl = detail.lower()
    if "suspension" in dl or "suspended" in dl or "card" in dl:
        return "suspended"
    if "national team" in dl:
        return "national_team"
    return "other_absence"


def main() -> None:
    rows: list[dict] = []

    for league in TOP_LEAGUES:
        league_dir = RAW_DIR / league
        if not league_dir.exists():
            continue

        for season_dir in sorted(league_dir.iterdir()):
            if not season_dir.is_dir():
                continue
            season = season_dir.name

            for club_file in sorted(season_dir.glob("*.json")):
                data = json.loads(club_file.read_text())
                tm_id = data.get("tmId", 0)
                club = data.get("club", "")
                tm_slug = data.get("tmSlug", "")

                league_comp = next(
                    (c for c in data.get("competitions", []) if c.get("code") == league),
                    None,
                )
                if not league_comp:
                    continue

                injured = 0
                suspended = 0
                national_team = 0
                other_absent = 0
                starting = 0
                sub_in = 0
                bench = 0
                not_in_squad = 0
                total_in_squad = 0
                squad_players = 0

                for player in league_comp.get("players", []):
                    player_in_squad = False
                    for m in player.get("matches", []):
                        status = m.get("status", "unknown")
                        detail = m.get("detail", "")
                        status = classify_detail(status, detail)

                        if status in IN_SQUAD:
                            total_in_squad += 1
                            player_in_squad = True
                        if status == "injured":
                            injured += 1
                        elif status == "suspended":
                            suspended += 1
                        elif status == "national_team":
                            national_team += 1
                        elif status in ("other_absence", "absent"):
                            other_absent += 1
                        elif status == "starting":
                            starting += 1
                        elif status == "sub_in":
                            sub_in += 1
                        elif status == "bench":
                            bench += 1
                        elif status == "not_in_squad":
                            not_in_squad += 1

                    if player_in_squad:
                        squad_players += 1

                total_absent = injured + suspended
                injury_burden = total_absent / total_in_squad if total_in_squad > 0 else None

                n_competitions = len(data.get("competitions", []))
                in_europe = int(any(
                    c.get("code", "") in ("CL", "EL", "UCOL", "USC")
                    or c.get("code", "").startswith("EC")
                    or c.get("code", "").startswith("UC")
                    for c in data.get("competitions", [])
                ))

                rows.append({
                    "league": league,
                    "league_name": LEAGUE_LABELS.get(league, league),
                    "season": season,
                    "tm_id": tm_id,
                    "club": club,
                    "tm_slug": tm_slug,
                    "injury_burden": injury_burden,
                    "injured_matchdays": injured,
                    "suspended_matchdays": suspended,
                    "national_team_matchdays": national_team,
                    "other_absent_matchdays": other_absent,
                    "starting_matchdays": starting,
                    "sub_in_matchdays": sub_in,
                    "bench_matchdays": bench,
                    "not_in_squad_matchdays": not_in_squad,
                    "total_in_squad": total_in_squad,
                    "squad_players": squad_players,
                    "n_competitions": n_competitions,
                    "in_europe": in_europe,
                })

    df = pd.DataFrame(rows)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT, index=False)
    print(f"Saved: {OUTPUT}")
    print(f"  {len(df)} club-seasons")
    print(f"  Leagues: {sorted(df['league'].unique())}")
    print(f"  Seasons: {sorted(df['season'].unique())}")


if __name__ == "__main__":
    main()
