"""Tracking for the 3D view: each converted match (tracking.py) cut into one-minute chunks the
browser streams while it plays, on the same clock and pitch orientation as the 2D replay.

    python -m matchreplay.tracking_web               # every match in data/tracking, into site/tracking/
    python -m matchreplay.tracking_web --games 3859  # some PFF games only

Needs the World Cup 2022 events (python -m matchreplay.ingest --only wc2022 --no-360), to find
the StatsBomb match each PFF game is and to put both on one clock.

Per match, site/tracking/<StatsBomb match id>/:
    meta.json          teams, kits, shirts and names, pitch size, chunk list
    <n>.bin.gz         one minute of play, every frame (29.97 per second), gzipped:
                         int32 frames, int32 time[frames], then int16 series, each one frame after
                         another: ball x, y, z and a rebuilt flag (1 where the cameras had lost the
                         ball and it was rebuilt from the events, see fill_ball), then x and y of
                         every home shirt, then every away shirt
                       time is StatsBomb event time (stream.event_time_ms), so the 3D view and the
                       2D replay's score, feed and win probability line up. Positions are centimetres
                       from the centre spot, the home team always attacking to the right (+x).
                       Every series is delta-encoded (first value as is, then frame-to-frame
                       differences, wrapping in 16 bits), which makes it compress about 3 times better.
                       MISSING (-32768) marks a player who is not on the pitch.
"""

from __future__ import annotations

import argparse
import difflib
import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

from matchreplay.paths import MATCHES, ROOT
from matchreplay.stream import PERIOD_OFFSET_MIN, event_time_ms
from matchreplay.tracking import MISSING, PFF, TRACKING

OUT = ROOT / "site" / "tracking"
PERIOD_BASE_MIN = {1: 0, 2: 45, 3: 90, 4: 105}
CHUNK_MS = 60_000


def pff_meta(game: int) -> dict:
    m = json.loads((PFF / "metadata" / f"{game}.json").read_text())
    return m[0] if isinstance(m, list) else m


def statsbomb_match(meta: dict, matches: pd.DataFrame) -> tuple[int, bool]:
    """The StatsBomb match a PFF game is (same day, most similar team names), and whether the
    two providers disagree on which team is at home."""
    day = matches[matches["date"] == meta["date"][:10]]
    h, a = meta["homeTeam"]["name"], meta["awayTeam"]["name"]
    sim = lambda x, y: difflib.SequenceMatcher(None, x.lower(), y.lower()).ratio()  # noqa: E731
    candidates = [(sim(h, r.home) + sim(a, r.away), False, r.match_id) for r in day.itertuples()]
    candidates += [(sim(h, r.away) + sim(a, r.home), True, r.match_id) for r in day.itertuples()]
    best = max(candidates)
    if best[0] < 1.0:
        raise ValueError(f"no StatsBomb match for {h} v {a} on {meta['date'][:10]}")
    return int(best[2]), best[1]


def clock_offsets(pff_events: list[dict], sb_events: pd.DataFrame) -> dict[int, int]:
    """Per period, what to add to PFF's clock to land on StatsBomb's: the median gap between the
    shots both providers recorded (each PFF shot paired with the nearest StatsBomb one)."""
    out = {}
    for p in (1, 2, 3, 4):
        sb = sb_events[(sb_events["type"] == "Shot") & (sb_events["period"] == p)]
        sb_t = np.array([event_time_ms(p, c) for c in sb["clock"]])
        pf = [e["clock_ms"] for e in pff_events if e["period"] == p and e["possession"] == "SH"]
        naive = [PERIOD_OFFSET_MIN[p] * 60000 + c - PERIOD_BASE_MIN[p] * 60000 for c in pf]
        gaps = [sb_t[np.argmin(np.abs(sb_t - t))] - t for t in naive] if len(sb_t) else []
        gaps = [g for g in gaps if abs(g) < 30_000]
        out[p] = int(np.median(gaps)) if gaps else 0
    return out


