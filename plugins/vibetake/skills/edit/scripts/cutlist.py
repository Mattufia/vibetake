# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""From transcript lines to an edit decision list (02-work/edl.json).

    uv run cutlist.py --keep auto [--fillers drop|keep] [--format vertical|horizontal|square|source]
    uv run cutlist.py --keep "L9,L2-L7,L12"      # explicit lines, in this order (L9 first = cold open)

--keep auto keeps every line that isn't marked as a retake. The script turns the kept
lines into cut points: word-accurate, without the dropped fillers, long pauses
shortened, a natural margin around each phrase, and every cut moved to the quietest
moment within ±80 ms (never into a word that was dropped). Then:

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
SNAP_S = 0.08
MIN_SEGMENT_S = 0.25


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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", default="auto", help='"auto" or lines like "L9,L2-L7"')
    ap.add_argument("--fillers", choices=("drop", "keep"), default="drop")
    ap.add_argument("--max-pause", type=float, default=0.45,
                    help="longer pauses inside a phrase are cut (tight: 0.3; let it breathe: 0.8)")
    ap.add_argument("--pad-in", type=float, default=0.06)
    ap.add_argument("--pad-out", type=float, default=0.14)
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
            if same and item[1]["_i"] == pw["_i"] + 1 and item[1]["start"] - pw["end"] <= args.max_pause:
                runs[-1].append(item)
                continue
        runs.append([item])

    dbs: dict[str, np.ndarray] = {}
    segments = []
    for run in runs:
        name = file_of[id(run[0][1])]
        src = (material.get(name) or {}).get("file") or die(f"no source path for {name} in material.json")
        if name not in dbs:
            dbs[name] = quiet_db(src, tr["files"][name].get("track", 0))
        ws = words[name]
        first, last = run[0][1], run[-1][1]
        prev_end = ws[first["_i"] - 1]["end"] + 0.02 if first["_i"] > 0 else 0.0
        next_start = ws[last["_i"] + 1]["start"] - 0.02 if last["_i"] + 1 < len(ws) else tr["files"][name]["duration"]
        t_in = snap(max(first["start"] - args.pad_in, prev_end), prev_end, first["start"], dbs[name])
        t_out = snap(min(last["end"] + args.pad_out, next_start), last["end"], next_start, dbs[name])
        if t_out - t_in < MIN_SEGMENT_S:
            continue
        segments.append({"source": src, "name": name, "in": t_in, "out": t_out,
                         "lines": sorted({n for n, _ in run}),
                         "text": "".join(w["word"] for _, w in run).strip()})

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
