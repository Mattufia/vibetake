# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Real music for the edit, free for commercial use with no credit: found, measured, shortlisted.

    uv run musicfind.py find --query "upbeat,happy background" --duration 42.6 --tone bright --bpm 90-135
    uv run musicfind.py check --file music/track.mp3 --duration 42.6 --tone bright --bpm 90-135
    uv run musicfind.py use --file <track> [--url <page> --license "Pixabay Content License" --title "..."]

find searches Openverse for CC0 tracks, confirms CC0 on each track's own page, measures them against
the video and keeps the few that fit in 02-work/music/candidates/ (candidates.json has why). A track
already shown is never offered again, so searching again brings new ones.
check measures a track the user downloaded, before it's mixed. use records its source in project.md.
Only words about the music are sent to the search: never the project's name, transcript or brief.
Runs on the CPU. Measuring rules tracks out; only a person can say one sounds good.
"""
from __future__ import annotations

import argparse
import datetime
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _common import die, tool  # noqa: E402
from audio import find_beats  # noqa: E402

API = "https://api.openverse.org/v1/audio/"
AGENT = {"User-Agent": "video-editing-tool/1.0"}
FOLDER = Path("02-work/music/candidates")
MAX_BYTES = 30_000_000
MAX_TRIES = 14       # tracks measured per search
SAMPLE_BYTES = 500_000   # ~30 s at 128 kbps: the CDN is slow, so only the chosen tracks come down whole
NOT_MUSIC = ("field-recording", "fieldrecording", "ambience", "ambient-noise", "sfx", "sound-effect", "foley", "birds",
             "rain", "noise", "speech", "voice", "vocal", "vocals", "singing", "singer", "sung", "choir", "rap", "lyrics",
             "spoken", "talking", "podcast", "jingle-voice")
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])   # Krumhansl-Kessler
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
NOTES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def fetch(url: str, limit: int = MAX_BYTES, sample: bool = False) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers=AGENT), timeout=30) as r:
        data = r.read(limit + 1)
    if sample:
        return data[:limit]
    if len(data) > limit:
        raise ValueError("file too large")
    return data


def usable(r: dict, duration: float) -> bool:
    """A record worth downloading: CC0, from Freesound, long enough, and tagged as music rather than a sound or a voice."""
    words = {t.get("name", "").lower().replace(" ", "-") for t in r.get("tags") or []}
    words |= set(re.findall(r"[a-z-]+", (r.get("title") or "").lower()))
    host = urllib.parse.urlparse(r.get("url") or "")
    seconds = (r.get("duration") or 0) / 1000
    return (r.get("license") == "cc0" and r.get("source") == "freesound" and not r.get("mature")
            and host.scheme == "https" and host.hostname == "cdn.freesound.org"
            and max(20, 0.6 * duration) <= seconds <= 600 and not words & set(NOT_MUSIC))


def page_is_cc0(html: str) -> bool:
    """The track's own page says CC0, and no other Creative Commons license."""
    return "creativecommons.org/publicdomain/zero/" in html and "creativecommons.org/licenses/" not in html


