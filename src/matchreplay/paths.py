from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"           # untouched StatsBomb JSON (cache)
MATCHES = DATA / "matches"   # one compact folder per competition: events, frames, matches
REPORTS = ROOT / "reports"
