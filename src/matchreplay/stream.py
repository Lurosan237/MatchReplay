"""The replay stream: match events as messages, a chaos layer, and the reference match state.

Three pieces, shared by every later layer:

1. `messages(match_id)`: a match's events as the messages the pipeline receives, each with an
   event time in milliseconds on one match-long clock (below).
2. `chaos(...)`: what a real feed does to them on the way: some arrive late, a few twice,
   in a different order. Seeded, so a run can be repeated exactly.
3. `MatchState` + `reference_snapshots(...)`: the live numbers (score, shots, xG, passes,
   cards, momentum) computed the simple, obviously-correct way, in event order. The Java/Flink
   job must produce exactly the same snapshots from the chaotic stream: this is its test oracle,
   like the parity test of the CleverPay project.

The match clock. StatsBomb restarts the clock at each period, and stoppage time means the
first half's clock can run past 45:00. Event time therefore starts each period at a fixed
offset with room to spare (0, 60, 120, 140 and 160 minutes), so it always increases through a
match. The minute shown to viewers is StatsBomb's own `minute` field.
"""

from __future__ import annotations

import json
import math
import random
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field

import pandas as pd

from matchreplay.paths import MATCHES

PERIOD_OFFSET_MIN = {1: 0, 2: 60, 3: 120, 4: 140, 5: 160}
SHOOTOUT = 5
SNAPSHOT_EVERY_MS = 5_000          # one state snapshot per 5 s of match time
MOMENTUM_HALF_LIFE_S = 300.0       # momentum forgets half of what happened 5 minutes ago
ON_TARGET = {"Goal", "Saved", "Saved To Post"}
CARDS = {"Yellow Card": "yellow", "Second Yellow": "red", "Red Card": "red"}
# The penalty area, on StatsBomb's 120 x 80 pitch, attacking to the right
BOX_X, BOX_Y = 102.0, (18.0, 62.0)

FIELDS = ("event_id", "index", "period", "minute", "second", "type", "team", "player", "x", "y", "end_x", "end_y",
          "outcome", "xg", "card", "duration")


def event_time_ms(period: int, clock_s: float) -> int:
    return int(round((PERIOD_OFFSET_MIN[period] * 60 + clock_s) * 1000))


