# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Who is speaking, sentence by sentence — for framing when several people are on screen.

    uv run speaker.py [files...]            # → 02-work/speakers.json, speakers.md, people_<file>.jpg
    uv run speaker.py --set L12=2           # override: line 12 is said by person 2 (kept across rebuilds)
    uv run speaker.py --set L12=auto        # back to the automatic choice

It uses whatever each video has, each signal for what it's worth:
- one mic per person (several audio tracks, or one person per channel): the strongest
  signal. Which mic is whose face is learned by comparing, over the whole file, when
  each mic sounds with when each mouth moves — nothing to set up;
- mouth movement of each face while someone speaks (the transcript says when);
- neither is reliable (a face in profile or too small, someone speaking off camera,
  two at once) → "unsure": the framing stays where it was.
People are numbered from left to right (people_<file>.jpg shows who is who).
Decided per sentence, so the framing changes only where a sentence starts.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _common import die, filter_path, find_font, load_json, tool  # noqa: E402

RATE = 10                 # analysis samples per second
MATCH = 0.12              # max center distance (0-1 of the frame) for a face to continue a track
MIN_PRESENCE = 0.3        # a track present less than this share of the file isn't a person on screen
MIN_FACE = 0.06           # face height (share of the frame) below which mouths aren't readable
AUDIO_DB = 6.0            # how much louder one person's mic must be to decide
MOUTH_MARGIN = 0.35       # how much more one mouth must move (each on its own scale) to decide


# ----------------------------------------------------------------- people

def tracks(samples: list[dict], duration: float) -> list[dict]:
    """Faces linked over time into people (nearest center from one sample to the next),
    ordered from left to right; each with x, y, h at RATE (NaN where it isn't seen)."""
    live: list[dict] = []
    for s in samples:
        boxes = [f[:4] for f in s["faces"]]
        used = set()
        for tr in live:
            if s["t"] - tr["t"][-1] > 2.0:
                continue
            last = tr["b"][-1]
            cx, cy = last[0] + last[2] / 2, last[1] + last[3] / 2
            best = min(((abs(b[0] + b[2] / 2 - cx) + abs(b[1] + b[3] / 2 - cy), i) for i, b in enumerate(boxes)
                        if i not in used), default=None)
            if best and best[0] < MATCH:
                used.add(best[1])
                tr["t"].append(s["t"])
                tr["b"].append(boxes[best[1]])
        for i, b in enumerate(boxes):
            if i not in used:
                live.append({"t": [s["t"]], "b": [b]})
    n_samples = max(len(samples), 1)
    people = [tr for tr in live if len(tr["t"]) / n_samples >= MIN_PRESENCE]
    t = np.arange(0, duration, 1 / RATE)
    out = []
    for tr in sorted(people, key=lambda tr: np.median([b[0] + b[2] / 2 for b in tr["b"]])):
        st, b = np.array(tr["t"]), np.array(tr["b"])
        x = np.interp(t, st, b[:, 0] + b[:, 2] / 2)
        y = np.interp(t, st, b[:, 1] + b[:, 3] / 2)
        w = np.interp(t, st, b[:, 2])
        h = np.interp(t, st, b[:, 3])
        far = np.array([np.min(np.abs(st - ti)) for ti in t]) > 1.0
        for a in (x, y, w, h):
            a[far] = np.nan
        out.append({"x": x, "y": y, "w": w, "h": h})
    return out


def mouth_motion(video: str, people: list[dict], duration: float, width: int, height: int) -> np.ndarray:
    """How much each person's mouth area changes from frame to frame (people × samples),
    read from a small grey copy of the video streamed at RATE."""
    w = 320
    h = int(round(w * height / width / 2)) * 2
    proc = subprocess.Popen([tool("ffmpeg"), "-v", "error", "-i", video, "-vf", f"fps={RATE},scale={w}:{h},format=gray",
                             "-f", "rawvideo", "-"], stdout=subprocess.PIPE)
    n = int(duration * RATE) + 1
    out = np.full((len(people), n), np.nan)
    prev, i = None, 0
    while True:
        buf = proc.stdout.read(w * h)
        if len(buf) < w * h or i >= n:
            break
        frame = np.frombuffer(buf, np.uint8).reshape(h, w).astype(np.float32)
        if prev is not None:
            for k, p in enumerate(people):
                if i >= len(p["x"]) or np.isnan(p["x"][i]) or p["h"][i] < MIN_FACE:
                    continue
                # The mouth: the lower third of the face box, its middle half.
                x0, x1 = int((p["x"][i] - p["w"][i] / 4) * w), int((p["x"][i] + p["w"][i] / 4) * w)
                y0, y1 = int((p["y"][i] + p["h"][i] * 0.1) * h), int((p["y"][i] + p["h"][i] * 0.45) * h)
                x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, w), min(y1, h)
                if x1 - x0 >= 3 and y1 - y0 >= 3:
                    a, b = frame[y0:y1, x0:x1], prev[y0:y1, x0:x1]
                    out[k, i] = float(np.abs(a - b).mean() / (a.mean() + 8))
        prev, i = frame, i + 1
    proc.stdout.close()
    proc.wait()
    return out


