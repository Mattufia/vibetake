# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Chapters and GIFs.

    uv run deliver.py chapter --line L5 [--word x] --title "Pricing"    # a chapter where L5 is said
    uv run deliver.py chapter --cut 3 --title "The demo"                # at the cut before segment 3 (0-based)
    uv run deliver.py chapters [--clear]          # the list as YouTube wants it (timeline times)
    uv run deliver.py gif [--video f] [--from 12.5] [--to 16] [--width 480] [--fps 15]

Chapters are anchored to moments of the source, so a re-cut moves them with their lines.
render.py writes them into the MP4 (players show them) and next to it as <video>.chapters.txt
(paste into the YouTube description). The first chapter shows at 0:00.

gif: a stretch of the latest delivered video (default the whole thing, best under ~10 s) as a
looping GIF with its own palette, next to the video.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import DELIVERY, deliverable_name, die, ffprobe, run, tool, versions  # noqa: E402
from _timeline import anchor_from, chapters, total  # noqa: E402


def stamp(t: float) -> str:
    m, s = divmod(int(t), 60)
    return f"{m // 60}:{m % 60:02d}:{s:02d}" if m >= 60 else f"{m}:{s:02d}"


def youtube(chs: list[tuple[float, str]], total: float | None = None) -> tuple[str, list[str]]:
    """The description lines, and what YouTube would refuse (≥3 chapters, ≥10 s each, the last one too)."""
    warn = [] if len(chs) >= 3 else [f"YouTube shows chapters only with 3 or more (there are {len(chs)})"]
    warn += [f"'{b[1]}' starts {b[0] - a[0]:.0f} s after '{a[1]}': YouTube wants 10 s or more"
             for a, b in zip(chs, chs[1:]) if b[0] - a[0] < 10]
    if chs and total is not None and total - chs[-1][0] < 10:
        warn.append(f"'{chs[-1][1]}' lasts {total - chs[-1][0]:.0f} s to the end: YouTube wants 10 s or more")
    return "\n".join(f"{stamp(t)} {title}" for t, title in chs), warn


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edl", default="02-work/edl.json")
    ap.add_argument("--ingest", default="01-ingest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("chapter")
    c.add_argument("--title", required=True)
    c.add_argument("--line")
    c.add_argument("--word")
    c.add_argument("--cut", type=int)
    sub.add_parser("chapters").add_argument("--clear", action="store_true")
    g = sub.add_parser("gif")
    g.add_argument("--video")
    g.add_argument("--from", dest="start", type=float, default=0.0)
    g.add_argument("--to", type=float)
    g.add_argument("--width", type=int, default=480)
    g.add_argument("--fps", type=int, default=15)
    args = ap.parse_args()
    edl_p = Path(args.edl)
    edl = json.loads(edl_p.read_text(encoding="utf-8"))

    if args.cmd == "chapter":
        if not (args.line or args.cut is not None):
            die("--line L5 or --cut N: where the chapter starts")
        anchor = anchor_from(args, edl)
        edl.setdefault("chapters", []).append({"title": args.title, "anchor": anchor})
        edl_p.write_text(json.dumps(edl, indent=1, ensure_ascii=False), encoding="utf-8")
        if not any(t == args.title for _, t in chapters(edl)):
            print(f"WARNING: '{args.title}' is anchored to a moment that isn't in the current cut: it won't show")
    if args.cmd in ("chapter", "chapters"):
        if getattr(args, "clear", False):
            edl.pop("chapters", None)
            edl_p.write_text(json.dumps(edl, indent=1, ensure_ascii=False), encoding="utf-8")
        text, warn = youtube(chapters(edl), total(edl["segments"], edl["output"]["fps"]))
        print(text or "no chapters")
        for w in warn:
            print(f"WARNING: {w}")
        return

    vids = versions(deliverable_name(edl))
    video = Path(args.video) if args.video else (vids[-1][1] if vids else die(f"no video in {DELIVERY}/: render first"))
    end = args.to or float(ffprobe(video)["format"]["duration"])
    if end - args.start > 15:
        print(f"WARNING: {end - args.start:.0f} s is long for a GIF (heavy file): --from/--to for a moment")
    out = video.with_name(f"{video.stem}_{args.start:g}s.gif")
    run([tool("ffmpeg"), "-hide_banner", "-v", "error", "-y", "-ss", f"{args.start:.3f}", "-t", f"{end - args.start:.3f}",
         "-i", str(video), "-filter_complex",
         f"fps={args.fps},scale={args.width}:-2:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];"
         "[b][p]paletteuse=dither=sierra2_4a", "-loop", "0", str(out)])
    print(f"{out} · {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