def _clean(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return v.item() if hasattr(v, "item") else v


def load_match(match_id: int) -> tuple[dict, pd.DataFrame]:
    for folder in MATCHES.iterdir():
        matches = pd.read_parquet(folder / "matches.parquet")
        row = matches[matches["match_id"] == match_id]
        if len(row):
            events = pd.read_parquet(folder / "events.parquet", filters=[("match_id", "==", match_id)])
            return {k: _clean(v) for k, v in row.iloc[0].to_dict().items()}, events.sort_values("index")
    raise KeyError(f"match {match_id} not found in data/matches")


def messages(match_id: int) -> list[dict]:
    """The match as the pipeline's input messages, in true event order."""
    meta, events = load_match(match_id)
    out = []
    for e in events.itertuples(index=False):
        msg = {"match_id": match_id, "home": meta["home"], "away": meta["away"],
               "event_time": event_time_ms(int(e.period), float(e.clock))}
        msg.update({f: _clean(getattr(e, f)) for f in FIELDS})
        out.append(msg)
    return out


# -- chaos -------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Chaos:
    late_share: float = 0.05        # share of messages that arrive late
    max_delay_ms: int = 8_000       # how late (match time), uniformly up to this
    duplicate_share: float = 0.01   # share delivered twice
    seed: int = 7


DEFAULT_CHAOS = Chaos()


def chaos(msgs: list[dict], c: Chaos = DEFAULT_CHAOS) -> list[dict]:
    """Arrival order of a real feed: each message is delivered at its event time plus a delay;
    some are delayed, a few duplicated. Ties keep the original order."""
    rng = random.Random(c.seed)
    deliveries = []
    for i, m in enumerate(msgs):
        delay = rng.randint(1, c.max_delay_ms) if rng.random() < c.late_share else 0
        deliveries.append((m["event_time"] + delay, i, 0, m))
        if rng.random() < c.duplicate_share:
            deliveries.append((m["event_time"] + rng.randint(0, c.max_delay_ms), i, 1, m))
    return [m for *_, m in sorted(deliveries, key=lambda d: (d[0], d[1], d[2]))]


# -- the live match state ----------------------------------------------------------------------------

@dataclass
class Side:
    goals: int = 0
    shots: int = 0
    on_target: int = 0
    xg: float = 0.0
    passes: int = 0
    passes_completed: int = 0
    box_entries: int = 0
    fouls: int = 0
    yellow: int = 0
    red: int = 0
    shootout_goals: int = 0
    momentum: float = 0.0           # decayed sum of threat: xG of shots + 0.05 per box entry


@dataclass
class MatchState:
    match_id: int
    home: str
    away: str
    sides: dict = field(default_factory=dict)
    minute: int = 0
    period: int = 1
    last_time: int = 0              # event time of the last applied event (ms)
    events: int = 0

    def __post_init__(self):
        self.sides = self.sides or {self.home: Side(), self.away: Side()}

    def _decay(self, t: int) -> None:
        if t > self.last_time:
            factor = 0.5 ** ((t - self.last_time) / 1000 / MOMENTUM_HALF_LIFE_S)
            for s in self.sides.values():
                s.momentum *= factor
            self.last_time = t

    def apply(self, m: dict) -> None:
        """Apply one event. Events must come in event-time order (ties by index)."""
        self._decay(m["event_time"])
        self.events += 1
        self.minute, self.period = m["minute"], m["period"]
        team = m["team"]
        if team not in self.sides:
            return
        s, kind = self.sides[team], m["type"]
        if kind == "Shot":
            if m["period"] == SHOOTOUT:
                s.shootout_goals += m["outcome"] == "Goal"
                return
            s.shots += 1
            s.on_target += m["outcome"] in ON_TARGET
            s.goals += m["outcome"] == "Goal"
            s.xg += m["xg"] or 0.0
            s.momentum += m["xg"] or 0.0
        elif kind == "Own Goal For" and m["period"] != SHOOTOUT:
            s.goals += 1
        elif kind == "Pass":
            s.passes += 1
            if m["outcome"] is None:
                s.passes_completed += 1
                if _in_box(m):
                    s.box_entries += 1
                    s.momentum += 0.05
        elif kind == "Carry" and _in_box(m) and not _in_box({"end_x": m["x"], "end_y": m["y"]}):
            s.box_entries += 1
            s.momentum += 0.05
        elif kind == "Foul Committed":
            s.fouls += 1
        if m["card"] in CARDS:
            if CARDS[m["card"]] == "yellow":
                s.yellow += 1
            else:
                s.red += 1

    def snapshot(self, t: int) -> dict:
        self._decay(t)
        h, a = self.sides[self.home], self.sides[self.away]
        total_passes = h.passes + a.passes
        return {
            "match_id": self.match_id, "time": t, "minute": self.minute, "period": self.period, "events": self.events,
            "home": self.home, "away": self.away, "score": [h.goals, a.goals],
            "shootout": [h.shootout_goals, a.shootout_goals],
            "home_stats": _round(asdict(h)), "away_stats": _round(asdict(a)),
            "possession_home": round(h.passes / total_passes, 4) if total_passes else 0.5,
            "momentum": round(h.momentum - a.momentum, 4),
        }


def _in_box(m: dict) -> bool:
    x, y = m.get("end_x"), m.get("end_y")
    return x is not None and y is not None and x >= BOX_X and BOX_Y[0] <= y <= BOX_Y[1]


def _round(d: dict) -> dict:
    return {k: round(v, 4) if isinstance(v, float) else v for k, v in d.items()}


def reference_snapshots(msgs: Iterable[dict], every_ms: int = SNAPSHOT_EVERY_MS) -> list[dict]:
    """The live state at the end of every `every_ms` of match time that contains at least one event,
    computed from the events in true order with duplicates removed. The oracle for the pipeline."""
    unique = {m["event_id"]: m for m in msgs}
    ordered = sorted(unique.values(), key=lambda m: (m["event_time"], m["index"]))
    if not ordered:
        return []
    first = ordered[0]
    state = MatchState(first["match_id"], first["home"], first["away"])
    out, window_end = [], None
    for m in ordered:
        end = (m["event_time"] // every_ms + 1) * every_ms
        if window_end is not None and end != window_end:
            out.append(state.snapshot(window_end))
        window_end = end
        state.apply(m)
    out.append(state.snapshot(window_end))
    return out


def write_jsonl(rows: Iterable[dict], path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")


def read_jsonl(path) -> Iterator[dict]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)