def delta(a: np.ndarray) -> np.ndarray:
    """First value, then differences, wrapping in 16 bits (decoding is a running sum, also wrapping)."""
    d = a.astype(np.int32)
    d[1:] = d[1:] - d[:-1]
    return d.astype(np.int16)


def names(game: int, team_id: str) -> dict[str, str]:
    path = PFF / "rosters" / f"{game}.json"
    if not path.exists():
        return {}
    return {r["shirtNumber"]: r["player"]["nickname"] for r in json.loads(path.read_text())
            if r["team"]["id"] == team_id}


def keepers(game: int, team_id: str) -> list[str]:
    path = PFF / "rosters" / f"{game}.json"
    if not path.exists():
        return []
    return [r["shirtNumber"] for r in json.loads(path.read_text())
            if r["team"]["id"] == team_id and r.get("positionGroupType") == "GK"]


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """[start, end) of every run of True."""
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1), strict=True))


def clean_players(arr: np.ndarray, median_frames: int = 31, mean_frames: int = 5) -> np.ndarray:
    """Remove the tracker's glitches without blurring real movement.

    A running median over one second (31 frames) drops the short bursts where an estimate jumps
    metres away and back (a median keeps a steady run or turn exactly, with no lag); a short moving
    average then takes off the remaining jitter. Each stretch a player is on the pitch is filtered
    on its own, so nothing leaks across a substitution.
    """
    from numpy.lib.stride_tricks import sliding_window_view

    out = arr.copy()
    for j in range(arr.shape[1]):
        for start, end in runs(arr[:, j, 0] != MISSING):
            if end - start < median_frames:
                continue
            for k in (0, 1):
                v = arr[start:end, j, k].astype(np.float64)
                h = median_frames // 2
                padded = np.concatenate([np.full(h, v[0]), v, np.full(h, v[-1])])
                med = np.median(sliding_window_view(padded, median_frames), axis=1)
                m = mean_frames // 2
                padded = np.concatenate([np.full(m, med[0]), med, np.full(m, med[-1])])
                out[start:end, j, k] = np.round(np.convolve(padded, np.ones(mean_frames) / mean_frames, "valid"))
    return out


def fill_ball(ball: np.ndarray, home: np.ndarray, away: np.ndarray, home_shirts: list, away_shirts: list,
              pff_events: list[dict], period: np.ndarray, clock: np.ndarray, home_team: str,
              hold_over_ms: int = 6000) -> tuple[np.ndarray, np.ndarray]:
    """The ball where the cameras lost it (replays, out of play): rebuilt from PFF's own events.

    Every on-the-ball event names the player and the frame, and we know where that player was:
    that is where the ball was. Between two known points the ball moves in a straight line; if they
    are more than `hold_over_ms` apart (a stoppage), it waits at the first and moves in the last
    1.5 seconds. Returns the ball and a flag per frame: 1 where it is rebuilt, 0 where tracked.
    """
    known = ball[:, 0] != MISSING
    ax, ay, az = ball[:, 0].astype(np.float64), ball[:, 1].astype(np.float64), ball[:, 2].astype(np.float64)
    az[az == MISSING] = 0
    anchor = known.copy()
    key = period.astype(np.int64) * 10**9 + clock
    cols = ({s: j for j, s in enumerate(home_shirts)}, {s: j for j, s in enumerate(away_shirts)})
    for e in pff_events:
        if e.get("type") != "OTB" or not e.get("shirt"):
            continue
        i = int(np.clip(np.searchsorted(key, e["period"] * 10**9 + e["clock_ms"]), 0, len(key) - 1))
        if known[max(0, i - 5):i + 6].any():
            continue                                   # the cameras had the ball here anyway
        team = 0 if e.get("team") == home_team else 1
        j = cols[team].get(str(e["shirt"]))
        arr = home if team == 0 else away
        if j is None or arr[i, j, 0] == MISSING:
            continue
        ax[i], ay[i], az[i] = arr[i, j, 0], arr[i, j, 1], 0
        anchor[i] = True
    idx = np.flatnonzero(anchor)
    if len(idx) == 0:
        return ball, np.ones(len(ball), dtype=np.int16)
    t = (period.astype(np.int64) * 10**7 + clock).astype(np.float64)  # frame times, gaps between periods kept apart
    out = ball.copy()
    for a, b in zip(idx[:-1], idx[1:], strict=True):
        if b - a <= 1:
            continue
        span = t[b] - t[a]
        f = (t[a + 1:b] - t[a]) / span
        if span > hold_over_ms:                        # a stoppage: wait, then move late
            f = np.clip((t[a + 1:b] - (t[b] - 1500)) / 1500, 0, 1)
        for k, v in enumerate((ax, ay, az)):
            out[a + 1:b, k] = np.round(v[a] + (v[b] - v[a]) * f)
    out[:idx[0]] = [ax[idx[0]], ay[idx[0]], az[idx[0]]]
    out[idx[-1] + 1:] = [ax[idx[-1]], ay[idx[-1]], az[idx[-1]]]
    for i in idx:                                      # the anchors themselves
        out[i] = [ax[i], ay[i], az[i]]
    return out, (~known).astype(np.int16)


