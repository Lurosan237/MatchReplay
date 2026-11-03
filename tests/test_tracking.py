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
