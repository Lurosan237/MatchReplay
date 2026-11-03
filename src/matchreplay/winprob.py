"""Live win probability: who wins from here, at any moment of a match.

    python -m matchreplay.winprob            # train, evaluate, write models/winprob.json and reports/winprob.md

The model, in two parts.

1. How fast each team scores from now on. A goal rate per minute for each team,
   log(rate) = features . beta, a Poisson regression on (goals the team scored in the rest of the
   match) with the remaining minutes as exposure. The features describe the moment from that
   team's side: the game state (leading or trailing by one, or by two or more: teams that lead
   sit back, teams that trail push), red cards for and against, how much it has been creating
   and conceding so far (expected goals per 90 minutes, pulled towards the average early on, when
   little has happened), women's football, extra time.

2. What those rates add up to. The rest of the match is played minute by minute on the score
   difference: each minute each team scores with probability 1 - exp(-rate), and the rates move
   with the game state as the difference changes (a team that equalises stops pushing as hard).
   At the end: P(home win), P(draw), P(away win). Exact, no sampling, the same few lines in
   Python, Java (the Flink job) and JavaScript (the browser), and tested to agree.

"The rest of the match" is the current phase: regulation (90 minutes plus stoppage time) or,
once it starts, extra time, where a draw means penalties. Remaining time counts the average
stoppage time of the data.

Evaluated by leaving one competition out at a time: every match is forecast by a model that never
saw it (reports/winprob.md).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from matchreplay.paths import MATCHES, REPORTS, ROOT
from matchreplay.stream import PERIOD_OFFSET_MIN

MODEL = ROOT / "models" / "winprob.json"
# Average length of each period in the data (minutes), stoppage time included
PERIOD_LENGTH = {1: 48.0, 2: 50.7, 3: 16.6, 4: 17.4}
PERIOD_START_MIN = {1: 0.0, 2: 45.0, 3: 90.0, 4: 105.0}
MIN_REMAINING = 0.5
XG_PRIOR_PER_90 = 1.3      # a typical team's expected goals per 90 minutes
XG_PRIOR_MINUTES = 30.0    # how strongly the early minutes are pulled towards it
FEATURES = ("intercept", "home", "lead1", "lead2", "trail1", "trail2", "own_red", "opp_red", "xg_for", "xg_against",
            "women", "extra_time")
MAX_DIFF = 12              # score differences tracked from the current one: far more than ever happens


# -- the moment, from one team's side ----------------------------------------------------------------

def elapsed_in_period(t: int, period: int) -> float:
    return (t - PERIOD_OFFSET_MIN[period] * 60_000) / 60_000


def remaining_minutes(t: int, period: int) -> float:
    """Expected minutes of play left in the current phase (regulation, or extra time)."""
    left = max(PERIOD_LENGTH[period] - elapsed_in_period(t, period), MIN_REMAINING)
    if period in (1, 3):
        left += PERIOD_LENGTH[period + 1]
    return left


def minutes_played(t: int, period: int) -> float:
    return PERIOD_START_MIN[period] + max(elapsed_in_period(t, period), 0.0)


def shrunk_per_90(xg: float, played: float) -> float:
    return (xg + XG_PRIOR_PER_90 / 90 * XG_PRIOR_MINUTES) / (played + XG_PRIOR_MINUTES) * 90


def features(home: bool, diff: int, own_red: int, opp_red: int, xg_for: float, xg_against: float, women: bool,
             extra_time: bool) -> list[float]:
    """diff: this team's goals minus the opponent's. home: the team listed first (at home in a league;
    in a tournament usually the higher-ranked side)."""
    return [1.0, float(home), float(diff == 1), float(diff >= 2), float(diff == -1), float(diff <= -2),
            float(min(own_red, 2)), float(min(opp_red, 2)), xg_for - XG_PRIOR_PER_90,
            xg_against - XG_PRIOR_PER_90, float(women), float(extra_time)]


def rate(beta: list[float], x: list[float]) -> float:
    return math.exp(sum(b * v for b, v in zip(beta, x, strict=True)))


# -- from rates to outcome probabilities -----------------------------------------------------------

def outcome(beta: list[float], diff: int, remaining: float, home: tuple, away: tuple, women: bool,
            extra_time: bool) -> list[float]:
    """P(home wins, draw, away wins) at the end of the phase.

    home / away: (red cards, expected goals per 90 so far, shrunk). Played minute by minute on the
    score difference; the last step is what is left of the final minute. The Java (Flink) and
    JavaScript (browser) versions follow this one line by line, so the three agree to the 4th decimal.
    """
    n = 2 * MAX_DIFF + 1
    # Each team's goal rate (per minute) for every score difference d the match could reach
    rh = [rate(beta, features(True, diff - MAX_DIFF + i, home[0], away[0], home[1], away[1], women, extra_time))
          for i in range(n)]
    ra = [rate(beta, features(False, MAX_DIFF - diff - i, away[0], home[0], away[1], home[1], women, extra_time))
          for i in range(n)]
    p = [0.0] * n
    p[MAX_DIFF] = 1.0
    steps = max(1, math.ceil(remaining))
    for step in range(steps):
        dt = remaining - (steps - 1) if step == steps - 1 else 1.0
        q = [0.0] * n
        for i in range(n):
            if p[i] == 0.0:
                continue
            gh = 1.0 - math.exp(-rh[i] * dt)
            ga = 1.0 - math.exp(-ra[i] * dt)
            q[i] += p[i] * ((1.0 - gh) * (1.0 - ga) + gh * ga)
            q[min(i + 1, n - 1)] += p[i] * gh * (1.0 - ga)
            q[max(i - 1, 0)] += p[i] * ga * (1.0 - gh)
        p = q
    win = draw = 0.0
    for i in range(n):
        d = diff - MAX_DIFF + i
        if d > 0:
            win += p[i]
        elif d == 0:
            draw += p[i]
    return [round(win, 4), round(draw, 4), round(1.0 - win - draw, 4)]


def from_snapshot(beta: list[float], s: dict, women: bool = False) -> list[float] | None:
    """Win probability for a pipeline snapshot. None during the penalty shoot-out."""
    period = s["period"]
    if period not in PERIOD_LENGTH:
        return None
    t = s["time"]
    played = minutes_played(t, period)
    h, a = s["home_stats"], s["away_stats"]
    home = (h["red"], shrunk_per_90(h["xg"], played))
    away = (a["red"], shrunk_per_90(a["xg"], played))
    return outcome(beta, s["score"][0] - s["score"][1], remaining_minutes(t, period), home, away, women,
                   period >= 3)


def load(path: Path = MODEL) -> dict | None:
    return json.loads(path.read_text()) if path.exists() else None


# -- training data -----------------------------------------------------------------------------------

def all_matches() -> list[tuple[str, bool, int, list[dict]]]:
    """(competition, women's football, match id, reference snapshots) for every match."""
    from matchreplay.ingest import COMPETITIONS
    from matchreplay.stream import messages, reference_snapshots

    out = []
    for comp in COMPETITIONS:
        for mid in pd.read_parquet(MATCHES / comp.key / "matches.parquet")["match_id"]:
            out.append((comp.key, comp.women, int(mid), reference_snapshots(messages(int(mid)))))
    return out


def match_rows(snaps: list[dict], women: bool, every: int = 12) -> list[dict]:
    """One row per team per minute (every 12th 5-second snapshot): the moment's features, the goals
    the team went on to score before the end of the phase, and the minutes of play that took."""
    phase_end, period_end = {}, {}
    for s in snaps:
        if s["period"] in PERIOD_LENGTH:
            phase_end[0 if s["period"] <= 2 else 1] = s
            period_end[s["period"]] = s["time"]
    rows = []
    for k, s in enumerate(snaps):
        period = s["period"]
        if period not in PERIOD_LENGTH or k % every:
            continue
        end = phase_end[0 if period <= 2 else 1]
        exposure = (period_end[period] - s["time"]) / 60_000
        if end["period"] != period:   # the rest of this half, then the next half up to the end
            exposure += elapsed_in_period(end["time"], end["period"])
        if exposure <= 0:
            continue
        played = minutes_played(s["time"], period)
        for own, opp, i in (("home_stats", "away_stats", 0), ("away_stats", "home_stats", 1)):
            diff = s["score"][i] - s["score"][1 - i]
            x = features(i == 0, diff, s[own]["red"], s[opp]["red"], shrunk_per_90(s[own]["xg"], played),
                         shrunk_per_90(s[opp]["xg"], played), women, period >= 3)
            rows.append({"match_id": s["match_id"], "x": x, "goals": end["score"][i] - s["score"][i],
                         "exposure": exposure})
    return rows


# -- fitting: Poisson regression with exposure, by iteratively reweighted least squares --------------

def fit_poisson(X: np.ndarray, y: np.ndarray, exposure: np.ndarray, l2: float = 1.0, iters: int = 50) -> np.ndarray:
    beta = np.zeros(X.shape[1])
    beta[0] = math.log(max(y.sum() / exposure.sum(), 1e-6))
    penalty = np.full(X.shape[1], l2)
    penalty[0] = 0.0
    for _ in range(iters):
        mu = exposure * np.exp(X @ beta)
        grad = X.T @ (y - mu) - penalty * beta
        hess = (X * mu[:, None]).T @ X + np.diag(penalty)
        step = np.linalg.solve(hess, grad)
        beta += step
        if np.abs(step).max() < 1e-9:
            break
    return beta


# -- evaluation --------------------------------------------------------------------------------------

def phase_outcomes(snaps: list[dict]) -> dict[int, int]:
    """0 home win, 1 draw, 2 away win, at the end of regulation (phase 0) and of extra time (phase 1)."""
    out = {}
    for s in snaps:
        if s["period"] in PERIOD_LENGTH:
            d = s["score"][0] - s["score"][1]
            out[0 if s["period"] <= 2 else 1] = 0 if d > 0 else 1 if d == 0 else 2
    return out


def forecasts(matches, betas: dict[str, list[float]]) -> pd.DataFrame:
    """Each model's win/draw/loss forecast once a minute, with what actually happened."""
    rows = []
    for key, women, mid, snaps in matches:
        result = phase_outcomes(snaps)
        for k, s in enumerate(snaps):
            if k % 12 or s["period"] not in PERIOD_LENGTH:
                continue
            row = {"competition": key, "match_id": mid, "minute": minutes_played(s["time"], s["period"]),
                   "y": result[0 if s["period"] <= 2 else 1]}
            for name, beta in betas.items():
                row[name] = from_snapshot(beta, s, women)
            rows.append(row)
    return pd.DataFrame(rows)


def scores(df: pd.DataFrame, col: str) -> tuple[float, float]:
    """Log loss and Brier score."""
    probs = np.clip(np.array(df[col].tolist()), 1e-6, 1)
    y = df["y"].to_numpy()
    onehot = np.eye(3)[y]
    return float(-np.log(probs[np.arange(len(df)), y]).mean()), float(((probs - onehot) ** 2).sum(1).mean())


def fit(rows: pd.DataFrame, keep: tuple[str, ...] = FEATURES) -> list[float]:
    """Fit on the chosen features; the others get a zero coefficient (no effect)."""
    idx = [FEATURES.index(f) for f in keep]
    X = np.array(rows["x"].tolist())[:, idx]
    b = fit_poisson(X, rows["goals"].to_numpy(float), rows["exposure"].to_numpy(float))
    beta = [0.0] * len(FEATURES)
    for j, v in zip(idx, b, strict=True):
        beta[j] = round(float(v), 6)
    return beta


SCORE_AND_CLOCK = ("intercept", "home", "lead1", "lead2", "trail1", "trail2", "women", "extra_time")


def cross_validate(matches, rows: pd.DataFrame) -> pd.DataFrame:
    """Leave one competition out: each competition is forecast by models that never saw it."""
    out = []
    for key in sorted({m[0] for m in matches}):
        train = rows[rows["competition"] != key]
        betas = {"model": fit(train), "score_and_clock": fit(train, SCORE_AND_CLOCK)}
        out.append(forecasts([m for m in matches if m[0] == key], betas))
    df = pd.concat(out, ignore_index=True)
    prior = np.bincount(df["y"], minlength=3) / len(df)
    df["average"] = [list(prior)] * len(df)
    return df


def report(model: dict, cv: pd.DataFrame) -> str:
    beta = dict(zip(FEATURES, model["beta"], strict=True))
    names = {"model": "This model", "score_and_clock": "Score and clock only", "average": "Always the average outcome"}
    total = {k: scores(cv, k) for k in names}
    lines = [
        "# Live win probability", "",
        "A goal-rate model for each team (Poisson regression), played forward minute by minute to the end "
        "of the phase. See `src/matchreplay/winprob.py` for the method.", "",
        "## How good is it", "",
        f"Leave-one-competition-out: each of the 12 competitions is forecast by a model trained on the other "
        f"11, so every one of the {cv['match_id'].nunique()} matches is forecast without having been seen. "
        f"{len(cv):,} forecasts, one per minute of play.", "",
        "| Forecast | Log loss | Brier score |", "|---|---|---|",
        *[f"| {names[k]} | {total[k][0]:.3f} | {total[k][1]:.3f} |" for k in names], "",
        "Lower is better. \"Score and clock only\" knows the score, the time left and the game state; the full "
        "model also reads the match: expected goals for and against so far, and red cards.", "",
        "By competition (Brier score, unseen):", "",
        "| Competition | Matches | This model | Score and clock only |", "|---|---|---|---|",
    ]
    better = 0
    for key, sub in cv.groupby("competition"):
        mb, bb = scores(sub, "model")[1], scores(sub, "score_and_clock")[1]
        better += mb < bb
        lines.append(f"| {key} | {sub['match_id'].nunique()} | {mb:.3f} | {bb:.3f} |")
    lines += ["", f"Reading the match helps in {better} of the 12 competitions; where it doesn't, the two are "
              "within a few thousandths. Tournaments full of upsets (AFCON 2023 is the clearest) are hard for any "
              "model that knows nothing about the teams before kick-off: that is the obvious next feature."]
    lines += ["", "By stage of the match (Brier score):", "",
              "| Minutes | This model | Score and clock only |", "|---|---|---|"]
    for lo, hi in ((0, 30), (30, 60), (60, 90), (90, 125)):
        sub = cv[(cv["minute"] >= lo) & (cv["minute"] < hi)]
        lines.append(f"| {lo}-{hi} | {scores(sub, 'model')[1]:.3f} | {scores(sub, 'score_and_clock')[1]:.3f} |")
    lines += ["", "## Calibration", "", "When the model gives the home team X%, does it win X% of the time?", "",
              "| Predicted home win | Forecasts | Average prediction | Home team won |", "|---|---|---|---|"]
    ph = np.array(cv["model"].tolist())[:, 0]
    for lo in np.arange(0, 1, 0.1):
        m = (ph >= lo) & (ph < lo + 0.1 if lo < 0.9 else ph <= 1)
        if m.sum() >= 30:
            lines.append(f"| {lo:.1f}-{lo + 0.1:.1f} | {int(m.sum()):,} | {ph[m].mean():.2f} | "
                         f"{(cv['y'][m] == 0).mean():.2f} |")
    lines += ["", "## What it learned (all 558 matches)", "",
              "Multipliers on a team's goal rate (1.00 = no effect):", "",
              "| Feature | Multiplier |", "|---|---|",
              *[f"| {k} | {math.exp(v):.2f} |" for k, v in beta.items() if k != "intercept"], "",
              "`xg_for` and `xg_against` are per extra expected goal per 90 minutes, so far in the match "
              "(pulled towards the average early on).", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.parse_args(argv)
    matches = all_matches()
    rows = []
    for key, women, _mid, snaps in matches:
        for r in match_rows(snaps, women):
            r["competition"] = key
            rows.append(r)
    rows = pd.DataFrame(rows)
    cv = cross_validate(matches, rows)
    model = {"features": list(FEATURES), "beta": fit(rows), "period_length": PERIOD_LENGTH,
             "xg_prior_per_90": XG_PRIOR_PER_90, "xg_prior_minutes": XG_PRIOR_MINUTES, "max_diff": MAX_DIFF,
             "min_remaining": MIN_REMAINING, "train_matches": len(matches)}
    MODEL.parent.mkdir(parents=True, exist_ok=True)
    MODEL.write_text(json.dumps(model, indent=1))
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "winprob.md").write_text(report(model, cv))
    print({k: [round(v, 4) for v in scores(cv, k)] for k in ("model", "score_and_clock", "average")})
    print(dict(zip(FEATURES, (round(math.exp(b), 3) for b in model["beta"]), strict=True)))


if __name__ == "__main__":
    main()
