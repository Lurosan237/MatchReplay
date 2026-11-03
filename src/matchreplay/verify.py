"""Check what the pipeline wrote to Kafka against the reference, snapshot by snapshot.

    python -m matchreplay.verify --match 3922238

Reads the output topic with read_committed isolation (so only what the job committed counts),
waits until the final snapshot arrives or nothing new comes for --idle seconds, then compares
with stream.reference_snapshots. Exits 1 on any difference: missing, extra or changed snapshots
mean the job lost or double-counted something.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time

from matchreplay import winprob
from matchreplay.stream import messages, reference_snapshots


def same(a, b, tol: float = 1.5e-4) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return isinstance(a, int | float) and isinstance(b, int | float) and math.isclose(a, b, abs_tol=tol)
    if isinstance(a, dict):
        return isinstance(b, dict) and a.keys() == b.keys() and all(same(a[k], b[k], tol) for k in a)
    if isinstance(a, list):
        return isinstance(b, list) and len(a) == len(b) and all(same(x, y, tol) for x, y in zip(a, b, strict=True))
    return a == b


def compare(expected: list[dict], actual: list[dict]) -> list[str]:
    problems = []
    if len(actual) != len(expected):
        problems.append(f"{len(actual)} snapshots, expected {len(expected)}")
    by_time = {s["time"]: s for s in actual}
    if len(by_time) != len(actual):
        problems.append(f"{len(actual) - len(by_time)} duplicate snapshots")
    for e in expected:
        a = by_time.get(e["time"])
        if a is None:
            problems.append(f"missing snapshot at {e['time']} ms")
        elif not same(e, a):
            problems.append(f"snapshot at {e['time']} ms differs: expected score {e['score']}, got {a['score']}")
        if len(problems) > 10:
            break
    return problems


def main(argv: list[str] | None = None) -> None:
    from confluent_kafka import Consumer

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--match", type=int, required=True)
    parser.add_argument("--bootstrap", default="localhost:19092")
    parser.add_argument("--topic", default="match-state")
    parser.add_argument("--idle", type=float, default=90.0)
    args = parser.parse_args(argv)

    expected = reference_snapshots(messages(args.match), model=winprob.load())
    consumer = Consumer({"bootstrap.servers": args.bootstrap, "group.id": f"verify-{time.time_ns()}",
                         "auto.offset.reset": "earliest", "isolation.level": "read_committed"})
    consumer.subscribe([args.topic])
    actual, last_new = [], time.monotonic()
    while time.monotonic() - last_new < args.idle:
        msg = consumer.poll(1.0)
        if msg is None or msg.error():
            continue
        snap = json.loads(msg.value())
        if snap["match_id"] == args.match:
            actual.append(snap)
            last_new = time.monotonic()
            if snap["time"] == expected[-1]["time"]:
                break
    consumer.close()
    problems = compare(expected, actual)
    final = actual[-1] if actual else None
    print(f"{len(actual)} snapshots received, {len(expected)} expected; final score "
          f"{final['score'] if final else '-'} (reference {expected[-1]['score']})")
    if problems:
        print("MISMATCH\n  " + "\n  ".join(problems))
        sys.exit(1)
    print("OK: identical to the reference, nothing lost or counted twice")


if __name__ == "__main__":
    main()
