"""Download PFF FC's World Cup 2022 data straight from their shared Google Drive folder.

    python -m matchreplay.pff_download <folder id> --what Metadata Rosters "Tracking Data"
    python -m matchreplay.pff_download <folder id> --list

The folder id is the last part of the link PFF sends once their form is filled in
(https://drive.google.com/drive/folders/<id>). Files land in data/raw/pff/, flattened:
metadata/<game>.json, rosters/<game>.json and <game>.jsonl.bz2 for tracking. Files already there
are skipped, so an interrupted download just resumes. --games limits tracking to some matches.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import time
from pathlib import Path

import requests

from matchreplay.paths import RAW

PFF = RAW / "pff"
LISTING = "https://drive.google.com/embeddedfolderview?id={}"
DOWNLOAD = "https://drive.usercontent.google.com/download?id={}&export=download&confirm=t"
ENTRY = re.compile(r'id="entry-([\w-]{10,})".*?flip-entry-title">([^<]*)<', re.S)
SESSION = requests.Session()
SESSION.headers["User-Agent"] = "matchreplay (portfolio project)"


def listing(folder_id: str) -> list[tuple[str, str, bool]]:
    """(id, name, is_folder) for every entry of a public Drive folder."""
    page = SESSION.get(LISTING.format(folder_id), timeout=60)
    page.raise_for_status()
    out = []
    for fid, name in ENTRY.findall(page.text):
        start = page.text.find(f'id="entry-{fid}"')
        chunk = page.text[max(0, start - 600):start + 1500]
        is_folder = f"/drive/folders/{fid}" in chunk
        out.append((fid, html.unescape(name).strip(), is_folder))
    return out


def walk(folder_id: str, path: str = "") -> list[tuple[str, str]]:
    """(id, path) of every file under a folder, recursively."""
    files = []
    for fid, name, is_folder in listing(folder_id):
        if is_folder:
            files += walk(fid, f"{path}{name}/")
        else:
            files.append((fid, f"{path}{name}"))
    return files


def target(path: str) -> Path | None:
    name = path.rsplit("/", 1)[-1]
    if path.startswith("Metadata/") and name.endswith(".json"):
        return PFF / "metadata" / name
    if path.startswith("Rosters/") and name.endswith(".json"):
        return PFF / "rosters" / name
    if path.startswith("Tracking Data/") and name.endswith(".jsonl.bz2"):
        return PFF / name
    return None


def download(fid: str, dest: Path, retries: int = 5) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(retries):
        try:
            with SESSION.get(DOWNLOAD.format(fid), stream=True, timeout=120) as r:
                r.raise_for_status()
                if "text/html" in r.headers.get("content-type", ""):
                    raise RuntimeError("Drive answered with a web page instead of the file (quota or permissions)")
                with open(part, "wb") as f:
                    for block in r.iter_content(1 << 20):
                        f.write(block)
            part.replace(dest)
            return
        except Exception as e:  # noqa: BLE001
            print(f"  retry {attempt + 1} for {dest.name}: {e}", flush=True)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"could not download {dest.name}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("folder")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--what", nargs="*", default=["Metadata", "Rosters", "Tracking Data"])
    parser.add_argument("--games", nargs="*", help="tracking for these PFF game ids only")
    parser.add_argument("--shard", help="i/n: the i-th of n shares of the tracking files (parallel CI jobs)")
    args = parser.parse_args(argv)
    files = walk(args.folder)
    if args.list:
        for fid, path in files:
            print(fid, path)
        return
    todo = [(fid, path, target(path)) for fid, path in files if path.split("/")[0] in args.what]
    todo = [(f, p, t) for f, p, t in todo if t is not None]
    if args.games:
        todo = [(f, p, t) for f, p, t in todo if not p.startswith("Tracking") or t.name.split(".")[0] in args.games]
    if args.shard:
        i, n = (int(v) for v in args.shard.split("/"))
        tracking = sorted(t.name for _, p, t in todo if p.startswith("Tracking"))
        mine = set(tracking[i::n])
        todo = [(f, p, t) for f, p, t in todo if not p.startswith("Tracking") or t.name in mine]
    if not todo:
        sys.exit("nothing to download: check the folder id and that it is shared with anyone who has the link")
    for i, (fid, path, dest) in enumerate(todo, 1):
        if dest.exists():
            continue
        started = time.perf_counter()
        download(fid, dest)
        print(f"{i}/{len(todo)} {path} ({dest.stat().st_size / 1e6:.1f} MB, {time.perf_counter() - started:.0f}s)",
              flush=True)


if __name__ == "__main__":
    main()
