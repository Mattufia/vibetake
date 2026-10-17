# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""From transcript lines to an edit decision list (02-work/edl.json).

    uv run cutlist.py --keep auto [--fillers drop|keep] [--format vertical|horizontal|square|source]
    uv run cutlist.py --keep "L9,L2-L7,L12"      # explicit lines, in this order (L9 first = cold open)

--keep auto keeps every line that isn't marked as a retake. The script turns the kept
lines into cut points: word-accurate, without the dropped fillers, long pauses
shortened (--pace tight|normal|relaxed), a pause at every join that sounds like speech
(longer after a sentence than after a comma), and every cut moved to the quietest
moment nearby (never into a word that was dropped). Word edges are measured on the
audio, not taken from Whisper's timestamps (which can be a quarter second off and
usually include some air): so a dropped word leaves no fragment at the cut and the
pauses heard are the ones intended. Each segment records which words it holds. Then:

    uv run render.py            # renders 02-work/edl.json

edl.json is the source of truth of the edit: change it (or re-run this) and render
again. Segments: source, in, out (seconds in the source), lines, note.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _common import die, tc, tool  # noqa: E402

FORMATS = {"vertical": (1080, 1920), "horizontal": (1920, 1080), "square": (1080, 1080)}
SNAP_S = 0.04
MIN_SEGMENT_S = 0.25
# Pause at a join, by what ends the phrase before it (seconds, at normal pace).
GAPS = {"sentence": 0.40, "comma": 0.25, "word": 0.15}
PACE = {"tight": (0.75, 0.30), "normal": (1.0, 0.45), "relaxed": (1.35, 0.80)}  # (gap scale, max pause)
TAIL_S = 0.05          # a word's sound lingers past its timestamp; never start inside it
START_LEAD_S = 0.10    # before the very first word
END_TAIL_S = 0.35      # after the very last word


def join_kind(last_word: str, follows: bool) -> str:
    """What kind of pause a join needs. A jump to another file or back in time reads as a
    new sentence; otherwise the punctuation of the last kept word decides."""
    w = last_word.strip()
    if not follows or w.endswith((".", "?", "!", "…")):
        return "sentence"
    return "comma" if w.endswith((",", ";", ":")) else "word"


def parse_keep(spec: str, sentences: list[dict]) -> list[int]:
    if spec == "auto":
        return [s["line"] for s in sentences if not s.get("retake_of")]
    lines: list[int] = []
    for part in spec.replace(" ", "").split(","):
        m = re.fullmatch(r"L?(\d+)(?:-L?(\d+))?", part, re.I)
        if not m:
            die(f"can't read '{part}' (use L3, L3-L7, …)")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        lines += list(range(a, b + 1)) if a <= b else list(range(a, b - 1, -1))
    known = {s["line"] for s in sentences}
    missing = [n for n in lines if n not in known]
    if missing:
        die(f"lines not in the transcript: {missing}")
    return lines


def quiet_db(path: str, track: int) -> np.ndarray:
    """10 ms RMS (dB) of the source audio, to snap cuts to silence."""
    p = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", path, "-map", f"0:a:{track}", "-ac", "1", "-ar", "16000",
                        "-f", "s16le", "-"], capture_output=True)
    x = np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768
    n = len(x) // 160
    return 20 * np.log10(np.sqrt((x[:n * 160].reshape(n, 160) ** 2).mean(1)) + 1e-9)


def snap(t: float, lo: float, hi: float, db: np.ndarray) -> float:
    """Quietest 10 ms frame within ±SNAP_S of t, staying inside [lo, hi]."""
    a, b = max(t - SNAP_S, lo), min(t + SNAP_S, hi)
    i0, i1 = int(a * 100), int(b * 100)
    if i1 <= i0 or i1 > len(db):
        return min(max(t, lo), hi)
    return round((i0 + int(np.argmin(db[i0:i1]))) / 100 + 0.005, 3)


VALLEY_REACH = 0.25   # how far Whisper's word edges can be off around a gap


