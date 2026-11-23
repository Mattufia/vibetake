# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Finding CC0 music (musicfind.py), with the network replaced by local files.

    uv run tests/test_musicfind.py

Checks: only CC0 music records are considered; a deleted track is dropped by its page; a minor
piece is dropped for a bright brief; a track already shown never comes back, and when nothing
is left the user gets one Pixabay search; the source of the chosen track lands in project.md.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
import tempfile
import wave
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import musicfind as mf  # noqa: E402


def piece(third: int) -> bytes:
    sr, beat = 22050, 0.5
    roots = [48, 53, 55, 48] * 8
    out = []
    for root in roots:
        t = np.arange(int(sr * beat * 2)) / sr
        env = 0.35 + 0.65 * np.exp(-3 * t)
        out.append(env * sum(np.sin(2 * np.pi * 440 * 2 ** ((root + 12 * o + n - 69) / 12) * t)
                             for n in (0, third, 7) for o in (0, 1)) / 6)
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1), w.setsampwidth(2), w.setframerate(sr)
        w.writeframes((np.concatenate(out) * 26000).astype(np.int16).tobytes())
    return b.getvalue()


def record(n: int, **over) -> dict:
    return {"id": f"r{n}", "title": f"Track {n}", "creator": "someone", "license": "cc0", "source": "freesound",
            "mature": False, "duration": 32000, "tags": [{"name": "music"}],
            "url": f"https://cdn.freesound.org/previews/{n}.mp3",
            "foreign_landing_url": f"https://freesound.org/people/x/sounds/{n}", **over}


CC0 = b'<a href="https://creativecommons.org/publicdomain/zero/1.0/">Creative Commons 0</a>'
RECORDS = [record(1), record(2), record(3), record(4, license="by"), record(5, tags=[{"name": "field-recording"}])]
AUDIO = {"1": piece(4), "2": piece(3)}


def fake_fetch(url: str, limit: int = mf.MAX_BYTES, sample: bool = False) -> bytes:
    if url.startswith(mf.API):
        return json.dumps({"results": RECORDS}).encode()
    if "/sounds/" in url:
        return b"Deleted sound" if url.endswith("/3") else CC0
    return AUDIO[url.rsplit("/", 1)[1].split(".")[0]]


def find() -> tuple[str, dict]:
    a = argparse.Namespace(query="upbeat", duration=30.0, tone="bright", bpm=None, keep=3)
    out = io.StringIO()
    with redirect_stdout(out):
        mf.cmd_find(a)
    return out.getvalue(), json.loads((mf.FOLDER / "candidates.json").read_text(encoding="utf-8"))


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="vt_musicfind_"))
    os.chdir(tmp)
    mf.fetch, mf.time.sleep = fake_fetch, lambda _: None
    first, d = find()
    why = {x["title"]: " ".join(x["why"]) for x in d["dropped"]}
    chosen = d["keep"][0] if d["keep"] else {}
    with redirect_stdout(io.StringIO()):
        if chosen:
            mf.cmd_use(argparse.Namespace(file=chosen["file"], url=None, license=None, title=None))
    project = Path("project.md").read_text(encoding="utf-8") if Path("project.md").exists() else ""
    second, again = find()
    checks = {
        "the major piece is the one kept": [c["title"] for c in d["keep"]] == ["Track 1"],
        "minor piece dropped for a bright brief": "minor" in why.get("Track 2", ""),
        "deleted track dropped by its page": "CC0" in why.get("Track 3", ""),
        "non-CC0 and non-music never measured": not {"Track 4", "Track 5"} & set(why),
        "source recorded in project.md": "sounds/1" in project and "CC0 1.0" in project,
        "a track already shown doesn't come back": "Track 1" not in [c["title"] for c in again["keep"]],
        "nothing left: one Pixabay search": "pixabay.com/music/search/upbeat" in second,
        "page with another license isn't CC0": not mf.page_is_cc0(
            "creativecommons.org/publicdomain/zero/ creativecommons.org/licenses/by/4.0/"),
    }
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    n = sum(checks.values())
    print(f"{n}/{len(checks)} pass · {tmp}")
    os.chdir(SCRIPTS)
    if n == len(checks):
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if n == len(checks) else 1)


if __name__ == "__main__":
    main()
