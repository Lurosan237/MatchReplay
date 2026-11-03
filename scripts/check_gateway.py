"""E2E check: a browser-like client connected to the gateway gets the pipeline's final snapshot."""
import asyncio
import json
import sys

import websockets

from matchreplay import winprob
from matchreplay.stream import messages, reference_snapshots


async def main(match_id: int) -> None:
    msgs = messages(match_id)
    expected = reference_snapshots(msgs, model=winprob.load())
    final = expected[-1]
    events = snaps = 0
    last = None
    async with websockets.connect(f"ws://localhost:8765/match/{match_id}") as ws:
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), 60))
            events += m["kind"] == "event"
            if m["kind"] == "snap":
                snaps += 1
                last = m
            if events == len(msgs) and last and last["time"] == final["time"]:
                break
    ok = last["score"] == final["score"] and abs(last["h_xg"] - final["home_stats"]["xg"]) < 2e-4
    print(f"gateway: {events} events (each once, of {len(msgs)}), {snaps} snapshots (of {len(expected)}), "
          f"final score {last['score']} (reference {final['score']})")
    sys.exit(0 if ok and snaps == len(expected) else 1)


asyncio.run(main(int(sys.argv[1])))
