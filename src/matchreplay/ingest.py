"""Download StatsBomb open data and turn it into compact, typed tables.

    python -m matchreplay.ingest                 # every competition in COMPETITIONS
    python -m matchreplay.ingest --only afcon    # one of them

For each competition, writes data/matches/<key>/:
    matches.parquet   one row per match: teams, score, date, stage, stadium
    events.parquet    one row per event, in match order, with the match clock in seconds
    frames.parquet    where players stood: "360" frames (every event, some competitions) and shot
                      freeze frames (every shot, every match)
    lineups.parquet   players, numbers and positions per team

Data: Hudl StatsBomb open data (https://github.com/statsbomb/open-data). Free to use with
credit to Hudl StatsBomb as the source.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import pandas as pd
import requests

from matchreplay.paths import MATCHES, RAW

BASE = "https://raw.githubusercontent.com/statsbomb/open-data/master/data"


@dataclass(frozen=True)
class Competition:
    key: str
    name: str            # as in StatsBomb's competitions.json
    season: str
    label: str           # how we show it
    women: bool = False


COMPETITIONS = [
    Competition("afcon2023", "African Cup of Nations", "2023", "AFCON 2023"),
    Competition("wc2022", "FIFA World Cup", "2022", "World Cup 2022"),
    Competition("wc2018", "FIFA World Cup", "2018", "World Cup 2018"),
    Competition("euro2024", "UEFA Euro", "2024", "Euro 2024"),
    Competition("euro2020", "UEFA Euro", "2020", "Euro 2020"),
    Competition("copa2024", "Copa America", "2024", "Copa América 2024"),
    Competition("wwc2023", "Women's World Cup", "2023", "Women's World Cup 2023", women=True),
    Competition("wwc2019", "Women's World Cup", "2019", "Women's World Cup 2019", women=True),
    Competition("weuro2025", "UEFA Women's Euro", "2025", "Women's Euro 2025", women=True),
    Competition("weuro2022", "UEFA Women's Euro", "2022", "Women's Euro 2022", women=True),
    Competition("bundesliga2324", "1. Bundesliga", "2023/2024", "Bundesliga 2023/24"),
    Competition("ligue12223", "Ligue 1", "2022/2023", "Ligue 1 2022/23"),
]
BY_KEY = {c.key: c for c in COMPETITIONS}

SESSION = requests.Session()


def fetch(path: str, retries: int = 4):
    """A StatsBomb file, cached in data/raw. None if it doesn't exist (e.g. no 360 data)."""
    cached = RAW / path
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    for attempt in range(retries):
        r = SESSION.get(f"{BASE}/{path}", timeout=60)
        if r.status_code == 404:
            return None
        if r.ok:
            try:
                data = r.json()        # parse before caching: a cut-off download must not be kept
            except ValueError:
                time.sleep(2 ** attempt)
                continue
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(r.text, encoding="utf-8")
            return data
        time.sleep(2 ** attempt)
    r.raise_for_status()


# -- normalising -----------------------------------------------------------------------------------

def clock_seconds(timestamp: str) -> float:
    """'00:02:08.023' -> 128.023 (time since the start of the period)."""
    h, m, s = timestamp.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def _name(obj: dict | None) -> str | None:
    return obj.get("name") if obj else None


def _xy(loc) -> tuple[float | None, float | None, float | None]:
    if not loc:
        return None, None, None
    return loc[0], loc[1], loc[2] if len(loc) > 2 else None


# Type-specific details: (block key, end location key, outcome key)
DETAILS = {
    "Pass": ("pass", "end_location", "outcome"),
    "Carry": ("carry", "end_location", None),
    "Shot": ("shot", "end_location", "outcome"),
    "Dribble": ("dribble", None, "outcome"),
    "Duel": ("duel", None, "outcome"),
    "Goal Keeper": ("goalkeeper", "end_location", "outcome"),
    "Interception": ("interception", None, "outcome"),
    "Ball Receipt*": ("ball_receipt", None, "outcome"),
    "Foul Committed": ("foul_committed", None, None),
    "50/50": ("50_50", None, "outcome"),
}


def event_row(match_id: int, e: dict) -> dict:
    kind = e["type"]["name"]
    block_key, end_key, outcome_key = DETAILS.get(kind, (None, None, None))
    block = e.get(block_key, {}) if block_key else {}
    x, y, _ = _xy(e.get("location"))
    ex, ey, ez = _xy(block.get(end_key)) if end_key else (None, None, None)
    outcome = _name(block.get(outcome_key)) if outcome_key else None
    card = _name(e.get("foul_committed", {}).get("card")) or _name(e.get("bad_behaviour", {}).get("card"))
    # Everything type-specific we don't flatten stays available, as JSON (minus the freeze frame)
    extra = {k: v for k, v in block.items() if k not in ("freeze_frame",)} if block else {}
    return {
        "match_id": match_id,
        "event_id": e["id"],
        "index": e["index"],
        "period": e["period"],
        "clock": clock_seconds(e["timestamp"]),
        "minute": e["minute"],
        "second": e["second"],
        "type": kind,
        "team": _name(e.get("team")),
        "player": _name(e.get("player")),
        "player_id": (e.get("player") or {}).get("id"),
        "position": _name(e.get("position")),
        "possession": e.get("possession"),
        "possession_team": _name(e.get("possession_team")),
        "play_pattern": _name(e.get("play_pattern")),
        "x": x, "y": y,
        "end_x": ex, "end_y": ey, "end_z": ez,
        "outcome": outcome,
        "duration": e.get("duration"),
        "under_pressure": bool(e.get("under_pressure", False)),
        "xg": block.get("statsbomb_xg") if kind == "Shot" else None,
        "shot_type": _name(block.get("type")) if kind == "Shot" else None,
        "card": card,
        "extra": json.dumps(extra, separators=(",", ":")) if extra else None,
    }


