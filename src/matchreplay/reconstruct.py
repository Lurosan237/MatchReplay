"""Where were the players? Reconstructed movement for matches without tracking data.

Event data records where the ball was and who had it; StatsBomb's "360" frames add, at most events,
where the other visible players stood, without saying who they are. From that, every player's path
through the match is rebuilt in three steps:

1. A formation model, learned from real tracking (PFF's World Cup 2022): for each role (left back,
   defensive midfielder, ...), where a player stands given where the ball is and which team has it.
   This alone gives a plausible team shape that shifts and breathes with the ball.
2. Observations: the player on the ball at each event (named), players in shot freeze frames
   (named), and the anonymous players of the 360 frames, matched to names by solving an assignment
   problem (Hungarian algorithm) against where each player is expected to be.
   Players are first linked into short trails across consecutive frames; a trail passing through a
   named moment keeps that name. Matching and position estimates are refined over a few rounds.
3. Fusion: each player's path is the formation model, shifted with their team's observed block,
   then corrected towards that player's own sightings; a correction fades with time from the
   moment it was seen (Gaussian kernel), so a player drifts back towards their role when unseen.

The result is scored on World Cup 2022 matches that the formation model never saw, against the
real tracking: python -m matchreplay.reconstruct --evaluate (reports/reconstruction.md).

    python -m matchreplay.reconstruct --fit                 # learn the formation model
    python -m matchreplay.reconstruct --evaluate            # score it against real tracking
    python -m matchreplay.reconstruct --export afcon2023    # 3D files for the site

Coordinates: metres from the centre spot, x along the pitch with the home team attacking +x, y
across, as in the tracking files (tracking_web), so the 3D view reads both alike.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

from matchreplay.paths import MATCHES, REPORTS, ROOT
from matchreplay.stream import event_time_ms

MODEL_PATH = ROOT / "models" / "formation.json"
OUT = ROOT / "site" / "reconstructed"
STEP_MS = 200                       # 5 positions per second while reconstructing and scoring
EXPORT_EVERY = 2                    # every other one for the site: the 3D view interpolates between
SIGMA_S = 4.0                       # how long an observation keeps pulling a player (Gaussian, seconds)
PRIOR_WEIGHT = 0.35                 # how strongly a player returns to the formation when unseen
ASSIGN_MAX_M = 14.0                 # a 360 point is matched to a player only if this close to expected
MAX_SPEED = 8.5                     # m/s, no reconstructed player runs faster
TEAM_SIGMA_S = 3.0                  # the team block correction is smoothed over this (seconds)
TEAM_PRIOR_WEIGHT = 8.0             # and fades to the plain formation model when no one is seen
ROUNDS = 3                          # name the 360 trails, rebuild positions, name again
L, W = 105.0, 68.0

# Where each role stands, team attacking +x, y > 0 on the team's left (metres). Starting values for
# roles the formation model has no data for; the model replaces them with what it learned.
ROLE_BASE = {
    "Goalkeeper": (-47, 0),
    "Right Back": (-25, -24), "Right Center Back": (-33, -9), "Center Back": (-34, 0),
    "Left Center Back": (-33, 9), "Left Back": (-25, 24),
    "Right Wing Back": (-15, -27), "Left Wing Back": (-15, 27),
    "Right Defensive Midfield": (-18, -8), "Center Defensive Midfield": (-20, 0),
    "Left Defensive Midfield": (-18, 8),
    "Right Midfield": (-5, -24), "Right Center Midfield": (-8, -10), "Center Midfield": (-8, 0),
    "Left Center Midfield": (-8, 10), "Left Midfield": (-5, 24),
    "Right Attacking Midfield": (5, -14), "Center Attacking Midfield": (3, 0), "Left Attacking Midfield": (5, 14),
    "Right Wing": (10, -24), "Left Wing": (10, 24),
    "Right Center Forward": (12, -7), "Center Forward": (14, 0), "Left Center Forward": (12, 7),
    "Secondary Striker": (8, 0),
}


def to_metres(x, y):
    """StatsBomb yards (acting team attacking right) -> metres, same team frame."""
    return (np.asarray(x, float) - 60) * L / 120, -(np.asarray(y, float) - 40) * W / 80


# -- one match, as the reconstruction sees it --------------------------------------------------------

@dataclass
class Player:
    pid: float
    team: int                         # 0 home, 1 away
    name: str
    shirt: str
    on: int                           # event time (ms) the player comes on
    off: int                          # and goes off
    role_t: np.ndarray = field(default_factory=lambda: np.zeros(0, np.int64))
    role: list = field(default_factory=list)

    def role_at(self, t: np.ndarray) -> np.ndarray:
        i = np.clip(np.searchsorted(self.role_t, t, side="right") - 1, 0, len(self.role) - 1)
        return np.array(self.role, dtype=object)[i]


@dataclass
class MatchData:
    match_id: int
    home: str
    away: str
    events: pd.DataFrame              # with t (event time ms) and home-frame metres X, Y, EX, EY
    players: list
    frames: pd.DataFrame              # 360 and shot freeze frames, home-frame metres
    grid: np.ndarray                  # event time of every reconstructed position


def load(match_id: int, competition: str) -> MatchData:
    folder = MATCHES / competition
    m = pd.read_parquet(folder / "matches.parquet")
    meta = m[m["match_id"] == match_id].iloc[0]
    home, away = meta["home"], meta["away"]
    ev = pd.read_parquet(folder / "events.parquet", filters=[("match_id", "==", match_id)]).sort_values("index")
    ev = ev[ev["period"] <= 4].copy()
    ev["t"] = [event_time_ms(int(p), float(c)) for p, c in zip(ev["period"], ev["clock"], strict=True)]
    ev["side"] = np.where(ev["team"] == home, 0, np.where(ev["team"] == away, 1, -1))
    flip = np.where(ev["side"] == 1, -1.0, 1.0)
    for a, b, xa, ya in (("x", "y", "X", "Y"), ("end_x", "end_y", "EX", "EY")):
        X, Y = to_metres(ev[a], ev[b])
        ev[xa], ev[ya] = X * flip, Y * flip

    lineups = pd.read_parquet(folder / "lineups.parquet", filters=[("match_id", "==", match_id)])
    players = roster(ev, lineups, home)

    frames = pd.DataFrame()
    fpath = folder / "frames.parquet"
    if fpath.exists():
        fr = pd.read_parquet(fpath, filters=[("match_id", "==", match_id)])
        if len(fr):
            info = ev.set_index("event_id")[["t", "side"]]
            fr = fr.join(info, on="event_id", how="inner")
            X, Y = to_metres(fr["x"], fr["y"])
            flip = np.where(fr["side"] == 1, -1.0, 1.0)      # frames are drawn from the event team's side
            fr["X"], fr["Y"] = X * flip, Y * flip
            fr["team"] = np.where(fr["teammate"], fr["side"], 1 - fr["side"])
            frames = fr[fr["side"] >= 0]

    grid = []
    for _, g in ev.groupby("period"):
        grid.append(np.arange(g["t"].min(), g["t"].max() + STEP_MS, STEP_MS))
    return MatchData(match_id, home, away, ev, players, frames, np.concatenate(grid).astype(np.int64))


def roster(ev: pd.DataFrame, lineups: pd.DataFrame, home: str) -> list[Player]:
    """Who was on the pitch when: starters, substitutes (paired with the substitution events in
    order, since the data does not say who came on), and red cards."""
    start, end = int(ev["t"].min()), int(ev["t"].max()) + 1
    by_player = ev[ev["player_id"].notna()].groupby("player_id")
    first = by_player["t"].min()
    out = []
    for side in (0, 1):
        lu = lineups[(lineups["team"] == home) if side == 0 else (lineups["team"] != home)]
        subs_out = ev[(ev["type"] == "Substitution") & (ev["side"] == side)].sort_values("t")
        bench = lu[~lu["starter"]].copy()
        bench["first"] = [first.get(p, np.nan) for p in bench["player_id"]]
        coming = bench[bench["first"].notna()].sort_values("first")
        on_time = dict(zip(coming["player_id"], list(subs_out["t"]) + [None] * len(coming), strict=False))
        off_time = {p: t for p, t in zip(subs_out["player_id"], subs_out["t"], strict=True)}
        reds = ev[(ev["side"] == side) & ev["card"].isin(["Red Card", "Second Yellow"])]
        for p, t in zip(reds["player_id"], reds["t"], strict=True):
            off_time[p] = min(off_time.get(p, t), t)
        for r in lu.itertuples():
            if not r.starter and r.player_id not in on_time:
                continue                                   # never played
            on = start if r.starter else int(on_time[r.player_id] if on_time[r.player_id] is not None
                                              else first[r.player_id] - 30_000)
            pl = Player(float(r.player_id), side, r.player, "" if pd.isna(r.number) else str(int(r.number)),
                        on, int(off_time.get(r.player_id, end)))
            mine = ev[(ev["player_id"] == r.player_id) & ev["position"].notna() & (ev["position"] != "Substitute")]
            lineup_role = r.position if isinstance(r.position, str) and r.position in ROLE_BASE else None
            roles = list(mine["position"])
            times = list(mine["t"])
            if not roles:
                roles, times = [lineup_role or "Center Midfield"], [on]
            pl.role_t, pl.role = np.array(times, np.int64), roles
            out.append(pl)
    return out


# -- the ball and the game state, from the events ----------------------------------------------------

def ball_path(ev: pd.DataFrame, t: np.ndarray) -> np.ndarray:
    """(len(t), 2): the ball between recorded actions, held still through stoppages."""
    e = ev[ev["X"].notna()].sort_values(["t", "index"])
    kt, kx, ky = [], [], []
    for r in e.itertuples():
        kt.append(r.t)
        kx.append(r.X)
        ky.append(r.Y)
        if not np.isnan(r.EX):
            kt.append(r.t + max(250, min((r.duration or 0.4) * 1000, 6000)))
            kx.append(r.EX)
            ky.append(r.EY)
    kt = np.maximum.accumulate(np.array(kt, float))
    return np.stack([np.interp(t, kt, kx), np.interp(t, kt, ky)], 1)


def possession(ev: pd.DataFrame, home: str, t: np.ndarray) -> np.ndarray:
    """1 where the home team has the ball, 0 where the away team has it."""
    e = ev.sort_values(["t", "index"])
    i = np.clip(np.searchsorted(e["t"].to_numpy(), t, side="right") - 1, 0, len(e) - 1)
    return (e["possession_team"].to_numpy()[i] == home).astype(float)


# -- the formation model ----------------------------------------------------------------------------

def features(ball_team: np.ndarray, has_ball: np.ndarray) -> np.ndarray:
    """Per row: 1, ball x, ball y, in possession, and how the line moves with the ball in or out of
    possession (team frame: the team attacks +x)."""
    bx, by = ball_team[:, 0] / 50, ball_team[:, 1] / 34
    return np.stack([np.ones_like(bx), bx, by, has_ball, bx * has_ball, by * has_ball, bx * bx, by * by], 1)


class FormationModel:
    def __init__(self, coef: dict | None = None):
        self.coef = coef or {}

    def predict(self, roles: np.ndarray, ball_team: np.ndarray, has_ball: np.ndarray) -> np.ndarray:
        X = features(ball_team, has_ball)
        out = np.zeros((len(roles), 2))
        for role in np.unique(roles):
            m = roles == role
            if role in self.coef:
                c = np.array(self.coef[role])
                out[m] = X[m] @ c
            else:                                          # unseen role: its base spot, shifted with the ball
                bx, by = ROLE_BASE.get(role, (0, 0))
                out[m, 0] = bx * 0.75 + ball_team[m, 0] * 0.45
                out[m, 1] = by * 0.8 + ball_team[m, 1] * 0.25
        return out

    @staticmethod
    def fit(samples: dict[str, tuple[list, list]], ridge: float = 1.0) -> FormationModel:
        coef = {}
        for role, (Xs, Ys) in samples.items():
            X, Y = np.concatenate(Xs), np.concatenate(Ys)
            if len(X) < 500:
                continue
            A = X.T @ X + ridge * np.eye(X.shape[1])
            coef[role] = np.linalg.solve(A, X.T @ Y).tolist()
        return FormationModel(coef)

    def save(self, path: Path = MODEL_PATH) -> None:
        path.write_text(json.dumps({"features": ["1", "ball_x/50", "ball_y/34", "has_ball", "ball_x*has_ball",
                                                 "ball_y*has_ball", "(ball_x/50)^2", "(ball_y/34)^2"],
                                    "coef": {k: [[round(v, 4) for v in row] for row in c]
                                             for k, c in sorted(self.coef.items())}}, indent=1))

    @staticmethod
    def load(path: Path = MODEL_PATH) -> FormationModel:
        return FormationModel(json.loads(path.read_text())["coef"]) if path.exists() else FormationModel()


def team_frame(xy: np.ndarray, team: int) -> np.ndarray:
    return xy if team == 0 else -xy


# -- the reconstruction ----------------------------------------------------------------------------

def smooth(values: np.ndarray, weights: np.ndarray, sigma_steps: float) -> tuple[np.ndarray, np.ndarray]:
    """Gaussian-weighted sums of observations on the grid (convolution)."""
    half = int(4 * sigma_steps)
    k = np.exp(-0.5 * (np.arange(-half, half + 1) / sigma_steps) ** 2)
    return np.convolve(values, k, "same"), np.convolve(weights, k, "same")


def reconstruct(md: MatchData, model: FormationModel, use_360: bool = True, use_events: bool = True,
                sigma_s: float = SIGMA_S, prior_weight: float = PRIOR_WEIGHT) -> dict:
    """Every player's path on md.grid: {"pos": (T, P, 2) with NaN off the pitch, "players": [...],
    "ball": (T, 2), "prior": (T, P, 2)}."""
    t = md.grid
    ball = ball_path(md.events, t)
    home_has = possession(md.events, md.home, t)
    P = len(md.players)
    prior = np.full((len(t), P, 2), np.nan)
    on = np.zeros((len(t), P), bool)
    for j, p in enumerate(md.players):
        on[:, j] = (t >= p.on) & (t < p.off)
        has = home_has if p.team == 0 else 1 - home_has
        pred = model.predict(p.role_at(t), team_frame(ball, p.team), has)
        prior[:, j] = team_frame(pred, p.team)

    idx = {p.pid: j for j, p in enumerate(md.players)}
    step = lambda times: np.clip(np.searchsorted(t, times), 0, len(t) - 1)  # noqa: E731
    sigma = sigma_s * 1000 / STEP_MS

    teams = np.array([p.team for p in md.players])

    def fuse(obs: list[tuple[int, int, float, float, float]]) -> np.ndarray:
        """obs: (grid index, player index, X, Y, weight) -> positions.

        Two layers of correction on the formation model: first the whole team's block (where the
        observed players of a team stand compared with their formation spots, averaged and smoothed:
        how high the line is, how far it has shifted), which moves the unseen players too; then each
        player's own sightings."""
        S = np.zeros((len(t), P, 2))
        Wt = np.zeros((len(t), P))
        for i, j, x, y, w in obs:
            S[i, j, 0] += w * (x - prior[i, j, 0])
            S[i, j, 1] += w * (y - prior[i, j, 1])
            Wt[i, j] += w
        base = prior.copy()
        for team in (0, 1):
            m = teams == team
            ts, tw = S[:, m].sum(1), Wt[:, m].sum(1)
            sx, wsum = smooth(ts[:, 0], tw, TEAM_SIGMA_S * 1000 / STEP_MS)
            sy, _ = smooth(ts[:, 1], tw, TEAM_SIGMA_S * 1000 / STEP_MS)
            shift = np.stack([sx, sy], 1) / (wsum + TEAM_PRIOR_WEIGHT)[:, None]
            base[:, m] += shift[:, None, :]
        pos = base.copy()
        for j in range(P):
            if Wt[:, j].sum() == 0:
                continue
            rx = np.where(Wt[:, j] > 0, S[:, j, 0] - Wt[:, j] * (base[:, j, 0] - prior[:, j, 0]), 0)
            ry = np.where(Wt[:, j] > 0, S[:, j, 1] - Wt[:, j] * (base[:, j, 1] - prior[:, j, 1]), 0)
            sx, wsum = smooth(rx, Wt[:, j], sigma)
            sy, _ = smooth(ry, Wt[:, j], sigma)
            pos[:, j, 0] += sx / (wsum + prior_weight)
            pos[:, j, 1] += sy / (wsum + prior_weight)
        return pos

    obs = []
    if use_events:
        e = md.events[md.events["player_id"].notna() & md.events["X"].notna()]
        for i, pid, x, y in zip(step(e["t"].to_numpy()), e["player_id"], e["X"], e["Y"], strict=True):
            if pid in idx:
                obs.append((i, idx[pid], x, y, 1.0))
        if len(md.frames):
            named = md.frames[(md.frames["source"] == "shot") & md.frames["player_id"].notna() & ~md.frames["actor"]]
            for i, pid, x, y in zip(step(named["t"].to_numpy()), named["player_id"], named["X"], named["Y"],
                                    strict=True):
                if float(pid) in idx:
                    obs.append((i, idx[float(pid)], x, y, 1.0))
    pos = fuse(obs) if obs else prior.copy()

    if use_360 and len(md.frames):
        trails = trails_360(md, idx)
        for _ in range(ROUNDS):                            # better positions -> better names -> ...
            pos = fuse(obs + name_trails(md, trails, pos, on))

    pos = limit_speed(np.clip(pos, [-L / 2 - 2, -W / 2 - 2], [L / 2 + 2, W / 2 + 2]))
    pos[~on] = np.nan
    return {"pos": pos, "prior": prior, "ball": ball, "players": md.players, "t": t}