def write_chunks(out: Path, t: np.ndarray, ball: np.ndarray, home: np.ndarray, away: np.ndarray
                 ) -> tuple[list[dict], int]:
    """One-minute chunks in the format above (ball: 4 series). Returns the chunk list and total bytes."""
    out.mkdir(parents=True, exist_ok=True)
    chunk_of = t // CHUNK_MS
    chunks, total = [], 0
    for c in np.unique(chunk_of):
        idx = np.flatnonzero(chunk_of == c)
        series = [delta(ball[idx, k]) for k in range(4)]
        for arr in (home, away):
            for j in range(arr.shape[1]):
                series += [delta(arr[idx, j, 0]), delta(arr[idx, j, 1])]
        tt = t[idx].astype(np.int32)
        payload = (np.array([len(idx)], dtype="<i4").tobytes() + tt.astype("<i4").tobytes()
                   + np.concatenate(series).astype("<i2").tobytes())
        data = gzip.compress(payload, 6)
        (out / f"{int(c)}.bin.gz").write_bytes(data)
        total += len(data)
        chunks.append({"n": int(c), "start": int(tt[0]), "end": int(tt[-1]), "frames": int(len(idx))})
    return chunks, total


def export(game: int, matches: pd.DataFrame, events: pd.DataFrame, out_root: Path = OUT) -> dict:
    meta = pff_meta(game)
    sb_id, swapped = statsbomb_match(meta, matches)
    z = np.load(TRACKING / f"{game}.npz")
    pff_events = json.loads((TRACKING / f"{game}.events.json").read_text())
    offsets = clock_offsets(pff_events, events[events["match_id"] == sb_id])

    period, clock = z["period"].astype(int), z["clock_ms"].astype(np.int64)
    base = np.array([PERIOD_BASE_MIN[p] for p in period])
    off = np.array([PERIOD_OFFSET_MIN[p] for p in period])
    shift = np.array([offsets[p] for p in period])
    t = (off * 60000 + clock - base * 60000 + shift).astype(np.int64)

    # Which way the PFF home team attacks in each frame; flip frames where it attacks left
    left_et = meta.get("homeTeamStartLeftExtraTime")
    left_et = meta["homeTeamStartLeft"] if left_et is None else left_et
    first_half = (period == 1) | (period == 3)
    home_on_left = np.where(period <= 2, first_half == bool(meta["homeTeamStartLeft"]), first_half == bool(left_et))
    hs, as_ = [str(v) for v in z["home_shirts"]], [str(v) for v in z["away_shirts"]]
    home, away = clean_players(z["home"]), clean_players(z["away"])
    ball, rebuilt = fill_ball(z["ball"], home, away, hs, as_, pff_events, period, clock, meta["homeTeam"]["name"])
    hk, ak = meta["homeTeamKit"], meta["awayTeamKit"]
    hn, an = names(game, meta["homeTeam"]["id"]), names(game, meta["awayTeam"]["id"])
    hg, ag = keepers(game, meta["homeTeam"]["id"]), keepers(game, meta["awayTeam"]["id"])
    hteam, ateam = meta["homeTeam"]["name"], meta["awayTeam"]["name"]
    if swapped:                       # StatsBomb's home team is PFF's away team
        home, away, hs, as_, hk, ak, hn, an = away, home, as_, hs, ak, hk, an, hn
        hg, ag = ag, hg
        hteam, ateam = ateam, hteam
        home_on_left = ~home_on_left
    flip = ~home_on_left
    for arr in (home, away):
        present = arr != MISSING
        arr[flip] = np.where(present[flip], -arr[flip], arr[flip])
    ball_present = ball != MISSING
    ball[flip, :2] = np.where(ball_present[flip, :2], -ball[flip, :2], ball[flip, :2])
    ball = np.concatenate([ball, rebuilt[:, None]], axis=1)       # 4th ball series: 1 = rebuilt

    order = np.argsort(t, kind="stable")
    t, home, away, ball = t[order], home[order], away[order], ball[order]
    out = out_root / str(sb_id)
    chunks, total = write_chunks(out, t, ball, home, away)

    kit = lambda k: {"primary": k.get("primaryColor"), "text": k.get("primaryTextColor"),  # noqa: E731
                     "secondary": k.get("secondaryColor")}
    pitch = meta["stadium"]["pitches"][-1] if meta.get("stadium", {}).get("pitches") else {"length": 105, "width": 68}
    (out / "meta.json").write_text(json.dumps({
        "match_id": sb_id, "pff_game": game, "home": hteam, "away": ateam,
        "home_kit": kit(hk), "away_kit": kit(ak),
        "home_shirts": [str(s) for s in hs], "away_shirts": [str(s) for s in as_],
        "home_names": {str(s): hn.get(str(s)) for s in hs}, "away_names": {str(s): an.get(str(s)) for s in as_},
        "home_keepers": hg, "away_keepers": ag,
        "pitch": {"length": pitch["length"], "width": pitch["width"]}, "fps": meta.get("fps", 29.97),
        "chunk_ms": CHUNK_MS, "ball_series": 4, "ball_rebuilt_share": round(float(rebuilt.mean()), 3),
        "chunks": chunks, "clock_offsets_ms": offsets,
        "source": "PFF FC World Cup 2022 tracking data",
    }, ensure_ascii=False, separators=(",", ":")))
    return {"game": game, "match_id": sb_id, "home": hteam, "away": ateam, "swapped": swapped, "chunks": len(chunks),
            "frames": int(len(t)), "mb": round(total / 1e6, 1), "offsets_s": {p: o / 1000 for p, o in offsets.items()}}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--games", nargs="*", type=int)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--index-only", action="store_true", help="just rebuild index.json")
    args = parser.parse_args(argv)
    if args.index_only:
        write_index(args.out)
        return
    matches = pd.read_parquet(MATCHES / "wc2022" / "matches.parquet")
    events = pd.read_parquet(MATCHES / "wc2022" / "events.parquet", columns=["match_id", "type", "period", "clock"])
    games = args.games or sorted(int(p.stem) for p in TRACKING.glob("*.npz"))
    for g in games:
        print(export(g, matches, events, args.out), flush=True)
    write_index(args.out)


def write_index(out: Path = OUT) -> None:
    """index.json: every match with tracking, from the meta.json files present (so shards built
    separately merge by simply copying their folders together)."""
    rows = []
    for meta in sorted(out.glob("*/meta.json")):
        m = json.loads(meta.read_text())
        rows.append({"match_id": m["match_id"], "home": m["home"], "away": m["away"]})
    (out / "index.json").write_text(json.dumps(sorted(rows, key=lambda r: r["match_id"]), separators=(",", ":")))


if __name__ == "__main__":
    main()
