"""E2E check: a browser-like client connected to the gateway gets the pipeline's final snapshot."""
import asyncio
import json
import sys

import websockets

from matchreplay.stream import messages, reference_snapshots


async def main(match_id: int) -> None:
    final = reference_snapshots(messages(match_id))[-1]
    events = snaps = 0
    async with websockets.connect(f"ws://localhost:8765/match/{match_id}") as ws:
        while True:
            m = json.loads(await asyncio.wait_for(ws.recv(), 60))
            events += m["kind"] == "event"
            snaps += m["kind"] == "snap"
            if m["kind"] == "snap" and m["time"] == final["time"]:
                break
    ok = m["score"] == final["score"] and abs(m["h_xg"] - final["home_stats"]["xg"]) < 2e-4
    print(f"gateway: {events} events, {snaps} snapshots, final score {m['score']} (reference {final['score']})")
    sys.exit(0 if ok and snaps == len(reference_snapshots(messages(match_id))) else 1)


asyncio.run(main(int(sys.argv[1])))
