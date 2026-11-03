# Match Replay

Real football matches replayed as live data streams: live win probability, "what if" simulation
of any moment, and a 3D view of the match. Showcase: the Africa Cup of Nations 2023, including
Cameroon's four matches.

> **Status: work in progress.** Layer 1 (data) is done. See the roadmap below.

## Data

558 matches from 12 competitions, all from
[Hudl StatsBomb open data](https://github.com/statsbomb/open-data): AFCON 2023, World Cups 2018
and 2022, Euro 2020 and 2024, Copa América 2024, Women's World Cups 2019 and 2023, Women's Euro
2022 and 2025, Bundesliga 2023/24 and Ligue 1 2022/23. About 2 million events, roughly 3,500
per match: every pass, carry, shot, tackle and pressure, with the match clock and pitch
coordinates.

Where players stood:
- every shot in every match carries a freeze frame of the players around it;
- 357 matches also have "360" frames: every visible player at almost every event.

**Every final score is rebuilt from the event stream and matches the official result, for all
558 matches** ([data report](reports/data.md)). Goals come from scoring shots and own goals;
penalty shoot-outs don't count. The live numbers the pipeline will compute depend on this.

## Roadmap

- [x] Layer 1: ingest 12 competitions, typed tables, data report
- [ ] Layer 2: streaming replay pipeline (Java, Apache Flink, Kafka-compatible broker)
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

## Credits

Data: Hudl StatsBomb open data. Free to use with credit to Hudl StatsBomb as the source.
