"""Test fixtures for the Java/Flink pipeline, generated from the Python reference.

    python -m matchreplay.fixtures

For each match below, writes to pipeline/src/test/resources/fixtures/:
    <match>.input.jsonl.gz      the match's events as a chaotic feed delivers them, then the end marker
    <match>.expected.jsonl.gz   the snapshots the reference computes from them

The Flink job must turn the first file into the second, exactly.
"""

from __future__ import annotations

import gzip
import json

from matchreplay.paths import ROOT
from matchreplay.stream import Chaos, chaos, end_marker, messages, reference_snapshots

FIXTURES = ROOT / "pipeline" / "src" / "test" / "resources" / "fixtures"
MATCHES = {
    3922238: "AFCON 2023 round of 16, Nigeria 2-0 Cameroon",
    3922242: "AFCON 2023 round of 16, Senegal 1-1 Cote d'Ivoire, extra time and penalties",
}
CHAOS = Chaos(late_share=0.05, max_delay_ms=8_000, duplicate_share=0.01, seed=7)


def write(path, rows) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for match_id, label in MATCHES.items():
        msgs = messages(match_id)
        delivered = chaos(msgs, CHAOS) + [end_marker(msgs)]
        expected = reference_snapshots(delivered)
        write(FIXTURES / f"{match_id}.input.jsonl.gz", delivered)
        write(FIXTURES / f"{match_id}.expected.jsonl.gz", expected)
        print(f"{label}: {len(delivered):,} messages in, {len(expected)} snapshots out, final score "
              f"{expected[-1]['score']} (shoot-out {expected[-1]['shootout']})")


if __name__ == "__main__":
    main()
