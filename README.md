# Availability Data

Player availability data for six European football leagues. Covers the Premier League, La Liga, Bundesliga, Serie A, Ligue 1, and Liga Portugal from 2015/16 to the current season.

Updated weekly from [Transfermarkt](https://www.transfermarkt.com/).

## What's in the data

For every player at every club in every matchday, we record their status:

| Status | Meaning |
|--------|---------|
| `starting` | Started the match |
| `sub_in` | Came on as a substitute |
| `bench` | Named in the matchday squad but did not play |
| `injured` | Absent due to injury or illness |
| `suspended` | Absent due to suspension |
| `national_team` | Away on international duty |
| `not_in_squad` | Fit but not selected for the matchday squad |
| `not_at_club` | Transferred or on loan elsewhere |
| `not_included` | Not registered or in youth setup |

Injury entries include a `detail` field with the specific injury type and expected return date (e.g. "Hamstring injury - Return expected on 05/01/2026").

## Files

```
raw/                          # One JSON per club per season per league
  GB1/                        # Premier League
    2015/
      arsenal-fc.json
      ...
    2025/                     # Current season (in progress)
  ES1/                        # La Liga
  L1/                         # Bundesliga
  IT1/                        # Serie A
  FR1/                        # Ligue 1
  PO1/                        # Liga Portugal

cleaned/
  panel.csv                   # Aggregated: one row per club-season with injury burden
```

### Raw JSON shape

Each file contains all competitions for one club in one season:

```json
{
  "club": "Arsenal FC",
  "tmSlug": "fc-arsenal",
  "tmId": 11,
  "season": "2025",
  "league": "GB1",
  "scrapedAt": "2026-03-28T...",
  "competitions": [
    {
      "code": "GB1",
      "name": "Premier League 25/26",
      "players": [
        {
          "name": "Bukayo Saka",
          "tmId": 433177,
          "position": "RW",
          "matches": [
            { "round": "1", "status": "starting" },
            { "round": "5", "status": "injured", "detail": "Hamstring injury - Return expected on 15/10/2025" }
          ]
        }
      ]
    }
  ]
}
```

### Cleaned panel CSV

One row per club-season with pre-computed fields:

| Column | Description |
|--------|-------------|
| `league` | League code (GB1, ES1, L1, IT1, FR1, PO1) |
| `league_name` | Full league name |
| `season` | Season start year (e.g. 2025 = 2025/26) |
| `tm_id` | Transfermarkt club ID |
| `club` | Club name |
| `injury_burden` | (injured + suspended matchdays) / total in-squad matchdays |
| `injured_matchdays` | Total player-matchdays with status "injured" |
| `suspended_matchdays` | Total player-matchdays with status "suspended" |
| `national_team_matchdays` | Total player-matchdays on international duty |
| `starting_matchdays` | Total starts across all players |
| `squad_players` | Number of players who appeared in the squad at least once |
| `n_competitions` | Number of competitions the club participated in |
| `in_europe` | 1 if the club played in a European competition |

## Coverage

| League | Code | Seasons | Clubs/season |
|--------|------|---------|-------------|
| Premier League | GB1 | 2015-2025 | 20 |
| La Liga | ES1 | 2015-2025 | 20 |
| Bundesliga | L1 | 2015-2025 | 18 |
| Serie A | IT1 | 2015-2025 | 20 |
| Ligue 1 | FR1 | 2015-2025 | 18-20 |
| Liga Portugal | PO1 | 2015-2025 | 18 |

The current season (2025/26) is included and updated weekly.

## Updates

Data is refreshed automatically every Tuesday via GitHub Actions. The workflow fetches the current season for all six leagues and rebuilds the cleaned panel.

## Usage

```python
import pandas as pd

panel = pd.read_csv("cleaned/panel.csv")

# Average injury burden by league
panel.groupby("league_name")["injury_burden"].mean().sort_values()

# Clubs with the highest injury burden in 2024/25
panel[panel["season"] == "2024"].nlargest(10, "injury_burden")[["club", "league_name", "injury_burden"]]
```

## Methodology notes

- Data from Transfermarkt's "Periods of Absence" (Ausfallzeiten) pages
- The generic "absent" status is reclassified into `suspended`, `national_team`, or `other_absence` using the injury detail text
- `injury_burden` excludes national team callups from the numerator (they are not a club-level health issue)
- `not_at_club` (transferred/loaned players) is excluded from both numerator and denominator
- Transfermarkt data is crowd-sourced and may contain reporting inconsistencies, particularly in La Liga and Liga Portugal

## Related

- [Availability Is the Best Ability](https://thisismy.team/stories/availability/) — analysis using this dataset
- [salimt/football-datasets](https://github.com/salimt/football-datasets) — complementary Transfermarkt data (squad values, standings, transfers)

## License

The data is from Transfermarkt and is provided here for research and analysis purposes.