def measure(path: Path) -> dict:
    """What can be known about a track without taste: key and mode, tempo, how much it's music, where it starts."""
    sr, n, hop = 22050, 4096, 2048
    raw = subprocess.run([tool("ffmpeg"), "-v", "error", "-t", "120", "-i", str(path), "-ac", "1", "-ar", str(sr),
                          "-f", "f32le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32)
    if len(x) < sr * 4:
        raise ValueError("too short to measure")
    idx = np.arange(n)[None, :] + hop * np.arange((len(x) - n) // hop)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * np.hanning(n), axis=1))
    freqs = np.fft.rfftfreq(n, 1 / sr)
    rms = np.sqrt((x[idx] ** 2).mean(1))
    loud = float(np.percentile(rms, 95)) or 1e-9
    db = 20 * np.log10(rms / loud + 1e-9)
    band = (freqs >= 65) & (freqs <= 2000)
    pcs = np.round(12 * np.log2(freqs[band] / 440)).astype(int) % 12            # 0 = A
    power = (spec[:, band] ** 2).sum(0)
    chroma = np.bincount(pcs, power, 12)
    chroma = np.roll(chroma, 9)                                                 # 0 = C
    fit = [[float(np.corrcoef(chroma, np.roll(p, k))[0, 1]) for k in range(12)] for p in (MAJOR, MINOR)]
    best_major, best_minor = max(fit[0]), max(fit[1])
    mode = "major" if best_major >= best_minor else "minor"
    tonal_band = spec[:, (freqs >= 100) & (freqs <= 4000)] + 1e-9
    flat = np.exp(np.log(tonal_band).mean(1)) / tonal_band.mean(1)
    total = (spec ** 2).sum()
    start = int(np.argmax(db > -20)) * hop / sr
    return {"key": NOTES[int(np.argmax(fit[mode == "minor"]))], "mode": mode, "mode_margin": round(abs(best_major - best_minor), 3),
            "bpm": find_beats(path)["bpm"], "tonal": round(float(1 - np.median(flat)), 3),
            "voice_band": round(float((spec[:, (freqs >= 1000) & (freqs <= 4000)] ** 2).sum() / (total or 1)), 3),
            "active": round(float((db > -24).mean()), 3), "in": round(start, 2),
            "seconds": round(float(subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries", "format=duration",
                                                   "-of", "csv=p=0", str(path)], capture_output=True, text=True).stdout or 0), 2)}


def objections(m: dict, tone: str, bpm: tuple[float, float] | None, duration: float | None) -> list[str]:
    out = []
    if m["tonal"] < 0.55:
        out.append("more noise than notes")
    if tone == "bright" and m["mode"] == "minor" and m["mode_margin"] > 0.04:
        out.append(f"sounds minor ({m['key']}m) and the brief is bright")
    if tone == "dark" and m["mode"] == "major" and m["mode_margin"] > 0.04:
        out.append(f"sounds major ({m['key']}) and the brief is dark")
    if bpm and not any(bpm[0] <= m["bpm"] * k <= bpm[1] for k in (0.5, 1, 2)):      # half and double time feel the same
        out.append(f"{m['bpm']:g} bpm is outside {bpm[0]:g}–{bpm[1]:g}")
    if m["voice_band"] > 0.3:
        out.append(f"{100 * m['voice_band']:.0f}% of its energy sits where the voice is")
    if m["active"] < 0.7:
        out.append("long silences or big drops")
    if duration and m["seconds"] - m["in"] < 0.6 * duration:
        out.append(f"only {m['seconds'] - m['in']:.0f} s usable for {duration:g} s")
    return out


def fit_cost(m: dict, bpm: tuple[float, float] | None) -> float:
    """Lower is better: leaves room for the voice, stays active, sits inside the tempo."""
    center = sum(bpm) / 2 if bpm else m["bpm"]
    off = min(abs(m["bpm"] * k - center) for k in (0.5, 1, 2)) / center
    return m["voice_band"] + (1 - m["active"]) * 0.5 + off * 0.3


def parse_bpm(spec: str | None) -> tuple[float, float] | None:
    if not spec:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)", spec.strip())
    if not m or float(m.group(1)) >= float(m.group(2)):
        die(f"--bpm: '{spec}' — use a range, e.g. 90-135")
    return float(m.group(1)), float(m.group(2))


def pixabay_hint(queries: list[str], tone: str, bpm) -> str:
    q = urllib.parse.quote(queries[0])
    wanted = f"{tone}, " if tone != "any" else ""
    tempo = f"{bpm[0]:g}–{bpm[1]:g} BPM, " if bpm else ""
    return (f"nothing fit. Ask the user for one track from https://pixabay.com/music/search/{q}/ "
            f"({wanted}{tempo}instrumental), into music/, then: musicfind.py check --file music/<track>")


