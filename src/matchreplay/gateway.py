"""The live gateway: from the pipeline's Kafka topics to browsers, over WebSockets.

    python -m matchreplay.gateway                      # ws://localhost:8765, broker localhost:19092
    open site/match.html?id=3922238&live=ws://localhost:8765

It reads two topics:
    match-events   what the feed delivered (late, duplicated, out of order): drawn on the pitch
    match-state    the snapshots the Flink job committed (read_committed): the live numbers

and keeps everything per match in memory, so a browser that connects mid-match first gets the
story so far, then each new message as it arrives. A browser asks for one match by connecting
to ws://host:port/match/<id>.

Messages to the browser are JSON:
    {"kind": "meta",  "match_id", "home", "away"}
    {"kind": "event", ...}   one event, in the web view's coordinates (home attacks to the right)
    {"kind": "snap",  ...}   one pipeline snapshot, flattened like the exported files (h_xg, a_xg...)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import threading
from collections import defaultdict

STATS = ("shots", "on_target", "xg", "passes", "passes_completed", "box_entries", "fouls", "yellow", "red")


def flip(v: float | None, size: float) -> float | None:
    return None if v is None else round(size - v, 1)


def web_event(m: dict) -> dict:
    """A raw pipeline message -> the event the web view draws."""
    side = 0 if m["team"] == m["home"] else 1 if m["team"] == m["away"] else -1
    x, y, ex, ey = m.get("x"), m.get("y"), m.get("end_x"), m.get("end_y")
    if side == 1:
        x, y, ex, ey = flip(x, 120), flip(y, 80), flip(ex, 120), flip(ey, 80)
    r = lambda v: None if v is None else round(v, 1)  # noqa: E731
    return {"kind": "event", "id": m["event_id"], "index": m["index"], "t": m["event_time"], "period": m["period"],
            "minute": m["minute"], "second": m["second"], "type": m["type"], "side": side, "player": m.get("player"),
            "x": r(x), "y": r(y), "ex": r(ex), "ey": r(ey), "outcome": m.get("outcome"), "xg": m.get("xg"),
            "card": m.get("card"), "dur": m.get("duration")}


def web_snap(s: dict) -> dict:
    out = {"kind": "snap", "time": s["time"], "minute": s["minute"], "period": s["period"], "score": s["score"],
           "shootout": s["shootout"], "momentum": s["momentum"], "possession_home": s["possession_home"]}
    for k in STATS:
        out["h_" + k] = s["home_stats"][k]
        out["a_" + k] = s["away_stats"][k]
    return out


class Hub:
    """Everything seen so far, per match, and the browsers watching each match.

    All state changes happen on the event loop (the Kafka thread hands messages over), and each
    browser has its own queue: it gets the history first, then new messages, in order, never twice.
    """

    def __init__(self) -> None:
        self.history: dict[int, list[str]] = defaultdict(list)
        self.meta: dict[int, str] = {}
        self.seen: dict[int, set[str]] = defaultdict(set)
        self.queues: dict[int, set[asyncio.Queue]] = defaultdict(set)
        self.loop: asyncio.AbstractEventLoop | None = None

    def add_threadsafe(self, match_id: int, message: dict) -> None:
        """Called from the Kafka thread."""
        self.loop.call_soon_threadsafe(self.add, match_id, message)

    def add(self, match_id: int, message: dict) -> None:
        if message["kind"] == "meta":
            self.meta[match_id] = json.dumps(message, separators=(",", ":"))
            return
        if message["kind"] == "event":
            if message["id"] in self.seen[match_id]:
                return                                   # the feed delivered it twice
            self.seen[match_id].add(message["id"])
        text = json.dumps(message, separators=(",", ":"))
        self.history[match_id].append(text)
        for q in self.queues[match_id]:
            q.put_nowait(text)

    async def serve(self, ws) -> None:
        path = ws.request.path if hasattr(ws, "request") else ws.path
        try:
            match_id = int(path.rstrip("/").split("/")[-1])
        except ValueError:
            await ws.close(code=1008, reason="connect to /match/<id>")
            return
        q: asyncio.Queue = asyncio.Queue()
        for text in ([self.meta[match_id]] if match_id in self.meta else []) + self.history[match_id]:
            q.put_nowait(text)
        self.queues[match_id].add(q)

        async def pump() -> None:
            while True:
                await ws.send(await q.get())

        sender = asyncio.create_task(pump())
        try:
            await ws.wait_closed()                       # the browser went away
        finally:
            sender.cancel()
            self.queues[match_id].discard(q)


def consume(hub: Hub, bootstrap: str, events_topic: str, state_topic: str, stop: threading.Event) -> None:
    from confluent_kafka import Consumer

    def consumer(isolation: str) -> Consumer:
        return Consumer({"bootstrap.servers": bootstrap, "group.id": f"gateway-{isolation}-{id(hub)}",
                         "auto.offset.reset": "earliest", "isolation.level": isolation})

    events, states = consumer("read_uncommitted"), consumer("read_committed")
    metas: set[int] = set()
    events.subscribe([events_topic])
    states.subscribe([state_topic])
    while not stop.is_set():
        for c, is_state in ((events, False), (states, True)):
            for msg in c.consume(500, 0.05):
                if msg.error():
                    continue
                data = json.loads(msg.value())
                mid = int(data["match_id"])
                if mid not in metas:
                    metas.add(mid)
                    meta = {"kind": "meta", "match_id": mid, "home": data["home"], "away": data["away"]}
                    hub.add_threadsafe(mid, meta)
                if is_state:
                    hub.add_threadsafe(mid, web_snap(data))
                elif data.get("type") != "End":
                    hub.add_threadsafe(mid, web_event(data))
    events.close()
    states.close()


async def run(args) -> None:
    import websockets

    hub = Hub()
    hub.loop = asyncio.get_running_loop()
    stop = threading.Event()
    thread = threading.Thread(target=consume, args=(hub, args.bootstrap, args.events, args.state, stop), daemon=True)
    thread.start()
    async with websockets.serve(hub.serve, args.host, args.port):
        print(f"gateway on ws://{args.host}:{args.port}/match/<id>, reading {args.bootstrap}", flush=True)
        await asyncio.Future()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--bootstrap", default="localhost:19092")
    parser.add_argument("--events", default="match-events")
    parser.add_argument("--state", default="match-state")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    main()
