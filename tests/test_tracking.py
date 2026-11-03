import numpy as np

from matchreplay.tracking import MISSING
from matchreplay.tracking_web import delta


def decode(d: np.ndarray) -> np.ndarray:
    """What the browser does (site/js/tracking.js): a running sum, wrapping in 16 bits."""
    out, acc = np.empty_like(d), 0
    for i, v in enumerate(d.astype(np.int64)):
        acc = ((acc + v + 32768) % 65536) - 32768
        out[i] = acc
    return out


def test_delta_encoding_round_trips_even_across_missing_players():
    rng = np.random.default_rng(1)
    walk = np.cumsum(rng.integers(-40, 40, 2000)).astype(np.int16)
    walk[300:420] = MISSING                    # substituted off, then back (or a different man)
    walk[-5:] = MISSING
    for series in (walk, np.array([MISSING, 5000, -5000, MISSING, 32767, -32767], dtype=np.int16)):
        assert np.array_equal(decode(delta(series)), series)


def test_small_moves_become_small_numbers():
    walk = np.cumsum(np.full(1000, 3)).astype(np.int16) + 1000
    d = delta(walk)
    assert d[0] == 1003 and set(d[1:].tolist()) == {3}


def test_cleaning_removes_a_glitch_but_keeps_a_steady_run():
    from matchreplay.tracking_web import clean_players

    n = 300
    run = np.zeros((n, 1, 2), dtype=np.int16)
    run[:, 0, 0] = np.arange(n) * 20                  # 6 m/s along x, centimetres per frame
    glitch = run.copy()
    glitch[150:154, 0, 1] = 900                         # a 4-frame jump 9 m sideways
    out = clean_players(glitch)
    assert np.abs(out[140:160, 0, 1]).max() < 50        # the jump is gone
    assert np.abs(out[20:280, 0, 0].astype(int) - run[20:280, 0, 0]).max() <= 1   # the run is untouched


def test_the_ball_is_rebuilt_from_the_player_who_touched_it():
    from matchreplay.tracking_web import fill_ball

    n = 100
    period = np.ones(n, dtype=np.int8)
    clock = (np.arange(n) * 33).astype(np.int64)
    ball = np.full((n, 3), MISSING, dtype=np.int16)
    ball[:10] = [0, 0, 0]                                # tracked, then lost by the cameras
    home = np.full((n, 1, 2), MISSING, dtype=np.int16)
    home[:, 0] = [1000, 500]                             # shirt 9 stands still at (10 m, 5 m)
    away = np.full((n, 0, 2), MISSING, dtype=np.int16)
    events = [{"type": "OTB", "shirt": "9", "team": "Home", "period": 1, "clock_ms": 60 * 33}]
    out, rebuilt = fill_ball(ball, home, away, ["9"], [], events, period, clock, "Home")
    assert rebuilt[:10].sum() == 0 and rebuilt[10:].all()
    assert tuple(out[60, :2]) == (1000, 500)             # at the touch: at the player's feet
    assert 0 < out[35, 0] < 1000                         # on its way there in between
    assert (out[:, 0] != MISSING).all()                  # never missing