def valley(db: np.ndarray, gap_a: float, gap_b: float) -> tuple[float, float] | None:
    """The real silence between two words whose timestamps put the gap at [gap_a, gap_b]:
    among the stretches (≥50 ms, 18 dB under the speech around them) within VALLEY_REACH of
    it, the one that overlaps it most (then the nearest). Whisper's word edges can be off by
    a quarter second; the audio isn't. A stop consonant inside a word (the closure of a
    't') is short and close to the word's middle, so it rarely wins."""
    lo, hi = gap_a - VALLEY_REACH, gap_b + VALLEY_REACH
    i0, i1 = max(int(lo * 100), 0), min(int(hi * 100), len(db))
    if i1 - i0 < 5:
        return None
    ctx = db[max(i0 - 30, 0):min(i1 + 30, len(db))]
    quiet = db[i0:i1] < np.percentile(ctx, 90) - 18
    found, start = [], None
    for k, q in enumerate(np.r_[quiet, False]):
        if q and start is None:
            start = k
        elif not q and start is not None:
            if k - start >= 5:
                found.append(((i0 + start) / 100, (i0 + k) / 100))
            start = None
    if not found:
        return None

    def score(v):
        overlap = min(v[1], gap_b + 0.15) - max(v[0], gap_a - 0.15)
        dist = max(v[0] - gap_b, gap_a - v[1], 0)
        return (overlap, -dist)
    return max(found, key=score)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", default="auto", help='"auto" or lines like "L9,L2-L7"')
    ap.add_argument("--fillers", choices=("drop", "keep"), default="drop")
    ap.add_argument("--pace", choices=tuple(PACE), default="normal",
                    help="scales the pauses at the joins and the longest pause kept inside a phrase")
    ap.add_argument("--max-pause", type=float, help="override: longer pauses inside a phrase are cut")
    ap.add_argument("--format", choices=(*FORMATS, "source"), default="source")
    ap.add_argument("--size", help="explicit WxH, overrides --format")
    ap.add_argument("--ingest", default="01-ingest")
    ap.add_argument("--out", default="02-work/edl.json")
    args = ap.parse_args()

    ingest = Path(args.ingest)
    tr = json.loads((ingest / "transcript.json").read_text(encoding="utf-8"))
    material = {}
    if (ingest / "material.json").exists():
        material = {m["name"]: m for m in json.loads((ingest / "material.json").read_text(encoding="utf-8"))["files"]}
    sents = {s["line"]: s for s in tr["sentences"]}
    keep = parse_keep(args.keep, tr["sentences"])
    scale, default_pause = PACE[args.pace]
    max_pause = args.max_pause if args.max_pause is not None else default_pause

    # Every word of every file, in source order, so neighbors (kept or not) are known.
    words: dict[str, list[dict]] = {}
    for name, f in tr["files"].items():
        words[name] = sorted((w for s in f["segments"] for w in s["words"]), key=lambda w: w["start"])
        for i, w in enumerate(words[name]):
            w["_i"] = i

    def in_span(w, spans):
        return any(w["start"] < e and w["end"] > s for s, e, _ in spans)

    kept: list[tuple[int, dict]] = []      # (line, word)
    dropped_fillers = 0
    for n in keep:
        s = sents[n]
        for w in words[s["file"]]:
            if w["start"] >= s["start"] - 1e-3 and w["end"] <= s["end"] + 1e-3:
                if args.fillers == "drop" and in_span(w, s.get("fillers", [])):
                    dropped_fillers += 1
                    continue
                kept.append((n, w))
    if not kept:
        die("nothing to keep")

    # Runs of contiguous words: same file, consecutive in the source, short gap.
    runs: list[list[tuple[int, dict]]] = []
    file_of = {id(w): sents[n]["file"] for n, w in kept}
    for item in kept:
        if runs:
            pn, pw = runs[-1][-1]
            same = file_of[id(pw)] == file_of[id(item[1])]
            if same and item[1]["_i"] == pw["_i"] + 1 and item[1]["start"] - pw["end"] <= max_pause:
                runs[-1].append(item)
                continue
        runs.append([item])

    dbs: dict[str, np.ndarray] = {}
    srcs: dict[str, str] = {}
    for name in {file_of[id(r[0][1])] for r in runs}:
        srcs[name] = (material.get(name) or {}).get("file") or die(f"no source path for {name} in material.json")
        dbs[name] = quiet_db(srcs[name], tr["files"][name].get("track", 0))

    def sound_start(name: str, i: int) -> float:
        """Where word i's sound really starts: the end of the silence the audio shows before it
        (Whisper's edges can be a quarter second off, and they usually include some air)."""
        ws, w = words[name], words[name][i]
        v = valley(dbs[name], ws[i - 1]["end"] if i > 0 else w["start"] - 0.3, w["start"])
        return v[1] if v and v[1] <= w["end"] else w["start"]

    def sound_end(name: str, i: int) -> float:
        ws, w = words[name], words[name][i]
        v = valley(dbs[name], w["end"], ws[i + 1]["start"] if i + 1 < len(ws) else w["end"] + 0.3)
        return v[0] if v and v[0] >= w["start"] else w["end"]

    # Clean silence available around each run, from the audio: before its first word (after
    # the previous word's tail, kept or dropped) and after its last word.
    info = []
    for run in runs:
        name = file_of[id(run[0][1])]
        ws, first, last = words[name], run[0][1], run[-1][1]
        s0, e0 = sound_start(name, first["_i"]), sound_end(name, last["_i"])
        before = sound_end(name, first["_i"] - 1) + TAIL_S if first["_i"] > 0 else 0.0
        after = sound_start(name, last["_i"] + 1) - 0.02 if last["_i"] + 1 < len(ws) \
            else tr["files"][name]["duration"]
        info.append({"name": name, "first": first, "last": last, "s0": s0, "e0": e0,
                     "lead_avail": max(s0 - before, 0.0), "tail_avail": max(after - e0, 0.0)})

    # The pause at each join sounds like speech: longer after a sentence than after a comma,
    # short where a filler was taken out mid-phrase. It's taken from wherever there's clean
    # silence — mostly after the previous phrase, only a little before the next word.
    lead = [min(i["lead_avail"], START_LEAD_S) for i in info]
    tail = [min(i["tail_avail"], END_TAIL_S) for i in info]
    for k in range(len(info) - 1):
        a, b = info[k], info[k + 1]
        follows = a["name"] == b["name"] and b["first"]["_i"] > a["last"]["_i"]
        g = GAPS[join_kind(a["last"]["word"], follows)] * scale
        lead[k + 1] = min(b["lead_avail"], min(max(g * 0.3, 0.04), 0.10))
        tail[k] = min(a["tail_avail"], g - lead[k + 1])
        if tail[k] < g - lead[k + 1]:
            lead[k + 1] = min(b["lead_avail"], g - tail[k])

    segments = []
    for run, i, ld, tl in zip(runs, info, lead, tail):
        name, s0, e0 = i["name"], i["s0"], i["e0"]
        src = srcs[name]
        # Edges measured on the audio, so a misplaced timestamp neither leaves a fragment of a
        # dropped word at the cut ("…no") nor eats the air the join needs.
        t_in = snap(s0 - ld, s0 - i["lead_avail"], s0 - min(0.01, ld), dbs[name])
        t_out = snap(e0 + tl, e0 + min(0.01, tl), e0 + i["tail_avail"], dbs[name])
        # On the source's frame grid, as render.py cuts: so subtitles, graphics, b-roll and
        # sound effects, which read these values, land on the same frames the render does.
        fr = (material.get(name) or {}).get("fps")
        if fr:
            t_in, t_out = (round(round(t * fr) / fr, 4) for t in (t_in, t_out))
        if t_out - t_in < MIN_SEGMENT_S:
            continue
        segments.append({"source": src, "name": name, "in": t_in, "out": t_out,
                         "lines": sorted({n for n, _ in run}), "words": [run[0][1]["_i"], run[-1][1]["_i"]],
                         "text": "".join(w["word"] for _, w in run).strip()})
    # A first line that comes later in the material is a cold open (hook).
    if segments and len(keep) > 1 and keep[0] > min(keep[1:]):
        segments[0]["role"] = "hook"

    first_src = material.get(segments[0]["name"], {})
    if args.size:
        w, h = map(int, args.size.lower().split("x"))
    elif args.format == "source":
        w, h = first_src.get("display_width", 1920), first_src.get("display_height", 1080)
    else:
        w, h = FORMATS[args.format]
    edl = {"version": 1, "output": {"width": w, "height": h, "fps": first_src.get("fps", 30)},
           "made_by": "cutlist", "params": vars(args), "segments": segments}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(edl, indent=1, ensure_ascii=False), encoding="utf-8")

    total_src = sum(f["duration"] for f in tr["files"].values())
    total = sum(s["out"] - s["in"] for s in segments)
    dropped_lines = [s["line"] for s in tr["sentences"] if s["line"] not in keep]
    print(f"{len(segments)} segments · {tc(total_src)} of material → {tc(total)} · "
          f"{dropped_fillers} filler words out · lines left out: {dropped_lines or 'none'}")
    for s in segments:
        print(f"  [{tc(s['in'])}–{tc(s['out'])}] {s['name']}  L{','.join(map(str, s['lines']))}  {s['text'][:70]}")
    print(f"edl → {out}")


if __name__ == "__main__":
    main()