def trails_360(md: MatchData, idx: dict) -> list[dict]:
    """Link the anonymous players of the 360 frames into trails, one unknown player each.

    Within each team, every point is linked to the nearest point of the team's next frame (usually
    under a second later) when it is close enough to be the same player (Hungarian algorithm, gated by
    how far a player can run). The named player on the ball and the flagged goalkeepers are put back
    into their frames, so a trail passing through them carries a name. Scored against real tracking,
    about 80 % of a trail's points belong to the same real player.
    """
    keepers = {p.team: j for j, p in enumerate(md.players) if p.role and p.role[0] == "Goalkeeper"}
    ev = md.events.drop_duplicates("event_id").set_index("event_id")
    f = md.frames[md.frames["source"] == "360"]
    trails: list[dict] = []
    for team in (0, 1):
        frames = []
        for eid, g in f[f["team"] == team].groupby("event_id"):
            r = ev.loc[eid]
            others = g[~g["actor"]]
            pts = others[["X", "Y"]].to_numpy()
            names = np.where(others["keeper"].to_numpy(), keepers.get(team, -1), -1)
            if r["side"] == team and r["player_id"] in idx and not np.isnan(r["X"]):
                pts = np.vstack([pts, [[r["X"], r["Y"]]]])
                names = np.append(names, idx[r["player_id"]])
            frames.append((int(r["t"]), pts, names))
        frames.sort(key=lambda fr: fr[0])
        mine: list[dict] = []
        active: list[int] = []
        for tt, pts, names in frames:
            active = [k for k in active if tt - mine[k]["t"][-1] <= 3000]
            linked = np.zeros(len(pts), bool)
            if active and len(pts):
                last = np.array([mine[k]["xy"][-1] for k in active])
                dt = np.array([(tt - mine[k]["t"][-1]) / 1000 for k in active])
                d = np.hypot(pts[:, None, 0] - last[None, :, 0], pts[:, None, 1] - last[None, :, 1])
                gate = 2.0 + 7.0 * dt[None, :]
                rows, cols = linear_sum_assignment(np.where(d < gate, d, 1e6))
                for q, c in zip(rows, cols, strict=True):
                    if d[q, c] < gate[0, c]:
                        tr = mine[active[c]]
                        tr["t"].append(tt)
                        tr["xy"].append(pts[q])
                        tr["names"].append(int(names[q]))
                        linked[q] = True
            for q in np.flatnonzero(~linked):
                mine.append({"team": team, "t": [tt], "xy": [pts[q]], "names": [int(names[q])]})
                active.append(len(mine) - 1)
        trails += mine
    for tr in trails:
        tr["t"], tr["xy"] = np.array(tr["t"]), np.array(tr["xy"])
        labels = [n for n in tr["names"] if n >= 0]
        best = max(set(labels), key=labels.count) if labels else -1
        tr["name"] = best if labels and labels.count(best) * 2 > len(labels) else -1
    return trails


