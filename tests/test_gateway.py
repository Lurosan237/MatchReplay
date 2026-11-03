import asyncio
import json

import pytest

from matchreplay.gateway import Hub, web_event, web_snap

websockets = pytest.importorskip("websockets")

MSG = {"match_id": 1, "home": "Cameroon", "away": "Brazil", "event_id": "a", "index": 5, "event_time": 1000,
       "period": 1, "minute": 0, "second": 1, "type": "Pass", "team": "Brazil", "player": "Fred",
       "x": 30.0, "y": 20.0, "end_x": 50.0, "end_y": 10.0, "outcome": None, "xg": None, "card": None, "duration": 1.2}


def test_away_events_are_turned_around():
    e = web_event(MSG)
    assert (e["side"], e["x"], e["y"], e["ex"], e["ey"]) == (1, 90.0, 60.0, 70.0, 70.0)
    assert web_event(MSG | {"team": "Cameroon"})["x"] == 30.0


def test_snapshots_are_flattened():
    stats = {k: 1 for k in ("shots", "on_target", "xg", "passes", "passes_completed", "box_entries", "fouls",
                            "yellow", "red")}
    s = web_snap({"time": 5000, "minute": 0, "period": 1, "score": [0, 0], "shootout": [0, 0], "momentum": 0.1,
                  "possession_home": 0.5, "home_stats": stats, "away_stats": stats | {"xg": 0.4}})
    assert s["h_xg"] == 1 and s["a_xg"] == 0.4 and s["kind"] == "snap"


def test_a_late_browser_gets_the_story_so_far_then_live_messages_without_duplicates():
    async def scenario():
        hub = Hub()
        hub.loop = asyncio.get_running_loop()
        hub.add(1, {"kind": "meta", "match_id": 1, "home": "Cameroon", "away": "Brazil"})
        hub.add(1, web_event(MSG))
        hub.add(1, web_event(MSG))                       # the feed sent it twice
        async with websockets.serve(hub.serve, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            async with websockets.connect(f"ws://127.0.0.1:{port}/match/1") as ws:
                got = [json.loads(await ws.recv()) for _ in range(2)]
                hub.add(1, web_event(MSG | {"event_id": "b", "event_time": 2000}))
                got.append(json.loads(await ws.recv()))
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(ws.recv(), 0.2)
        return got

    got = asyncio.run(scenario())
    assert [m["kind"] for m in got] == ["meta", "event", "event"]
    assert [m["id"] for m in got[1:]] == ["a", "b"]
