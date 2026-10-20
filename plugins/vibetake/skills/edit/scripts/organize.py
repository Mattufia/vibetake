# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""Tidy a folder of loose files into a project, before ingest.

    uv run organize.py            # in the folder the user opened
    uv run organize.py --dry-run  # only show what would move
    uv run organize.py --move references IMG_2045.jpg IMG_2046.jpg   # the user said where they go
    uv run organize.py --undo     # put everything back where it was

People open Claude Code in the folder where their files already are. Only what's clear
moves:
    video and audio files            → 00-raw/
    folders that contain them        → 00-raw/<folder>/ (a card dump, "Camera A/")
    logos (name says logo/brand/marca, or a PNG with transparency),
    fonts and .cube LUTs             → brand/
    anything named ref/referencia/inspo/moodboard (photo or video),
    and screenshots (screenshot/captura/pantallazo) → references/
    audio named music/musica/track/song… → music/
Other photos are left where they are and listed as unsure: they're used for nothing
until the user says where they go (--move). Anything else (a script, a brief, a PDF)
stays where it is too. It also creates the folders
the user can fill — brand/, references/, music/ — empty if there's nothing for them
yet, and lists them with what goes in each. Moves within the same
folder are instant renames — nothing is copied or re-encoded, file names are kept.
Every move is logged in 00-raw/.organized.json so --undo restores the original
layout exactly.

What's already in the project folders is left alone: run it again after adding
files and only the new loose ones move.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import AUDIO_EXT, VIDEO_EXT, die  # noqa: E402

RAW, BRAND, REFS, MUSIC = Path("00-raw"), Path("brand"), Path("references"), Path("music")
PROJECT_DIRS = {"00-raw", "01-ingest", "02-work", "03-delivery", "brand", "references", "music"}
USER_DIRS = {  # folders the user fills, and what goes in each (said to the user in their language)
    RAW: "footage: video, separate audio, b-roll",
    BRAND: "logo, typefaces, brand colors, a LUT",
    REFS: "styles you like, photos or videos: covers, graphics, captions, edits (taken as criteria, never copied)",
    MUSIC: "your own music",
}
# Whole words of the file name (so "preferido" isn't a "ref", "sistema" isn't a "tema");
# a word starting with one of the stems counts too ("referencias", "inspiración").
REF_WORDS = ({"ref", "refs", "inspo", "moodboard"}, ("referenc", "reference", "inspira"))
SHOT_WORDS = ({"screenshot", "screenshots", "captura", "capturas", "pantallazo", "scr"}, ("screenshot",))
MUSIC_WORDS = ({"music", "musica", "música", "track", "song", "tema", "cancion", "canción", "beat"},
               ("music", "músic", "cancion", "canción"))
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


def named(name: str, words: tuple[set[str], tuple[str, ...]]) -> bool:
    exact, stems = words
    toks = re.findall(r"[a-záéíóúñü]+", name.lower())
    return any(t in exact or t.startswith(stems) for t in toks)


UNSURE = object()    # a photo that could be material or a reference: left for the user


def destination(p: Path):
    ext, name = p.suffix.lower(), p.stem
    if p.is_dir():
        if p.name in PROJECT_DIRS or p.name.startswith("."):
            return None
        has_media = any(is_media(f) for f in p.rglob("*") if f.is_file())
        return RAW / p.name if has_media else None
    if p.name.startswith(".") or p.name == "project.md":
        return None
    if is_media(p):
        if named(name, REF_WORDS):                 # someone else's video, to learn from — never footage
            return REFS / p.name
        if ext in AUDIO_EXT and named(name, MUSIC_WORDS):
            return MUSIC / p.name
        return RAW / p.name
    if ext in FONT_EXT | LUT_EXT:
        return BRAND / p.name
    if ext in PHOTO_EXT:
        if named(name, REF_WORDS) or named(name, SHOT_WORDS) or "screen shot" in name.lower():
            return REFS / p.name
        if any(w in name.lower() for w in BRAND_WORDS) or (ext == ".png" and png_has_alpha(p)):
            return BRAND / p.name
        return UNSURE
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


def plan() -> tuple[list[tuple[Path, Path]], list[Path]]:
    moves, unsure = [], []
    for p in sorted(Path(".").iterdir(), key=lambda x: x.name.lower()):
        d = destination(p)
        if d is UNSURE:
            unsure.append(p)
        elif d is not None:
            moves.append((p, d))
    return moves, unsure


def say_unsure(unsure: list[Path]) -> None:
    """Photos left where they were: ask the user (in the one opening question) where they go."""
    if not unsure:
        return
    print(f"unsure — left here and used for nothing until the user says where they go "
          f"(references/ or 00-raw/; then organize.py --move <folder> <files>):")
    for p in unsure:
        print(f"  ? {p.name}")


def move(moves: list[tuple[Path, Path]]) -> tuple[int, list[str]]:
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
    return done, failed


def user_dirs() -> None:
    """Create the folders the user can fill and say what's in each."""
    print("project folders:")
    for d, what in USER_DIRS.items():
        d.mkdir(exist_ok=True)
        n = sum(1 for f in d.rglob("*") if f.is_file() and not f.name.startswith("."))
        print(f"  {str(d) + '/':<13} {n:>3} file(s)  — {what}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--move", nargs="+", metavar=("FOLDER", "FILE"),
                    help="move these files into FOLDER (references, 00-raw, brand, music), as the user said")
    args = ap.parse_args()

    if args.move:
        folder, files = args.move[0].strip("/\\"), args.move[1:]
        dest = {d.name: d for d in USER_DIRS}.get(folder) or die(
            f"--move: the folder is one of {', '.join(d.name for d in USER_DIRS)}")
        if not files:
            die("--move: name the files too")
        missing = [f for f in files if not Path(f).is_file()]
        if missing:
            die("not found here: " + ", ".join(missing))
        done, failed = move([(Path(f), dest / Path(f).name) for f in files])
        print(f"{done} file(s) → {dest}/ (undo: organize.py --undo)")
        for f in failed:
            print("WARNING: couldn't move", f, "— close it in other programs and run again")
        say_unsure(plan()[1])
        return

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
        for d in (BRAND, REFS, MUSIC, RAW):
            if d.exists() and not any(d.iterdir()):
                d.rmdir()
        print(f"{back} item(s) back where they were")
        return

    moves, unsure = plan()
    media_moves = [m for m in moves if m[1].parts[0] == RAW.name]
    if not moves:
        have = any(is_media(f) for f in RAW.rglob("*")) if RAW.exists() else False
        print("already organized: footage in 00-raw/" if have else
              "no video or audio here: put the footage in this folder (or 00-raw/) and run again")
        if not args.dry_run:
            user_dirs()
        say_unsure(unsure)
        return
    if not media_moves and not RAW.exists():
        print("no video or audio here — only:", ", ".join([s.name for s, _ in moves] + [p.name for p in unsure]))
        return
    for src, dst in moves:
        print(f"{'would move' if args.dry_run else 'move'}  {src.name}{'/' if src.is_dir() else ''}  →  {dst.parent}/")
    if args.dry_run:
        say_unsure(unsure)
        return
    done, failed = move(moves)
    print(f"organized: {done} item(s) moved (undo: organize.py --undo)")
    for f in failed:
        print("WARNING: couldn't move", f, "— close it in other programs and run again")
    user_dirs()
    say_unsure(unsure)


if __name__ == "__main__":
    main()