def name_trails(md: MatchData, trails: list[dict], reference: np.ndarray, on: np.ndarray) -> list:
    """Observations from the 360 frames. Trails that pass through a named moment keep that name for
    all their points. Every other point is matched frame by frame (Hungarian algorithm, minimum total
    distance) to the players expected nearby (`reference`), goalkeepers to goalkeepers. Tested on
    real tracking, this beat naming whole trails by their average distance."""
    t = md.grid
    teams = np.array([p.team for p in md.players])
    keepers = {p.team: j for j, p in enumerate(md.players) if p.role and p.role[0] == "Goalkeeper"}
    out, taken = [], set()
    for tr in trails:
        if tr["name"] < 0:
            continue
        ii = np.clip(np.searchsorted(t, tr["t"]), 0, len(t) - 1)
        for i, xy, n in zip(ii, tr["xy"], tr["names"], strict=True):
            taken.add((int(i), round(float(xy[0]), 3), round(float(xy[1]), 3)))
            if n < 0:                                      # named points are observations already
                out.append((int(i), tr["name"], xy[0], xy[1], 0.7))
    f = md.frames[(md.frames["source"] == "360") & ~md.frames["actor"]]
    for (_, team), g in f.groupby(["event_id", "team"]):
        i = int(np.clip(np.searchsorted(t, g["t"].iloc[0]), 0, len(t) - 1))
        pts, gk = g[["X", "Y"]].to_numpy(), g["keeper"].to_numpy()
        free = np.array([(i, round(float(x), 3), round(float(y), 3)) not in taken for x, y in pts])
        if (gk & free).any() and team in keepers:
            out.append((i, keepers[team], *pts[gk & free][0], 0.7))
        pts = pts[~gk & free]
        cand = [j for j in np.flatnonzero((teams == team) & on[i]) if j != keepers.get(team)]
        if not len(pts) or not cand:
            continue
        cost = np.hypot(pts[:, None, 0] - reference[i, cand, 0], pts[:, None, 1] - reference[i, cand, 1])
        for q, c in zip(*linear_sum_assignment(cost), strict=True):
            if cost[q, c] < ASSIGN_MAX_M:
                out.append((i, cand[c], pts[q, 0], pts[q, 1], 0.7))
    return out