def frame_rows(match_id: int, frames: list[dict]) -> list[dict]:
    """360 data: every visible player at (almost) every event, for the matches that have it."""
    rows = []
    for f in frames:
        for p in f.get("freeze_frame", []):
            x, y, _ = _xy(p.get("location"))
            rows.append({"match_id": match_id, "event_id": f["event_uuid"], "source": "360", "x": x, "y": y,
                         "teammate": p.get("teammate"), "actor": p.get("actor"), "keeper": p.get("keeper"),
                         "player_id": None})
    return rows


def shot_frame_rows(match_id: int, events: list[dict]) -> list[dict]:
    """Every shot in every match carries its own freeze frame (players around the ball, named)."""
    rows = []
    for e in events:
        shot = e.get("shot")
        if not shot:
            continue
        x, y, _ = _xy(e.get("location"))
        rows.append({"match_id": match_id, "event_id": e["id"], "source": "shot", "x": x, "y": y,
                     "teammate": True, "actor": True, "keeper": False, "player_id": (e.get("player") or {}).get("id")})
        for p in shot.get("freeze_frame", []):
            px, py, _ = _xy(p.get("location"))
            rows.append({"match_id": match_id, "event_id": e["id"], "source": "shot", "x": px, "y": py,
                         "teammate": p.get("teammate"), "actor": False,
                         "keeper": _name(p.get("position")) == "Goalkeeper",
                         "player_id": (p.get("player") or {}).get("id")})
    return rows


def match_row(comp: Competition, m: dict) -> dict:
    return {
        "match_id": m["match_id"], "competition": comp.key, "label": comp.label, "women": comp.women,
        "date": m["match_date"], "kick_off": m.get("kick_off"),
        "home": m["home_team"]["home_team_name"], "away": m["away_team"]["away_team_name"],
        "home_score": m["home_score"], "away_score": m["away_score"],
        "stage": _name(m.get("competition_stage")), "stadium": _name(m.get("stadium")),
        "home_manager": (m["home_team"].get("managers") or [{}])[0].get("name"),
        "away_manager": (m["away_team"].get("managers") or [{}])[0].get("name"),
    }


def lineup_rows(match_id: int, lineups: list[dict]) -> list[dict]:
    rows = []
    for team in lineups or []:
        for p in team["lineup"]:
            starts = [pos for pos in p.get("positions", []) if pos.get("start_reason") == "Starting XI"]
            rows.append({"match_id": match_id, "team": team["team_name"], "player_id": p["player_id"],
                         "player": p.get("player_nickname") or p["player_name"], "number": p.get("jersey_number"),
                         "country": _name(p.get("country")), "starter": bool(starts),
                         "position": starts[0]["position"] if starts else None})
    return rows


# -- one competition -------------------------------------------------------------------------------

def competition_ids(comp: Competition, catalogue: list[dict]) -> tuple[int, int]:
    for c in catalogue:
        if c["competition_name"] == comp.name and c["season_name"] == comp.season:
            return c["competition_id"], c["season_id"]
    raise KeyError(f"{comp.name} {comp.season} is not in StatsBomb's open data")


def ingest(comp: Competition, catalogue: list[dict], workers: int = 8, with_360: bool = True) -> dict:
    cid, sid = competition_ids(comp, catalogue)
    matches = fetch(f"matches/{cid}/{sid}.json")

    def one(m):
        mid = m["match_id"]
        events = fetch(f"events/{mid}.json")
        frames = fetch(f"three-sixty/{mid}.json") if with_360 else None
        lineups = fetch(f"lineups/{mid}.json")
        return mid, events, frames or [], lineups

    events, frames, lineups = [], [], []
    with ThreadPoolExecutor(workers) as pool:
        for mid, ev, fr, lu in pool.map(one, matches):
            events.extend(event_row(mid, e) for e in ev)
            frames.extend(frame_rows(mid, fr))
            frames.extend(shot_frame_rows(mid, ev))
            lineups.extend(lineup_rows(mid, lu))
    out = MATCHES / comp.key
    out.mkdir(parents=True, exist_ok=True)
    tables = {
        "matches": pd.DataFrame([match_row(comp, m) for m in matches]).sort_values(["date", "kick_off"]),
        "events": pd.DataFrame(events).sort_values(["match_id", "index"]),
        "frames": pd.DataFrame(frames, columns=["match_id", "event_id", "source", "x", "y", "teammate", "actor",
                                                "keeper", "player_id"]).astype({"player_id": "Int64"}),
        "lineups": pd.DataFrame(lineups),
    }
    for name, df in tables.items():
        df.to_parquet(out / f"{name}.parquet", index=False, compression="zstd")
    return {"competition": comp.label, "matches": len(matches), "events": len(events),
            "matches_with_360": int(tables["frames"].query("source == '360'")["match_id"].nunique())}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*", choices=list(BY_KEY), help="competition keys (default: all)")
    parser.add_argument("--no-360", action="store_true", help="skip the 360 freeze frames (the biggest download)")
    args = parser.parse_args(argv)
    catalogue = fetch("competitions.json")
    for key in args.only or list(BY_KEY):
        started = time.perf_counter()
        summary = ingest(BY_KEY[key], catalogue, with_360=not args.no_360)
        print(f"{summary['competition']:<26} {summary['matches']:>3} matches  {summary['events']:>8,} events  "
              f"{summary['matches_with_360']:>3} with 360 frames  ({time.perf_counter() - started:.0f}s)")


if __name__ == "__main__":
    main()