# ------------------------------------------------------------------ mics

def mic_levels(m: dict, duration: float) -> list[tuple[str, np.ndarray]]:
    """One level curve (dB at RATE) per person mic: several tracks with speech, or one
    person per channel ('separate mics' from ingest). Empty when there's one shared mic."""
    tracks_ = m.get("audio") or []
    sources = []
    if len(tracks_) >= 2:
        sources = [(f"track {a['index'] + 1}", ["-map", f"0:a:{a['index']}", "-ac", "1"]) for a in tracks_
                   if (a.get("integrated_lufs") or -99) > -50]
    elif tracks_ and (tracks_[0].get("channels") or {}).get("layout") == "separate mics":
        sources = [(f"channel {c + 1}", ["-map", "0:a:0", "-af", f"pan=mono|c0=c{c}"])
                   for c in tracks_[0]["channels"]["live"]]
    if len(sources) < 2:
        return []
    out = []
    for label, sel in sources:
        raw = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", m["file"], *sel, "-ar", "8000", "-f", "f32le",
                              "-"], capture_output=True).stdout
        x = np.frombuffer(raw, np.float32)
        hop = 8000 // RATE
        k = len(x) // hop
        rms = np.sqrt((x[:k * hop].reshape(k, hop) ** 2).mean(1)) if k else np.zeros(0)
        db = 20 * np.log10(rms + 1e-6)
        n = int(duration * RATE) + 1
        out.append((label, np.pad(db, (0, max(0, n - len(db))), constant_values=-120)[:n]))
    return out


def map_mics(mics: list[tuple[str, np.ndarray]], mouths: np.ndarray, talk: np.ndarray) -> dict[str, int]:
    """Which mic is whose: the person whose mouth moves when that mic is loud, over the file."""
    if not mics or not len(mouths):
        return {}
    pairs = []
    for label, db in mics:
        n = min(len(db), mouths.shape[1], len(talk))
        for p in range(len(mouths)):
            ok = talk[:n] & ~np.isnan(mouths[p][:n])
            if ok.sum() < RATE * 5:
                continue
            c = float(np.corrcoef(db[:n][ok], mouths[p][:n][ok])[0, 1])
            if not np.isnan(c):
                pairs.append((c, label, p))
    out, used = {}, set()
    for c, label, p in sorted(pairs, reverse=True):
        if c > 0.15 and label not in out and p not in used:
            out[label] = p
            used.add(p)
    return out


# ---------------------------------------------------------------- decide

def activity(a: np.ndarray) -> np.ndarray:
    """A mouth's movement on its own scale: 0 its still level (its quietest fifth), 1 its usual
    movement when talking (its top tenth) — whether that person talks a little or most of the time."""
    ok = ~np.isnan(a)
    if ok.sum() < 10:
        return np.full_like(a, np.nan)
    still, moving = np.percentile(a[ok], 20), np.percentile(a[ok], 90)
    return (a - still) / max(moving - still, 1e-6)


def decide(sentences: list[dict], people: list[dict], mouths: np.ndarray, mics, mapping) -> list[dict]:
    z = np.array([activity(m) for m in mouths]) if len(mouths) else np.zeros((0, 0))
    out = []
    for s in sentences:
        i0, i1 = int(s["start"] * RATE), max(int(s["end"] * RATE), int(s["start"] * RATE) + 1)
        who, conf, how = None, "unsure", ""
        if mapping:
            levels = sorted(((float(np.mean(db[i0:i1])), label) for label, db in mics if label in mapping),
                            reverse=True)
            if len(levels) >= 2 and levels[0][0] - levels[1][0] >= AUDIO_DB:
                who, conf, how = mapping[levels[0][1]], "high", f"{levels[0][1]} {levels[0][0] - levels[1][0]:.0f} dB louder"
        if who is None and len(z):
            moves = [(float(np.nanmean(z[p, i0:i1])) if (~np.isnan(z[p, i0:i1])).any() else -9, p)
                     for p in range(len(z))]
            moves.sort(reverse=True)
            margin = moves[0][0] - (moves[1][0] if len(moves) > 1 else 0.0)
            if moves[0][0] > 0.4 and margin >= MOUTH_MARGIN:
                who, how = moves[0][1], "mouth"
                conf = "high" if margin >= 0.7 else "medium"
        out.append({"line": s["line"], "start": s["start"], "end": s["end"],
                    "auto": {"person": None if who is None else who + 1, "confidence": conf, "how": how}})
    return out


# ------------------------------------------------------------------ sheet