def limit_speed(pos: np.ndarray, vmax: float = MAX_SPEED) -> np.ndarray:
    """No reconstructed player moves faster than vmax: steps beyond it are shortened."""
    out = pos.copy()
    cap = vmax * STEP_MS / 1000
    for i in range(1, len(out)):
        d = out[i] - out[i - 1]
        n = np.hypot(d[:, 0], d[:, 1])
        too = n > cap
        if too.any():
            out[i, too] = out[i - 1, too] + d[too] * (cap / n[too])[:, None]
    return out


# -- learning and scoring against real tracking -----------------------------------------------------

def tracking_truth(md: MatchData, root: Path) -> tuple[np.ndarray, list[int]] | None:
    """Real positions (T, P, 2) on md.grid from the tracking files, matched by team and shirt."""
    from matchreplay.tracking_truth import load_tracking

    got = load_tracking(md.match_id, root)
    if got is None:
        return None
    meta, tt, _, home, away = got
    i = np.clip(np.searchsorted(tt, md.grid), 0, len(tt) - 1)
    close = np.abs(tt[i] - md.grid) < 100
    truth = np.full((len(md.grid), len(md.players), 2), np.nan)
    for j, p in enumerate(md.players):
        shirts = meta["home_shirts"] if p.team == 0 else meta["away_shirts"]
        if p.shirt in shirts:
            arr = home if p.team == 0 else away
            truth[close, j] = arr[i[close], shirts.index(p.shirt)]
    return truth, close


