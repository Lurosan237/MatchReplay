import json

import pandas as pd

from matchreplay.datareport import goals_from_events
from matchreplay.ingest import clock_seconds, event_row, shot_frame_rows

SHOT = {
    "id": "s1", "index": 81, "period": 1, "timestamp": "00:02:08.023", "minute": 2, "second": 8,
    "type": {"name": "Shot"}, "possession": 4, "possession_team": {"name": "Congo DR"},
    "play_pattern": {"name": "From Counter"}, "team": {"name": "Congo DR"},
    "player": {"id": 35684, "name": "Meschak Elia"}, "position": {"name": "Right Wing"},
    "location": [105.1, 55.8], "duration": 0.72,
    "shot": {"statsbomb_xg": 0.06, "end_location": [120.0, 44.3, 6.0], "type": {"name": "Open Play"},
             "outcome": {"name": "Off T"},
             "freeze_frame": [{"location": [115.8, 43.9], "player": {"id": 1, "name": "Keeper"},
                               "position": {"name": "Goalkeeper"}, "teammate": False},
                              {"location": [99.0, 40.0], "player": {"id": 2, "name": "Mate"},
                               "position": {"name": "Left Wing"}, "teammate": True}]},
}


def test_clock_seconds():
    assert clock_seconds("00:02:08.023") == 128.023
    assert clock_seconds("01:00:00.000") == 3600


def test_shot_event_is_flattened_without_losing_details():
    row = event_row(42, SHOT)
    assert (row["match_id"], row["type"], row["team"], row["player"]) == (42, "Shot", "Congo DR", "Meschak Elia")
    assert (row["x"], row["y"], row["end_x"], row["end_y"], row["end_z"]) == (105.1, 55.8, 120.0, 44.3, 6.0)
    assert row["xg"] == 0.06 and row["outcome"] == "Off T" and row["clock"] == 128.023
    extra = json.loads(row["extra"])
    assert "freeze_frame" not in extra and extra["type"]["name"] == "Open Play"


def test_events_without_details_still_work():
    row = event_row(1, {"id": "e", "index": 1, "period": 1, "timestamp": "00:00:00.000", "minute": 0, "second": 0,
                        "type": {"name": "Starting XI"}, "team": {"name": "Cameroon"}})
    assert row["x"] is None and row["outcome"] is None and row["extra"] is None


def test_shot_freeze_frames_include_the_shooter_and_mark_the_keeper():
    rows = shot_frame_rows(42, [SHOT])
    assert len(rows) == 3
    shooter = [r for r in rows if r["actor"]]
    assert len(shooter) == 1 and shooter[0]["player_id"] == 35684
    assert [r["keeper"] for r in rows if not r["actor"]] == [True, False]


def test_final_score_is_rebuilt_from_events_ignoring_the_shootout():
    matches = pd.DataFrame([{"match_id": 1, "home": "Cameroon", "away": "Nigeria", "home_score": 1, "away_score": 2}])
    events = pd.DataFrame([
        {"match_id": 1, "period": 1, "type": "Shot", "outcome": "Goal", "team": "Nigeria"},
        {"match_id": 1, "period": 2, "type": "Shot", "outcome": "Saved", "team": "Cameroon"},
        {"match_id": 1, "period": 2, "type": "Own Goal For", "outcome": None, "team": "Cameroon"},
        {"match_id": 1, "period": 2, "type": "Shot", "outcome": "Goal", "team": "Nigeria"},
        {"match_id": 1, "period": 5, "type": "Shot", "outcome": "Goal", "team": "Cameroon"},
    ])
    check = goals_from_events(events, matches)
    assert (check.loc[0, "home_rebuilt"], check.loc[0, "away_rebuilt"]) == (1, 2)
