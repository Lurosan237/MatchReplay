"""Replay a real match into Kafka, as a live feed would deliver it.

    python -m matchreplay.replay --match 3922238                 # Nigeria-Cameroon at 60x speed
    python -m matchreplay.replay --match 3922238 --speed 0       # as fast as possible
    python -m matchreplay.replay --match 3922238 --no-chaos      # perfect order, no duplicates

Each message is delivered at its arrival time in the chaotic feed (stream.chaos), scaled down by
--speed: at 60x, a 95-minute match takes about a minute and a half. An end-of-match marker
follows the last event so the pipeline can close the final windows.
"""

from __future__ import annotations

import argparse
import json
import time

from matchreplay.stream import Chaos, chaos, end_marker, messages


def schedule(match_id: int, use_chaos: bool = True, seed: int = 7) -> list[tuple[int, dict]]:
    """(arrival time in match ms, message) in delivery order."""
    msgs = messages(match_id)
    delivered = chaos(msgs, Chaos(seed=seed)) if use_chaos else msgs
    # Arrival time = the latest event time seen so far: a late message arrives "now", not at its own time
    out, clock = [], 0
    for m in delivered:
        clock = max(clock, m["event_time"])
        out.append((clock, m))
    end = end_marker(msgs)
    out.append((end["event_time"], end))
    return out


def main(argv: list[str] | None = None) -> None:
    from confluent_kafka import Producer

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--match", type=int, required=True)
    parser.add_argument("--bootstrap", default="localhost:19092")
    parser.add_argument("--topic", default="match-events")
    parser.add_argument("--speed", type=float, default=60.0, help="times real time; 0 = as fast as possible")
    parser.add_argument("--no-chaos", action="store_true")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)

    plan = schedule(args.match, use_chaos=not args.no_chaos, seed=args.seed)
    producer = Producer({"bootstrap.servers": args.bootstrap, "enable.idempotence": True, "linger.ms": 5})
    started, first = time.monotonic(), plan[0][0]
    for i, (arrival, m) in enumerate(plan):
        if args.speed > 0:
            wait = (arrival - first) / 1000 / args.speed - (time.monotonic() - started)
            if wait > 0:
                time.sleep(wait)
        producer.produce(args.topic, key=str(m["match_id"]), value=json.dumps(m, separators=(",", ":")))
        if i % 500 == 0:
            producer.poll(0)
            print(f"{i:>5}/{len(plan)}  minute {m['minute']:>3}", flush=True)
    producer.flush(30)
    print(f"sent {len(plan)} messages for match {args.match}")


if __name__ == "__main__":
    main()