def fit(matches: list[int], root: Path) -> FormationModel:
    samples: dict[str, tuple[list, list]] = {}
    for mid in matches:
        md = load(mid, "wc2022")
        got = tracking_truth(md, root)
        if got is None:
            continue
        truth, _ = got
        ball = ball_path(md.events, md.grid)
        home_has = possession(md.events, md.home, md.grid)
        for j, p in enumerate(md.players):
            ok = ~np.isnan(truth[:, j, 0])
            if ok.sum() < 100:
                continue
            ok &= np.arange(len(ok)) % 5 == 0                    # one second apart is plenty
            has = home_has if p.team == 0 else 1 - home_has
            X = features(team_frame(ball[ok], p.team), has[ok])
            Y = team_frame(truth[ok, j], p.team)
            roles = p.role_at(md.grid[ok])
            for role in np.unique(roles):
                m = roles == role
                s = samples.setdefault(role, ([], []))
                s[0].append(X[m])
                s[1].append(Y[m])
    return FormationModel.fit(samples)


def evaluate(matches: list[int], model: FormationModel, root: Path) -> dict:
    """Mean distance (m) between reconstructed and real positions, for each method."""
    methods = {
        "Formation only (no ball)": dict(kind="static"),
        "Formation moving with the ball": dict(kind="prior"),
        "+ the player on the ball": dict(kind="rec", use_360=False),
        "+ the 360 frames (full reconstruction)": dict(kind="rec", use_360=True),
    }
    err = {k: [] for k in methods}
    bands = {"0-15 m": (0, 15), "15-30 m": (15, 30), "over 30 m": (30, 1e9)}
    by_band = {b: {"prior": [], "full": []} for b in bands}
    per_match = []
    for mid in matches:
        md = load(mid, "wc2022")
        got = tracking_truth(md, root)
        if got is None:
            continue
        truth, _ = got
        full = reconstruct(md, model, use_360=True)
        row = {"match": f"{md.home} v {md.away}"}
        from matchreplay.tracking_truth import load_tracking
        _, tt, tball, _, _ = load_tracking(md.match_id, root)
        k = np.clip(np.searchsorted(tt, md.grid), 0, len(tt) - 1)
        dball = np.hypot(truth[..., 0] - tball[k, None, 0], truth[..., 1] - tball[k, None, 1])
        for b, (lo, hi) in bands.items():
            m = (dball >= lo) & (dball < hi)
            for key, est in (("prior", full["prior"]), ("full", full["pos"])):
                d = np.hypot(est[..., 0] - truth[..., 0], est[..., 1] - truth[..., 1])[m]
                by_band[b][key].append(d[~np.isnan(d)])
        for name, how in methods.items():
            if how["kind"] == "static":
                est = np.full_like(truth, np.nan)
                for j, p in enumerate(md.players):
                    base = np.array([ROLE_BASE.get(r, (0, 0)) for r in p.role_at(md.grid)], float)
                    est[:, j] = team_frame(base, p.team)
            elif how["kind"] == "prior":
                est = full["prior"]
            elif how["use_360"]:
                est = full["pos"]
            else:
                est = reconstruct(md, model, use_360=False)["pos"]
            d = np.hypot(est[..., 0] - truth[..., 0], est[..., 1] - truth[..., 1])
            d = d[~np.isnan(d)]
            err[name].append(d)
            row[name] = float(d.mean())
        per_match.append(row)
    summary = {k: {"mean": float(np.concatenate(v).mean()), "median": float(np.median(np.concatenate(v))),
                   "within_5m": float((np.concatenate(v) < 5).mean())} for k, v in err.items()}
    bands_out = {b: {k: float(np.concatenate(v).mean()) for k, v in d.items()} | {"share": 0.0}
                 for b, d in by_band.items()}
    total = sum(len(np.concatenate(d["full"])) for d in by_band.values())
    for b, d in by_band.items():
        bands_out[b]["share"] = len(np.concatenate(d["full"])) / total
    return {"summary": summary, "per_match": per_match, "bands": bands_out}


