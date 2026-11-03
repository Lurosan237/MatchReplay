# Match Replay

Real football matches replayed as live data streams: live win probability, "what if" simulation
of any moment, and a 3D view of the match. Showcase: the Africa Cup of Nations 2023, including
Cameroon's four matches.

> **Status: work in progress.** Layers 1 (data) and 2 (streaming pipeline) are done. See the
> roadmap below.

## Data

558 matches from 12 competitions, all from
[Hudl StatsBomb open data](https://github.com/statsbomb/open-data): AFCON 2023, World Cups 2018
and 2022, Euro 2020 and 2024, Copa América 2024, Women's World Cups 2019 and 2023, Women's Euro
2022 and 2025, Bundesliga 2023/24 and Ligue 1 2022/23. About 2 million events, roughly 3,500
per match: every pass, carry, shot, tackle and pressure, with the match clock and pitch
coordinates.

Where players stood:
- every shot in every match carries a freeze frame of the players around it;
- 358 matches also have "360" frames: every visible player at almost every event.

**Every final score is rebuilt from the event stream and matches the official result, for all
558 matches** ([data report](reports/data.md)). Goals come from scoring shots and own goals;
penalty shoot-outs don't count. The live numbers the pipeline will compute depend on this.

## The streaming pipeline

```
replay (Python) --> match-events --> Flink job (Java) --> match-state --> verify / web page
   chaotic feed      (Kafka API)      event time,           (Kafka API)
   late, duplicated                   clock per match,  \-> match-late
                                      exactly-once
```

A match is replayed into a Kafka-compatible broker (Redpanda) the way a real feed delivers it:
5% of events late by up to 8 seconds, 1% delivered twice. The Flink job keeps one live state
per match (score, shots, xG, passes, box entries, cards, momentum) and emits a snapshot for
every 5 seconds of match time:

- **Event time, not arrival time.** Events are buffered per 5-second window of match time; a
  window is applied only when the match's clock (its latest event time minus the allowed delay)
  passes its end, so a late event still lands in the right place.
- **A clock per match, not one watermark.** Flink's watermark is one clock for the whole
  stream, but every match starts at minute 0: once one match ended, the next would be entirely
  "late". The crash test caught this. Each match now keeps its own clock in keyed state, and a
  test replays two matches one after the other and interleaved.
- **Duplicates** are recognised by event id; **events later than allowed** go to a side output
  instead of silently changing the numbers.
- **Exactly-once.** State and Kafka offsets are checkpointed every 5 seconds and the output is
  written in Kafka transactions, so a crash never loses or double-counts a goal.

**How it is tested.** `matchreplay/stream.py` computes the same snapshots the simple way, from
the events in perfect order: it is the oracle.
- *Parity test (CI, every push):* the Flink job, fed two real AFCON matches as a chaotic feed,
  must produce exactly the reference snapshots. One of them goes to extra time and penalties.
- *End-to-end test (CI, every push):* a real broker and a real Flink cluster in Docker. One
  match replayed as fast as possible; then a second one with the Flink task manager **killed in
  the middle of the match** and restarted. Both must come out identical to the reference
  (they do: 615/615 and 919/919 snapshots, the job restored from its last checkpoint).

## Roadmap

- [x] Layer 1: ingest 12 competitions, typed tables, data report
- [x] Layer 2: streaming replay pipeline (Java, Apache Flink, Kafka-compatible broker), parity
      and crash-recovery tests
- [ ] Layer 3: 2D pitch view in the browser
- [ ] Layer 4: live win probability and "what if" simulation
- [ ] Layer 5: 3D view with real player movement (World Cup 2022 tracking data)
- [ ] Layer 6: reconstructed movement for the other competitions
- [ ] Layer 7: polish, and Cameroon's matches as the story

## Running it

Python 3.10+.

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -e .
python -m matchreplay.ingest        # downloads about 4 GB of JSON, keeps 300 MB of tables
python -m matchreplay.datareport
pytest
```

The pipeline (Docker and Java 17 + Maven):

```powershell
mvn -f pipeline/pom.xml package               # builds the Flink job, runs the parity test
docker compose up -d                          # broker + Flink cluster (web UI: localhost:8081)
docker compose --profile submit run --rm submit
pip install -r requirements-stream.txt
python -m matchreplay.replay --match 3922238  # Nigeria-Cameroon, AFCON 2023, at 60x speed
python -m matchreplay.verify --match 3922238
```

## Credits

Data: Hudl StatsBomb open data. Free to use with credit to Hudl StatsBomb as the source.
