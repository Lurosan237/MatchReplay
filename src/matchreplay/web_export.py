"""Export matches for the web view: one compact JSON per match, plus an index.

    python -m matchreplay.web_export                  # every match, into site/data/
    python -m matchreplay.web_export --only afcon2023  # one competition

Each match file holds what the 2D view replays, in columns (lists) to stay small:
    meta    teams, score, competition, stage, date, stadium
    events  the on-ball events, on one coordinate system: the home team always attacks to the
            right (StatsBomb draws every event from the acting team's point of view, so away
            events are turned around), x 0-120, y 0-80
    snaps   the live numbers every 5 seconds of match time: exactly the snapshots the Flink job
            emits, computed by the reference (stream.reference_snapshots)

The site is built from this in CI, so nothing generated is committed. StatsBomb open data.
"""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

import pandas as pd

from matchreplay.ingest import COMPETITIONS
from matchreplay.paths import MATCHES, ROOT
from matchreplay.stream import END_AFTER_MS, event_time_ms, messages, reference_snapshots

SITE_DATA = ROOT / "site" / "data"
# Events worth drawing; the rest (ball receipts, pressures, tactical shifts...) only add weight
KEEP = {"Pass", "Carry", "Shot", "Dribble", "Clearance", "Ball Recovery", "Interception", "Goal Keeper",
        "Foul Committed", "Foul Won", "Duel", "Block", "Miscontrol", "Dispossessed", "Dribbled Past",
        "Own Goal Against", "Own Goal For", "Half Start", "Half End", "Substitution", "Bad Behaviour",
        "Injury Stoppage", "Offside", "Shield", "50/50", "Error", "Referee Ball-Drop"}


def r1(v) -> float | None:
    return None if v is None or pd.isna(v) else round(float(v), 1)


def export_match(meta: dict, events: pd.DataFrame, lineups: pd.DataFrame | None = None) -> dict:
    """lineups gives the names players are known by ("Fabinho", not "Fábio Henrique Tavares") and shirt numbers."""
    home, away = meta["home"], meta["away"]
    known: dict[float, tuple[str, int | None]] = {}
    if lineups is not None:
        for r in lineups.itertuples(index=False):
            known[float(r.player_id)] = (r.player, None if pd.isna(r.number) else int(r.number))
    numbers: list[int | None] = []
    ev = events[events["type"].isin(KEEP)].sort_values("index")
    players: dict[str, int] = {}
    cols = {k: [] for k in ("t", "period", "minute", "second", "type", "side", "player", "x", "y", "ex", "ey",
                            "outcome", "xg", "card", "dur")}
    types = sorted(KEEP)
    for e in ev.itertuples(index=False):
        side = 0 if e.team == home else 1 if e.team == away else -1
        x, y, ex, ey = r1(e.x), r1(e.y), r1(e.end_x), r1(e.end_y)
        if side == 1:   # turn away events around: home always attacks to the right
            x, y = (None if x is None else round(120 - x, 1)), (None if y is None else round(80 - y, 1))
            ex, ey = (None if ex is None else round(120 - ex, 1)), (None if ey is None else round(80 - ey, 1))
        name, number = known.get(float(e.player_id), (e.player, None)) if not pd.isna(e.player_id) else (e.player, None)
        if name and name not in players:
            players[name] = len(players)
            numbers.append(number)
        cols["t"].append(event_time_ms(int(e.period), float(e.clock)))
        cols["period"].append(int(e.period))
        cols["minute"].append(int(e.minute))
        cols["second"].append(int(e.second))
        cols["type"].append(types.index(e.type))
        cols["side"].append(side)
        cols["player"].append(players.get(name, -1) if name else -1)
        cols["x"].append(x)
        cols["y"].append(y)
        cols["ex"].append(ex)
        cols["ey"].append(ey)
        cols["outcome"].append(e.outcome)
        cols["xg"].append(None if pd.isna(e.xg) else round(float(e.xg), 3))
        cols["card"].append(e.card)
        cols["dur"].append(None if pd.isna(e.duration) else round(float(e.duration), 2))
    snaps = reference_snapshots(messages(meta["match_id"]))
    keep = ("time", "minute", "period", "score", "shootout", "momentum", "possession_home")
    stats = ("shots", "on_target", "xg", "passes", "passes_completed", "box_entries", "fouls", "yellow", "red")
    s_cols = {k: [s[k] for s in snaps] for k in keep}
    for k in stats:
        s_cols["h_" + k] = [s["home_stats"][k] for s in snaps]
        s_cols["a_" + k] = [s["away_stats"][k] for s in snaps]
    return {"meta": meta, "types": types, "players": list(players), "numbers": numbers, "events": cols, "snaps": s_cols,
            "end_time": (cols["t"][-1] if cols["t"] else 0) + END_AFTER_MS}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--out", type=Path, default=SITE_DATA)
    args = parser.parse_args(argv)
    comps = [c for c in COMPETITIONS if not args.only or c.key in args.only]
    (args.out / "matches").mkdir(parents=True, exist_ok=True)
    index, total = [], 0
    for comp in comps:
        key = comp.key
        matches = pd.read_parquet(MATCHES / key / "matches.parquet")
        events = pd.read_parquet(MATCHES / key / "events.parquet")
        lineups = pd.read_parquet(MATCHES / key / "lineups.parquet")
        group = {"key": key, "name": comp.label, "women": comp.women, "matches": []}
        for m in matches.sort_values(["date", "kick_off"]).to_dict("records"):
            meta = {k: (None if isinstance(v, float) and pd.isna(v) else v) for k, v in m.items()}
            meta["competition_name"] = group["name"]
            mid = m["match_id"]
            data = export_match(meta, events[events["match_id"] == mid], lineups[lineups["match_id"] == mid])
            path = args.out / "matches" / f"{m['match_id']}.json"
            path.write_text(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
            total += len(gzip.compress(path.read_bytes()))
            final = data["snaps"]
            group["matches"].append({"id": m["match_id"], "date": m["date"], "home": m["home"], "away": m["away"],
                                     "score": [m["home_score"], m["away_score"]], "stage": m["stage"],
                                     "shootout": final["shootout"][-1] if final["time"] else [0, 0]})
        index.append(group)
        print(f"{key}: {len(group['matches'])} matches", flush=True)
    model = ROOT / "models" / "winprob.json"
    (args.out / "winprob.json").write_text(model.read_text())
    (args.out / "index.json").write_text(json.dumps(index, separators=(",", ":"), ensure_ascii=False))
    print(f"{sum(len(g['matches']) for g in index)} matches, {total / 1e6:.1f} MB gzipped")


if __name__ == "__main__":
    main()