def report(result: dict, train: list[int], test: list[int]) -> str:
    s = result["summary"]
    lines = ["# Reconstructed movement: how close to reality", "",
             "For the matches without tracking, every player's path is reconstructed from the event data "
             "(see `src/matchreplay/reconstruct.py`). To find out how good that is, it is run on World Cup "
             f"2022 matches, where PFF's tracking says where everyone really was. The formation model is "
             f"learned on {len(train)} matches and scored on the other {len(test)}, which it never saw.", "",
             "| Method | Mean error | Median error | Within 5 m |", "|---|---|---|---|"]
    for k, v in s.items():
        lines.append(f"| {k} | {v['mean']:.1f} m | {v['median']:.1f} m | {v['within_5m']:.0%} |")
    lines += ["", "Error is the distance between where the reconstruction puts a player and where the tracking "
              "says they were, over every player on the pitch, five times a second.", "",
             "By distance from the ball (the 360 frames only see the area around it):", "",
             "| Player's distance from the ball | Share of positions | Formation moving with the ball "
             "| Full reconstruction |", "|---|---|---|---|"]
    for b, v in result["bands"].items():
        lines.append(f"| {b} | {v['share']:.0%} | {v['prior']:.1f} m | {v['full']:.1f} m |")
    lines += ["", "Near the ball, where the action is and the data sees most, the reconstruction is clearly "
              "closest; far from it, players are rarely seen and stay close to what the formation model "
              "predicts.", "",
              "By match (mean error, full reconstruction):", "", "| Match | Mean error |", "|---|---|"]
    full = "+ the 360 frames (full reconstruction)"
    for r in sorted(result["per_match"], key=lambda r: r[full]):
        lines.append(f"| {r['match']} | {r[full]:.1f} m |")
    return "\n".join(lines) + "\n"