def people_sheet(m: dict, people: list[dict], dst: Path) -> None:
    """One frame with every person boxed and numbered: who 'person 2' is."""
    t_best = None
    for i in range(len(people[0]["x"])):
        if all(not np.isnan(p["x"][i]) for p in people):
            t_best = i / RATE
            break
    if t_best is None:
        return
    W, H = m["display_width"], m["display_height"]
    i = int(t_best * RATE)
    font = find_font()
    ff = f"fontfile='{filter_path(font)}':" if font else ""
    draws = []
    for k, p in enumerate(people):
        x, y, w, h = p["x"][i] - p["w"][i] / 2, p["y"][i] - p["h"][i] / 2, p["w"][i], p["h"][i]
        draws.append(f"drawbox=x={x * W:.0f}:y={y * H:.0f}:w={w * W:.0f}:h={h * H:.0f}:color=yellow:t=6")
        draws.append(f"drawtext={ff}text='{k + 1}':x={x * W:.0f}:y={max(0, (y - 0.08) * H):.0f}:fontsize={int(H * 0.07)}"
                     ":fontcolor=yellow:box=1:boxcolor=black@0.6")
    subprocess.run([tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{t_best:.2f}", "-i", m["file"], "-frames:v", "1",
                    "-vf", ",".join(draws + ["scale=-2:720"]), str(dst)], check=False)


# ------------------------------------------------------------------- main

def analyze(names: list[str] | None = None, ingest: Path = Path("01-ingest"), out: Path = Path("02-work")) -> dict:
    """{file: {"people": n, "mics": {...}, "sentences": [...]}} for files with 2+ people on screen.
    Cached by the file and the transcript; overrides (--set) are applied on top."""
    material = {m["name"]: m for m in load_json(ingest / "material.json")["files"]}
    faces = load_json(ingest / "faces.json")["files"] if (ingest / "faces.json").exists() else {}
    tr = load_json(ingest / "transcript.json") if (ingest / "transcript.json").exists() else {"sentences": []}
    path = out / "speakers.json"
    old = load_json(path) if path.exists() else {}
    result, overrides = {}, old.get("_overrides", {})
    for name in names or [n for n, m in material.items() if m.get("kind") == "video"]:
        m = material.get(name)
        if not m:
            continue
        sents = [s for s in tr["sentences"] if s["file"] == name]
        src = Path(m["file"])
        key = [src.stat().st_size if src.exists() else 0, len(sents), sents[-1]["end"] if sents else 0]
        if old.get(name, {}).get("key") == key:
            result[name] = old[name]
            continue
        people = tracks(faces.get(name, []), m["duration"])
        if len(people) < 2:
            continue
        print(f"{name}: {len(people)} people on screen — reading who speaks…", flush=True)
        talk = np.zeros(int(m["duration"] * RATE) + 1, bool)
        for s in sents:
            talk[int(s["start"] * RATE):int(s["end"] * RATE) + 1] = True
        mouths = mouth_motion(m["file"], people, m["duration"], m["display_width"], m["display_height"])
        mics = mic_levels(m, m["duration"])
        mapping = map_mics(mics, mouths, talk)
        decided = decide(sents, people, mouths, mics, mapping)
        people_sheet(m, people, out / f"people_{Path(name).stem}.jpg")
        result[name] = {"key": key, "people": len(people),
                        "mics": {label: p + 1 for label, p in mapping.items()}, "sentences": decided}
    for name, r in result.items():
        for s in r["sentences"]:
            o = overrides.get(f"L{s['line']}")
            s.update({"person": int(o), "confidence": "set", "how": "override"} if o else s["auto"])
    out.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**result, "_overrides": overrides}, indent=1), encoding="utf-8")
    report(out, result)
    return result


def report(out: Path, result: dict) -> None:
    lines = ["# Who speaks", "", "Per sentence; people numbered from left to right "
             "(people_<file>.jpg). unsure → the framing stays where it was.", ""]
    for name, r in result.items():
        n_unsure = sum(1 for s in r["sentences"] if s["person"] is None)
        lines += [f"## {name}: {r['people']} people"
                  + (f" · mics: {', '.join(f'{k} = person {v}' for k, v in r['mics'].items())}" if r["mics"] else
                     " · one shared mic: decided by mouths")
                  + f" · {len(r['sentences']) - n_unsure}/{len(r['sentences'])} sentences decided", ""]
        for s in r["sentences"]:
            who = f"person {s['person']}" if s["person"] else "unsure"
            lines.append(f"- L{s['line']} [{s['start']:.1f}–{s['end']:.1f}] {who} ({s['confidence']}"
                         + (f", {s['how']}" if s["how"] else "") + ")")
        lines.append("")
    lines.append("Wrong? speaker.py --set L12=2 (then reframe.py again).")
    (out / "speakers.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--set", action="append", default=[], metavar="L12=2", help="line=person (or auto)")
    ap.add_argument("--ingest", default="01-ingest")
    args = ap.parse_args()
    path = Path("02-work/speakers.json")
    if args.set:
        data = load_json(path) if path.exists() else {}
        ov = data.setdefault("_overrides", {})
        for item in args.set:
            line, _, who = item.partition("=")
            if not line.upper().startswith("L") or not (who.isdigit() or who == "auto"):
                die(f"--set takes L<line>=<person number> or =auto, not '{item}'")
            if who == "auto":
                ov.pop(line.upper(), None)
            else:
                ov[line.upper()] = int(who)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
    analyze([Path(f).name for f in args.files] or None, Path(args.ingest))
    print((Path("02-work") / "speakers.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
