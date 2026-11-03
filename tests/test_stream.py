import random

import pytest

from matchreplay.stream import Chaos, MatchState, chaos, event_time_ms, messages, reference_snapshots


def ev(i, t_s, kind, team="A", period=1, **kw):
    base = {"match_id": 1, "home": "A", "away": "B", "event_id": f"e{i}", "index": i, "period": period,
            "minute": int(t_s // 60), "second": int(t_s % 60), "type": kind, "team": team, "player": None,
            "x": None, "y": None, "end_x": None, "end_y": None, "outcome": None, "xg": None, "card": None,
            "duration": None, "event_time": event_time_ms(period, t_s)}
    base.update(kw)
    return base


def test_event_time_always_increases_across_periods_even_with_stoppage_time():
    assert event_time_ms(1, 50 * 60) < event_time_ms(2, 0) < event_time_ms(3, 0) < event_time_ms(5, 0)


def test_state_counts_goals_shots_cards_and_own_goals():
    s = MatchState(1, "A", "B")
    for m in [ev(1, 10, "Shot", outcome="Goal", xg=0.3), ev(2, 20, "Shot", outcome="Saved", xg=0.1),
              ev(3, 30, "Own Goal For", team="B"), ev(4, 40, "Foul Committed", team="B", card="Yellow Card"),
              ev(5, 50, "Pass", end_x=110, end_y=40), ev(6, 60, "Pass", outcome="Incomplete"),
              ev(7, 70, "Shot", period=5, outcome="Goal", team="B")]:
        s.apply(m)
    snap = s.snapshot(70_000)
    assert snap["score"] == [1, 1]
    assert snap["shootout"] == [0, 1]
    assert snap["home_stats"]["shots"] == 2 and snap["home_stats"]["on_target"] == 2
    assert snap["home_stats"]["xg"] == pytest.approx(0.4)
    assert snap["home_stats"]["passes"] == 2 and snap["home_stats"]["passes_completed"] == 1
    assert snap["home_stats"]["box_entries"] == 1
    assert snap["away_stats"]["yellow"] == 1 and snap["away_stats"]["fouls"] == 1


def test_momentum_halves_every_five_minutes():
    s = MatchState(1, "A", "B")
    s.apply(ev(1, 0, "Shot", outcome="Off T", xg=0.4))
    assert s.snapshot(300_000)["momentum"] == pytest.approx(0.2)
    assert s.snapshot(600_000)["momentum"] == pytest.approx(0.1)


def test_snapshots_ignore_arrival_order_and_duplicates():
    msgs = [ev(i, i * 7.0, "Pass", team="A" if i % 3 else "B") for i in range(200)]
    shuffled = msgs + msgs[:10]
    random.Random(1).shuffle(shuffled)
    assert reference_snapshots(shuffled) == reference_snapshots(msgs)


def test_chaos_is_reproducible_and_keeps_every_event():
    msgs = [ev(i, i * 3.0, "Pass") for i in range(500)]
    a, b = chaos(msgs, Chaos(seed=3)), chaos(msgs, Chaos(seed=3))
    assert a == b
    assert {m["event_id"] for m in a} == {m["event_id"] for m in msgs}
    assert len(a) >= len(msgs)
    assert [m["event_id"] for m in a] != [m["event_id"] for m in msgs]


@pytest.mark.skipif(not __import__("matchreplay.paths", fromlist=["MATCHES"]).MATCHES.exists(), reason="needs data")
def test_a_real_match_ends_with_its_real_score():
    msgs = messages(3922238)   # AFCON 2023 round of 16: Nigeria 2-0 Cameroon
    last = reference_snapshots(chaos(msgs))[-1]
    assert (last["home"], last["away"], last["score"]) == ("Nigeria", "Cameroon", [2, 0])
