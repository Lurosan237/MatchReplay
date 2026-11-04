"""Read the tracking files back (site/tracking/<match>/, written by tracking_web): every frame of
a match as arrays, in metres. Used to score the reconstructed movement against real positions."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import numpy as np

MISSING = -32768


def load_tracking(match_id: int, root: Path):
    """(meta, t (F,), ball (F, 3), home (F, Jh, 2), away (F, Ja, 2)) with NaN where missing, or None."""
    d = Path(root) / str(match_id)
    if not (d / "meta.json").exists():
        return None
    m = json.loads((d / "meta.json").read_text())
    nb, nh, na = m.get("ball_series", 3), len(m["home_shirts"]), len(m["away_shirts"])
    times, blocks = [], []
    for c in m["chunks"]:
        b = gzip.decompress((d / f"{c['n']}.bin.gz").read_bytes())
        n = int(np.frombuffer(b[:4], "<i4")[0])
        times.append(np.frombuffer(b[4:4 + 4 * n], "<i4"))
        s = np.frombuffer(b[4 + 4 * n:], "<i2").reshape(-1, n).astype(np.int64)
        blocks.append(((np.cumsum(s, axis=1) + 32768) % 65536 - 32768).astype(np.int16))
    t, s = np.concatenate(times).astype(np.int64), np.concatenate(blocks, axis=1)

    def pos(a):
        f = a.astype(float) / 100
        f[a == MISSING] = np.nan
        return f

    ball = pos(s[:3]).T
    home = np.stack([pos(s[nb + 2 * j:nb + 2 * j + 2]).T for j in range(nh)], 1)
    away = np.stack([pos(s[nb + 2 * nh + 2 * j:nb + 2 * nh + 2 * j + 2]).T for j in range(na)], 1)
    return m, t, ball, home, away
