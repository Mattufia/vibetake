# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""Tidy a folder of loose files into a project, before ingest.

    uv run organize.py            # in the folder the user opened
    uv run organize.py --dry-run  # only show what would move
    uv run organize.py --undo     # put everything back where it was

People open Claude Code in the folder where their files already are. This moves:
    video and audio files            → 00-raw/
    folders that contain them        → 00-raw/<folder>/ (a card dump, "Camera A/")
    photos                           → 00-raw/ (stills, b-roll)
    logos (name says logo/brand/marca, or a PNG with transparency),
    fonts and .cube LUTs             → brand/
Anything else (a script, a brief, a PDF) stays where it is. Moves within the same
folder are instant renames — nothing is copied or re-encoded, file names are kept.
Every move is logged in 00-raw/.organized.json so --undo restores the original
layout exactly.

What's already in 00-raw/ and brand/ is left alone: run it again after adding
files and only the new loose ones move.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import AUDIO_EXT, VIDEO_EXT, die  # noqa: E402

RAW, BRAND = Path("00-raw"), Path("brand")
PROJECT_DIRS = {"00-raw", "01-ingest", "02-work", "03-delivery", "brand"}
PHOTO_EXT = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tif", ".tiff", ".dng", ".arw", ".cr2", ".cr3",
             ".nef", ".raf"}
FONT_EXT = {".ttf", ".otf", ".woff", ".woff2"}
LUT_EXT = {".cube"}
LOG = RAW / ".organized.json"
BRAND_WORDS = ("logo", "brand", "marca", "isotipo", "isologo", "imagotipo")


def png_has_alpha(p: Path) -> bool:
    """A PNG whose header declares an alpha channel (color type 4 or 6) — logos usually are."""
    try:
        with open(p, "rb") as f:
            head = f.read(26)
        return head[:8] == b"\x89PNG\r\n\x1a\n" and head[25] in (4, 6)
    except OSError:
        return False


def is_media(p: Path) -> bool:
    return p.suffix.lower() in VIDEO_EXT | AUDIO_EXT


def destination(p: Path) -> Path | None:
    ext, name = p.suffix.lower(), p.stem.lower()
    if p.is_dir():
        if p.name in PROJECT_DIRS or p.name.startswith("."):
            return None
        has_media = any(is_media(f) for f in p.rglob("*") if f.is_file())
        return RAW / p.name if has_media else None
    if p.name.startswith(".") or p.name == "project.md":
        return None
    if is_media(p):
        return RAW / p.name
    if ext in FONT_EXT | LUT_EXT:
        return BRAND / p.name
    if ext in PHOTO_EXT:
        if any(w in name for w in BRAND_WORDS) or (ext == ".png" and png_has_alpha(p)):
            return BRAND / p.name
        return RAW / p.name
    return None


def free_name(dst: Path) -> Path:
    """Never overwrite: clip.mp4 → clip (2).mp4 if the name is taken."""
    if not dst.exists():
        return dst
    i = 2
    while True:
        cand = dst.with_name(f"{dst.stem} ({i}){dst.suffix}")
        if not cand.exists():
            return cand
        i += 1


def plan() -> list[tuple[Path, Path]]:
    moves = []
    for p in sorted(Path(".").iterdir(), key=lambda x: x.name.lower()):
        d = destination(p)
        if d is not None:
            moves.append((p, d))
    return moves


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--undo", action="store_true")
    args = ap.parse_args()

    if args.undo:
        if not LOG.exists():
            die("nothing to undo (no 00-raw/.organized.json)")
        log = json.loads(LOG.read_text(encoding="utf-8"))
        back = 0
        for m in reversed(log["moves"]):
            src, dst = Path(m["to"]), Path(m["from"])
            if src.exists():
                os.replace(src, free_name(dst))    # never overwrite: a clashing name gets " (2)"
                back += 1
        LOG.unlink()
        for d in (BRAND, RAW):
            if d.exists() and not any(d.iterdir()):
                d.rmdir()
        print(f"{back} item(s) back where they were")
        return

    moves = plan()
    media_moves = [m for m in moves if m[1].parts[0] == RAW.name]
    if not moves:
        have = any(is_media(f) for f in RAW.rglob("*")) if RAW.exists() else False
        print("already organized: footage in 00-raw/" if have else
              "no video or audio here: put the footage in this folder (or 00-raw/) and run again")
        return
    if not media_moves and not RAW.exists():
        print("no video or audio here — only:", ", ".join(s.name for s, _ in moves))
        return
    for src, dst in moves:
        print(f"{'would move' if args.dry_run else 'move'}  {src.name}{'/' if src.is_dir() else ''}  →  {dst.parent}/")
    if args.dry_run:
        return
    RAW.mkdir(exist_ok=True)
    log = json.loads(LOG.read_text(encoding="utf-8")) if LOG.exists() else {"moves": []}
    done, failed = 0, []
    for src, dst in moves:
        dst.parent.mkdir(exist_ok=True)
        target = free_name(dst)
        try:
            os.replace(src, target)
        except OSError as e:          # open in another program, permissions…
            failed.append(f"{src.name}: {e.strerror or e}")
            continue
        log["moves"].append({"from": str(src), "to": str(target), "at": time.strftime("%Y-%m-%d %H:%M")})
        done += 1
    LOG.write_text(json.dumps(log, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"organized: {done} item(s) moved (undo: organize.py --undo)")
    for f in failed:
        print("WARNING: couldn't move", f, "— close it in other programs and run again")


if __name__ == "__main__":
    main()
