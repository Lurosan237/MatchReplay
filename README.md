# Match Replay

Real football matches replayed as live data streams: live win probability, "what if" simulation
of any moment, and a 3D view of the match. Showcase: the Africa Cup of Nations 2023, including
Cameroon's four matches.

> **Status: work in progress.** Layers 1 to 4 are done: the data, the streaming pipeline, the 2D
> replay in the browser, and live win probability with "what if". See the roadmap below.

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

## The replay in the browser

`site/` is a static website: pick any of the 558 matches and it plays again as if it were live.
The ball moves from action to action on a 2D pitch, passes and carries leave short trails, every
shot stays on the pitch sized by its expected goals, and the side panel shows the live numbers:
score, xG, shots, share of passes, box entries, cards, an xG-over-time chart and the match feed.
Under the pitch, a timeline shows momentum (home above the line, away below) and the goals; click
or drag it to jump. Speeds from real time to 240x, and "next goal".

The numbers are the pipeline's own snapshots (the same reference the Flink job is tested against),
so the page needs no server and is published on GitHub Pages. Its data files are generated in the
Pages workflow from StatsBomb's open data (`python -m matchreplay.web_export`), never committed.

**Live mode.** With the pipeline running, `python -m matchreplay.gateway` reads what the feed
delivered (`match-events`) and what the Flink job committed (`match-state`, read_committed) and
pushes both to browsers over WebSockets. `match.html?id=<match>&live=ws://localhost:8765` then
draws the match as it streams, late events landing in their right place. A browser that connects
mid-match first gets the story so far, then live messages, in order and never twice. The
end-to-end test checks that the gateway serves the crashed-and-recovered match down to its final
snapshot.

Tests: `npm test --prefix site` replays every AFCON 2023 match in the page's own model (final
scores, goals in the feed, timeline, clock, ball path); `tests/test_gateway.py` covers the gateway.

## Live win probability, and "what if"

Every snapshot now carries the chance of a home win, a draw and an away win
([model and evaluation](reports/winprob.md)). The model predicts how fast each team scores from
here: a Poisson regression on the game state (leading or trailing), red cards, the expected goals
each team has created and conceded so far, and whether the team is listed at home. Those goal
rates are then played forward minute by minute on the score difference, so they shift as the
score would (a team that equalises stops pushing as hard), giving exact probabilities with no
sampling. The phase is regulation, then extra time, where a draw means penalties.

Tested honestly: each of the 12 competitions is forecast by a model trained on the other 11.
Over all 558 matches it beats the same model without the match reading (score and clock only)
and it is well calibrated: when it says 75%, the home team wins about 77% of the time.

The same few lines run in three places and are tested to agree to the fourth decimal:
- **Python** (`winprob.py`): training, and the reference snapshots;
- **Java**, inside the Flink job (`WinProbability.java`): the pipeline emits win probability
  live, in the stream, and the parity and crash tests check it like every other number;
- **JavaScript**, in the browser (`site/js/winprob.js`).

**What if?** In the match feed, any goal, shot or red card can be flipped: the goal that didn't
go in, the miss that did, the red card never shown. The rest of the match is replayed with the
changed score (the dashed win-probability lines: how the odds would have read), and 5,000
endings are simulated from that moment with the same goal rates: the chances of each result, the
most likely final scores, and one simulated story of how it could have gone.

## Roadmap

- [x] Layer 1: ingest 12 competitions, typed tables, data report
- [x] Layer 2: streaming replay pipeline (Java, Apache Flink, Kafka-compatible broker), parity
      and crash-recovery tests
- [x] Layer 3: 2D replay in the browser, published as a static site, plus a live mode over
      WebSockets
- [x] Layer 4: live win probability (Python, Flink and browser, tested to agree) and "what if"
      simulation
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
python -m matchreplay.winprob       # trains and evaluates the win-probability model (about 2 minutes)
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

The website, locally:

```powershell
python -m matchreplay.web_export --only afcon2023 wc2022   # or no option: all 558 matches
python -m http.server -d site 8000                          # then open localhost:8000
```

And live, with the pipeline above running:

```powershell
python -m matchreplay.gateway                                # ws://localhost:8765
# open localhost:8000/match.html?id=3922242&live=ws://localhost:8765
python -m matchreplay.replay --match 3922242                 # watch it arrive
```

## Credits

Data: Hudl StatsBomb open data. Free to use with credit to Hudl StatsBomb as the source.
