# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""B-roll from the user's own material: catalog, placement, check.

    uv run broll.py list                         # candidates → 02-work/broll.json (describe them there)
    uv run broll.py place --clip desk.mp4 --line L5 --word material [--in 2.0] [--dur 2.5] [--offset -0.2]
    uv run broll.py place --clip desk.mp4 --cut 3 [--dur 2.0]   # cover the cut before segment 3
    uv run broll.py check                        # where each b-roll lands, overlaps, coverage
    uv run broll.py clear

`list` gathers the clips ingest flagged as b-roll candidates (little speech, no face
talking to camera) with their contact sheets into 02-work/broll.json. Look at the
sheets and fill in "description" and "usable" ranges: that's the catalog to choose
from. `place` anchors a clip to a moment of the source — a word of a transcript line,
or the cut before a segment — and stores it in edl.json. Because it's anchored to
the source, re-cutting doesn't break it. render.py overlays it (the speech keeps
playing), framed to the output, with the clip's own color grade and a slow push-in.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import die, load_json as load, tc  # noqa: E402
from _timeline import anchor_from, resolve_broll, total  # noqa: E402


def cmd_list(args) -> None:
    material = load(Path(args.ingest) / "material.json")["files"]
    cat_path = Path(args.catalog)
    catalog = load(cat_path) if cat_path.exists() else {"clips": []}
    known = {c["name"]: c for c in catalog["clips"]}
    for m in material:
        if m.get("kind") == "video" and m.get("broll_candidate") and m["name"] not in known:
            known[m["name"]] = {"name": m["name"], "file": m["file"], "duration": round(m["duration"], 2),
                                "sheets": [f"{args.ingest}/contact/{s}" for s in m.get("shots", {}).get("sheets", [])],
                                "description": "", "usable": [[0, round(m["duration"], 2)]]}
    catalog["clips"] = list(known.values())
    cat_path.parent.mkdir(parents=True, exist_ok=True)
    cat_path.write_text(json.dumps(catalog, indent=1, ensure_ascii=False), encoding="utf-8")
    if not catalog["clips"]:
        print("No b-roll candidates in the material (every clip has someone talking to camera).")
        return
    for c in catalog["clips"]:
        print(f"- {c['name']} ({tc(c['duration'])}): {c['description'] or '(describe it: look at its sheets)'}")
        for s in c["sheets"]:
            print(f"    {s}")
    print(f"catalog → {cat_path}")


def cmd_place(args) -> None:
    edl_path = Path(args.edl)
    edl = load(edl_path)
    material = {m["name"]: m for m in load(Path(args.ingest) / "material.json")["files"]}
    clip = material.get(args.clip) or die(f"{args.clip} isn't in material.json")
    anchor = anchor_from(args, edl)
    if args.offset is not None:
        offset = args.offset
    else:
        offset = -args.dur / 2 if args.cut is not None else -0.1     # centered on a cut; just before a word
    if args.in_ + args.dur > clip["duration"] + 1e-3:
        die(f"{args.clip} is {clip['duration']:.2f} s long: --in {args.in_} --dur {args.dur} goes past its end")
    item = {"clip": args.clip, "source": clip["file"], "in": args.in_, "dur": args.dur,
            "anchor": anchor, "offset": offset, "note": args.note or ""}
    edl.setdefault("broll", []).append(item)
    edl_path.write_text(json.dumps(edl, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"placed {args.clip} [{args.in_:.2f}+{args.dur:.2f} s] at {anchor['name']} @ {anchor['time']:.2f} s "
          f"{offset:+.2f} s")
    cmd_check(args)


def cmd_check(args) -> None:
    edl = load(Path(args.edl))
    fps = edl["output"]["fps"]
    placed, warnings = resolve_broll(edl["segments"], fps, edl.get("broll", []))
    end = total(edl["segments"], fps)
    covered = sum(b["dur"] for b in placed)
    for b in placed:
        print(f"[{tc(b['at'])}–{tc(b['at'] + b['dur'])}] {b['clip']} (from {b['in']:.2f} s)  {b.get('note', '')}")
    print(f"b-roll covers {covered:.1f} s of {end:.1f} s ({100 * covered / end:.0f}%)")
    hook = edl["segments"][0].get("role") == "hook"
    if hook and placed and placed[0]["at"] < (edl["segments"][0]["out"] - edl["segments"][0]["in"]):
        warnings.append("b-roll over the hook: the opening should show the person saying it")
    for w in warnings:
        print("WARNING:", w)


def cmd_clear(args) -> None:
    edl_path = Path(args.edl)
    edl = load(edl_path)
    n = len(edl.pop("broll", []))
    edl_path.write_text(json.dumps(edl, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"removed {n} b-roll placements")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ingest", default="01-ingest")
    ap.add_argument("--edl", default="02-work/edl.json")
    ap.add_argument("--catalog", default="02-work/broll.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("place")
    p.add_argument("--clip", required=True)
    p.add_argument("--line", help="transcript line the b-roll illustrates, e.g. L5")
    p.add_argument("--word", help="word of that line where it starts (default: the line's first word)")
    p.add_argument("--cut", type=int, help="cover the cut before this segment (0-based index in edl.json)")
    p.add_argument("--in", dest="in_", type=float, default=0.0, help="start inside the b-roll clip")
    p.add_argument("--dur", type=float, default=2.5)
    p.add_argument("--offset", type=float, help="seconds relative to the anchor")
    p.add_argument("--note")
    sub.add_parser("check")
    sub.add_parser("clear")
    args = ap.parse_args()
    if args.cmd == "place" and not (args.line or args.cut is not None):
        die("place needs --line (and optionally --word) or --cut")
    {"list": cmd_list, "place": cmd_place, "check": cmd_check, "clear": cmd_clear}[args.cmd](args)


if __name__ == "__main__":
    main()
