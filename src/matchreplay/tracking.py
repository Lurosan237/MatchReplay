"""PFF FC World Cup 2022 tracking data: every player and the ball, 30 times a second, for all 64
matches. Turned into one compact file per match for the 2D and 3D views.

    python -m matchreplay.tracking data/raw/pff/3859.jsonl.bz2 ...   # convert some matches
    python -m matchreplay.tracking --all                              # every file in data/raw/pff

The raw data is free but not ours to redistribute: request it from PFF FC
(https://www.blog.fc.pff.com/blog/pff-fc-release-2022-world-cup-data) and put the Metadata,
Rosters and Tracking Data files in data/raw/pff/. Nothing from it is committed.

Output, data/tracking/<game>.npz:
    clock_ms (F,)        game clock in ms (second half starts at 2 700 000, extra time at 5 400 000)
    period   (F,)        1, 2, 3, 4 (extra time); penalty shoot-out frames are dropped
    home, away (F, J, 2) x, y in centimetres per shirt number (J = every shirt that appears);
                         MISSING when the player is not on the pitch
    home_shirts, away_shirts (J,)
    ball     (F, 3)      x, y, z in centimetres
Coordinates: pitch centre is (0, 0), x along the length, y across, in metres x 100. Teams swap
ends at half time exactly as on the day; metadata says who started on the left.
Every frame is kept, all 29.97 per second: no downsampling.

Plus data/tracking/<game>.events.json: PFF's own event stream, already synchronised to the
frames (each event carries the clock of the frame it happened on).
"""

from __future__ import annotations

import argparse
import bz2
import json
import sys
from pathlib import Path

import numpy as np

from matchreplay.paths import DATA, RAW

PFF = RAW / "pff"
TRACKING = DATA / "tracking"
MISSING = np.iinfo(np.int16).min
KEEP_EVERY = 1


def cm(v: float | None) -> int:
    return MISSING if v is None else int(round(v * 100))


def team(players: list[dict] | None) -> dict[str, tuple[int, int]]:
    return {p["jerseyNum"]: (cm(p["x"]), cm(p["y"])) for p in players or [] if p.get("x") is not None}


def event_row(clock_ms: int, period: int, ge: dict, pe: dict | None) -> dict:
    return {"clock_ms": clock_ms, "period": period, "id": ge.get("game_event_id") or ge.get("id"),
            "type": ge.get("game_event_type"), "possession": (pe or {}).get("possession_event_type"),
            "player": ge.get("player_name"), "shirt": ge.get("shirt_number"), "team": ge.get("team_name"),
            "clock": ge.get("formatted_game_clock")}


def convert(path: Path, out_dir: Path = TRACKING) -> dict:
    game = int(path.name.split(".")[0])
    clock, period, home, away, ball, events = [], [], [], [], [], []
    last_event = None
    with bz2.open(path, "rt") as f:
        for i, line in enumerate(f):
            if i % KEEP_EVERY and '"game_event": null' in line:
                continue                               # a frame we don't keep, with no event on it
            d = json.loads(line)
            p = d.get("period")
            if p not in (1, 2, 3, 4) or d.get("periodGameClockTime") is None:
                continue
            ms = int(round(d["periodGameClockTime"] * 1000))
            ge = d.get("game_event")
            if ge and d.get("game_event_id") != last_event:
                last_event = d.get("game_event_id")
                events.append(event_row(ms, p, ge | {"game_event_id": last_event}, d.get("possession_event")))
            if i % KEEP_EVERY:
                continue
            b = d.get("ballsSmoothed") or {}
            clock.append(ms)
            period.append(p)
            home.append(team(d.get("homePlayersSmoothed") or d.get("homePlayers")))
            away.append(team(d.get("awayPlayersSmoothed") or d.get("awayPlayers")))
            ball.append((cm(b.get("x")), cm(b.get("y")), cm(b.get("z"))))

    def grid(frames: list[dict]) -> tuple[np.ndarray, np.ndarray]:
        shirts = sorted({s for fr in frames for s in fr}, key=lambda s: int(s) if s.isdigit() else 999)
        col = {s: j for j, s in enumerate(shirts)}
        arr = np.full((len(frames), len(shirts), 2), MISSING, dtype=np.int16)
        for i, fr in enumerate(frames):
            for s, xy in fr.items():
                arr[i, col[s]] = xy
        return arr, np.array(shirts)

    h, hs = grid(home)
    a, as_ = grid(away)
    out_dir.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(out_dir / f"{game}.npz", clock_ms=np.array(clock, dtype=np.int32),
                        period=np.array(period, dtype=np.int8), home=h, away=a, home_shirts=hs,
                        away_shirts=as_, ball=np.array(ball, dtype=np.int16))
    (out_dir / f"{game}.events.json").write_text(json.dumps(events, separators=(",", ":")))
    return {"game": game, "frames": len(clock), "events": len(events), "home_shirts": len(hs),
            "away_shirts": len(as_), "minutes": round(clock[-1] / 60000) if clock else 0}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--delete-raw", action="store_true", help="remove each raw file once converted")
    args = parser.parse_args(argv)
    files = sorted(PFF.glob("*.jsonl.bz2")) if args.all else args.files
    if not files:
        sys.exit("no tracking files: see the module docstring")
    for f in files:
        print(convert(f), flush=True)
        if args.delete_raw:
            f.unlink()


if __name__ == "__main__":
    main()