def cmd_find(a) -> None:
    queries = [q.strip() for q in a.query.split(",") if q.strip()] or die("--query: at least one search")
    bpm = parse_bpm(a.bpm)
    FOLDER.mkdir(parents=True, exist_ok=True)
    listed = FOLDER / "candidates.json"
    shown = set(json.loads(listed.read_text(encoding="utf-8")).get("shown", [])) if listed.exists() else set()
    seen, records = set(), []
    for q in queries:
        url = API + "?" + urllib.parse.urlencode({"q": q, "license": "cc0", "page_size": 20})
        try:                                # category=music returns nothing with cc0: Freesound's tags do that job
            found = json.loads(fetch(url))["results"]
        except (urllib.error.URLError, ValueError, KeyError) as e:
            print(f"search '{q}' failed: {e}")
            continue
        for r in found:
            if r["id"] not in seen and r["foreign_landing_url"] not in shown and usable(r, a.duration):
                seen.add(r["id"])
                records.append(r)
        time.sleep(1.1)                     # anonymous API: stay well under its limit
    keep, dropped = [], []
    for r in records[:MAX_TRIES]:
        sid = re.search(r"/sounds/(\d+)", r["foreign_landing_url"])
        path = FOLDER / f"freesound-{sid.group(1) if sid else r['id']}.mp3"
        part = path.with_suffix(".sample.mp3")
        m = {}
        try:                                # the license first: it's the cheap check, and deleted tracks fail it
            if not page_is_cc0(fetch(r["foreign_landing_url"], 2_000_000).decode("utf-8", "replace")):
                no = ["its page doesn't confirm CC0 (deleted or changed)"]
            else:
                part.write_bytes(fetch(r["url"], SAMPLE_BYTES, sample=True))
                m = {**measure(part), "seconds": round(r["duration"] / 1000, 2)}
                no = objections(m, a.tone, bpm, a.duration)
        except (urllib.error.URLError, ValueError, subprocess.CalledProcessError, OSError) as e:
            no = [f"couldn't read it ({type(e).__name__})"]
        part.unlink(missing_ok=True)
        if no:
            dropped.append({"title": r.get("title"), "why": no})
        else:
            keep.append({"file": path.as_posix(), "title": r.get("title"), "creator": r.get("creator"), "audio": r["url"],
                         "url": r["foreign_landing_url"], "license": "CC0 1.0", **m})
        time.sleep(0.4)
    keep.sort(key=lambda c: fit_cost(c, bpm))
    chosen = []
    for c in keep:
        if len(chosen) == a.keep:
            break
        try:
            Path(c["file"]).write_bytes(fetch(c["audio"]))
            whole = measure(Path(c["file"]))     # a sample can read a minor piece as its relative major
            no = objections(whole, a.tone, bpm, a.duration)
        except (urllib.error.URLError, ValueError, subprocess.CalledProcessError, OSError) as e:
            no = [f"download failed ({type(e).__name__})"]
        if no:
            Path(c["file"]).unlink(missing_ok=True)
            dropped.append({"title": c["title"], "why": no})
        else:
            chosen.append({**c, **whole})
    keep = chosen
    (FOLDER / "candidates.json").write_text(json.dumps({"keep": keep, "dropped": dropped, "shown": sorted(shown | {c["url"] for c in keep})}, indent=1, ensure_ascii=False),
                                            encoding="utf-8")
    print(f"{len(records)} found · {min(len(records), MAX_TRIES)} measured · {len(keep)} fit")
    for c in keep:
        print(f"  {c['file']}  {c['title']} · {c['key']} {c['mode']} · {c['bpm']:g} bpm · starts {c['in']:g} s · {c['url']}")
    if not keep:
        print(pixabay_hint(queries, a.tone, bpm))


def cmd_check(a) -> None:
    f = Path(a.file)
    if not f.exists():
        die(f"{f} doesn't exist")
    try:
        m = measure(f)
    except (ValueError, subprocess.CalledProcessError) as e:
        die(f"{f.name}: can't measure it ({e})")
    print(f"{f.name}: {m['key']} {m['mode']} · {m['bpm']:g} bpm · {m['seconds']:g} s · starts {m['in']:g} s")
    no = objections(m, a.tone, parse_bpm(a.bpm), a.duration)
    print("\n".join(f"warning: {n}" for n in no) or "fits: no warnings. Listen to it against the video.")


def cmd_use(a) -> None:
    f = Path(a.file)
    if not f.exists():
        die(f"{f} doesn't exist")
    listed = FOLDER / "candidates.json"
    known = next((c for c in (json.loads(listed.read_text(encoding="utf-8"))["keep"] if listed.exists() else [])
                  if Path(c["file"]) == f), {})
    title, url, lic = a.title or known.get("title") or f.stem, a.url or known.get("url"), a.license or known.get("license")
    if not url or not lic:
        die("use: a track from outside the shortlist needs --url (its page) and --license: that's the proof if a claim arrives")
    p = Path("project.md")
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    line = f"- {f.name}: \"{title}\" · {url} · {lic} · checked {datetime.date.today().isoformat()}\n"
    head = "## Music sources\n"
    if head in text:
        text = text.replace(head, head + line, 1)
    else:
        text = text.rstrip() + ("\n\n" if text else "") + head + line
    p.write_text(text, encoding="utf-8")
    print(f"recorded in project.md. Then: audio.py music --file {f.as_posix()}" + (f" --in {known['in']:g}" if known.get("in") else ""))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("find", "check"):
        c = sub.add_parser(name)
        c.add_argument("--duration", type=float, required=name == "find", help="the edit's length")
        c.add_argument("--tone", choices=("bright", "dark", "any"), default="any", help="from the brief")
        c.add_argument("--bpm", help="range, e.g. 90-135")
        if name == "find":
            c.add_argument("--query", required=True, help='words about the music only: "upbeat,happy background"')
            c.add_argument("--keep", type=int, default=3)
        else:
            c.add_argument("--file", required=True)
    u = sub.add_parser("use")
    u.add_argument("--file", required=True)
    u.add_argument("--url")
    u.add_argument("--license")
    u.add_argument("--title")
    a = ap.parse_args()
    {"find": cmd_find, "check": cmd_check, "use": cmd_use}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