# -- export for the 3D view -------------------------------------------------------------------------

def export(md: MatchData, model: FormationModel, out_root: Path = OUT) -> dict:
    from matchreplay.tracking_web import write_chunks

    MISS = np.iinfo(np.int16).min
    rec = reconstruct(md, model)
    # 2.5 positions a second to 10 cm is plenty for paths this smooth, and keeps a match near 400 KB
    keep = slice(None, None, EXPORT_EVERY)
    pos, grid = rec["pos"][keep], md.grid[keep]
    cm = lambda a: np.where(np.isnan(a), MISS, np.round(a * 10) * 10).astype(np.int16)  # noqa: E731
    sides = []
    for team in (0, 1):
        js = [j for j, p in enumerate(md.players) if p.team == team]
        sides.append((js, cm(pos[:, js])))
    ball = np.zeros((len(grid), 4), np.int16)
    ball[:, :2] = cm(rec["ball"][keep])
    ball[:, 3] = 1                                        # every ball position here is rebuilt
    out = out_root / str(md.match_id)
    chunks, total = write_chunks(out, grid, ball, sides[0][1], sides[1][1])
    shirts = [[md.players[j].shirt or f"#{k + 1}" for k, j in enumerate(js)] for js, _ in sides]
    names = [{md.players[j].shirt or f"#{k + 1}": md.players[j].name for k, j in enumerate(js)} for js, _ in sides]
    keepers = [[md.players[j].shirt for j in js if md.players[j].role and md.players[j].role[0] == "Goalkeeper"]
               for js, _ in sides]
    (out / "meta.json").write_text(json.dumps({
        "match_id": md.match_id, "kind": "reconstructed", "home": md.home, "away": md.away,
        "home_kit": {"primary": "#f2f5f3", "text": "#111111", "secondary": "#1f2a44"},
        "away_kit": {"primary": "#f5b82e", "text": "#111111", "secondary": "#3a2a0a"},
        "home_shirts": shirts[0], "away_shirts": shirts[1], "home_names": names[0], "away_names": names[1],
        "home_keepers": keepers[0], "away_keepers": keepers[1],
        "pitch": {"length": L, "width": W}, "fps": 1000 / STEP_MS / EXPORT_EVERY, "chunk_ms": 60_000, "ball_series": 4,
        "ball_rebuilt_share": 1.0, "chunks": chunks,
        "source": "Reconstructed from Hudl StatsBomb event and 360 data (src/matchreplay/reconstruct.py)",
    }, ensure_ascii=False, separators=(",", ":")))
    return {"match_id": md.match_id, "home": md.home, "away": md.away, "kb": round(total / 1000)}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tracking", type=Path, default=ROOT / "site" / "tracking", help="tracking files (web format)")
    parser.add_argument("--fit", action="store_true")
    parser.add_argument("--evaluate", action="store_true")
    parser.add_argument("--export", nargs="*", help="competition keys to export (default: all but wc2022)")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    wc = sorted(int(m) for m in pd.read_parquet(MATCHES / "wc2022" / "matches.parquet")["match_id"])
    train, test = wc[::2], wc[1::2]
    if args.fit or args.evaluate:
        model = fit(train, args.tracking)
        print(f"formation model: {len(model.coef)} roles learned from {len(train)} matches")
        if args.evaluate:
            result = evaluate(test, model, args.tracking)
            for k, v in result["summary"].items():
                print(f"{k:<42} mean {v['mean']:.2f} m  median {v['median']:.2f} m  within 5 m {v['within_5m']:.0%}")
            (REPORTS / "reconstruction.md").write_text(report(result, train, test))
        model = fit(wc, args.tracking)                    # the model shipped: every World Cup match
        model.save()
    if args.export is not None:
        model = FormationModel.load()
        keys = args.export or [p.name for p in sorted(MATCHES.iterdir()) if p.name != "wc2022"]
        for key in keys:
            ids = pd.read_parquet(MATCHES / key / "matches.parquet")["match_id"]
            for mid in ids:
                print(export(load(int(mid), key), model, args.out), flush=True)
        rows = [json.loads(p.read_text()) for p in sorted(args.out.glob("*/meta.json"))]
        (args.out / "index.json").write_text(json.dumps(
            [{"match_id": r["match_id"], "home": r["home"], "away": r["away"]} for r in rows], separators=(",", ":")))


if __name__ == "__main__":
    main()
