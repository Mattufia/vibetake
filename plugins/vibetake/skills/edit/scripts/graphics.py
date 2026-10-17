# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""Cue sheet for the graphics layer: when each word is said in the final edit, and where
there's room on screen.

    uv run graphics.py cues [--edl 02-work/edl.json] [--out 02-work/cues.json]

Run it after the edit and the framing are final. It writes, in timeline seconds and
frames of the output:
    lines     every kept transcript line with its words (text, start, end, frame)
    cuts      where each cut lands
    hook      the cold open's span, if there is one
    face      where the head is on screen every 0.5 s (top/bottom, 0-1 of the height)
    safe      the safe zone for this format
    captions  where burned-in subtitles sit (so graphics take another band)
The graphics project (Remotion or canvas) reads it as data: every element enters on
the frame of the word that names it, and stays out of the face and caption bands.
Sound effects anchor to the same words (sfx.py place --line --word), so picture and
sound land together.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import die  # noqa: E402
from _timeline import resolve_broll, starts, total  # noqa: E402
from subtitles import SAFE_H, SAFE_V, SAFE_X_V, face_band, timeline_words  # noqa: E402


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def build(edl: dict, tr: dict, faces: dict, material: dict) -> dict:
    W, H, fps = edl["output"]["width"], edl["output"]["height"], edl["output"]["fps"]
    segs = edl["segments"]
    t0 = starts(segs, fps)
    end = total(segs, fps)
    words = timeline_words(edl, tr)
    by_line: dict[int, dict] = {}
    for w in words:
        seg = segs[w["seg"]]
        sent = next((s for s in tr["sentences"] if s["file"] == seg["name"]
                     and s["start"] - 1e-3 <= w["src"] <= s["end"] + 1e-3), None)
        if sent is None:
            continue
        key = (sent["line"], w["seg"])                     # a line repeated (cold open) appears twice
        ln = by_line.setdefault(key, {"line": sent["line"], "role": seg.get("role", ""), "words": []})
        ln["words"].append({"text": w["text"], "start": w["t"], "end": w["end"], "frame": round(w["t"] * fps)})
    lines = sorted(by_line.values(), key=lambda x: x["words"][0]["start"])
    for ln in lines:
        ln["text"] = " ".join(w["text"] for w in ln["words"])
        ln["start"], ln["end"] = ln["words"][0]["start"], ln["words"][-1]["end"]
    broll, _ = resolve_broll(segs, fps, edl.get("broll", []))
    face = []
    t = 0.0
    while t < end:
        b = face_band(t, edl, t0, faces, material, broll)
        face.append({"t": round(t, 2), "top": round(b[0], 3), "bottom": round(b[1], 3)} if b
                    else {"t": round(t, 2), "top": None, "bottom": None})
        t += 0.5
    vertical = H > W
    sv = SAFE_V if vertical else SAFE_H
    sx = SAFE_X_V if vertical else (0.05, 0.95)
    hook = next(({"start": t0[k], "end": t0[k] + s["out"] - s["in"]} for k, s in enumerate(segs)
                 if s.get("role") == "hook"), None)
    return {"width": W, "height": H, "fps": fps, "duration": round(end, 3), "frames": round(end * fps),
            "safe": {"x0": sx[0], "y0": sv[0], "x1": sx[1], "y1": sv[1]},
            "captions": {"default_y": 0.70 if vertical else 0.86,
                         "note": "subtitles.py moves them below or above the face; keep graphics clear of both"},
            "hook": hook, "cuts": [round(x, 3) for x in t0[1:]], "lines": lines, "face": face}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("cues",))
    ap.add_argument("--edl", default="02-work/edl.json")
    ap.add_argument("--ingest", default="01-ingest")
    ap.add_argument("--out", default="02-work/cues.json")
    args = ap.parse_args()
    ing = Path(args.ingest)
    edl = load(Path(args.edl))
    tr = load(ing / "transcript.json")
    faces = load(ing / "faces.json")["files"] if (ing / "faces.json").exists() else {}
    material = {m["name"]: m for m in load(ing / "material.json")["files"]} if (ing / "material.json").exists() \
        else {}
    cues = build(edl, tr, faces, material)
    if not cues["lines"]:
        die("no transcript words inside the edit")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cues, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(cues['lines'])} lines, {len(cues['cuts'])} cuts, {cues['duration']:.1f} s "
          f"({cues['frames']} frames @ {cues['fps']}) → {out}")
    for ln in cues["lines"]:
        print(f"  [{ln['start']:6.2f}] L{ln['line']}{' (hook)' if ln['role'] == 'hook' else ''}: {ln['text']}")


if __name__ == "__main__":
    main()
