# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26", "opencv-python-headless>=4.9"]
# ///
"""The user's references (references/), ready to look at before designing.

    uv run refs.py            # → 01-ingest/references.json + 01-ingest/references/

References are someone else's covers, graphics, captions or edits the user likes: they
guide the style and are never put in the video. This makes each one viewable:
    photos   → itself, or a JPG preview when it's a format that can't be opened as is
               (HEIC, TIFF, RAW…)
    videos   → contact sheets (a regular grid and one frame per shot) and the rhythm:
               length, shot changes per minute, average shot length
    others   → listed (a PDF, a link file): open them
Already done and unchanged → skipped.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import AUDIO_EXT, VIDEO_EXT, Log, ffprobe, find_font, tool  # noqa: E402
from ingest import detect, sheets_and_scenes  # noqa: E402

REFS = Path("references")
OUT = Path("01-ingest")
VIEWABLE = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
PHOTO_EXT = {".heic", ".heif", ".tif", ".tiff", ".dng", ".arw", ".cr2", ".cr3", ".nef", ".raf", ".bmp", ".avif"}


def photo(p: Path, prev_dir: Path) -> dict:
    entry = {"name": p.name, "kind": "photo", "file": str(p)}
    try:
        s = next(x for x in ffprobe(p)["streams"] if x.get("codec_type") == "video")
        entry["size"] = [s.get("width"), s.get("height")]
    except Exception:            # noqa: BLE001 — a format ffmpeg can't read
        pass
    if p.suffix.lower() in VIEWABLE:
        entry["view"] = str(p)
        return entry
    dst = prev_dir / f"{p.stem}.jpg"
    r = subprocess.run([tool("ffmpeg"), "-hide_banner", "-v", "error", "-y", "-i", str(p), "-frames:v", "1",
                        "-vf", "scale='min(1600,iw)':-2", "-q:v", "3", str(dst)], capture_output=True, text=True)
    if r.returncode == 0 and dst.exists():
        entry["view"] = str(dst)
    else:
        entry["warning"] = "can't be opened here: ask for it as JPG or PNG"
    return entry


def video(p: Path, prev_dir: Path, font: str | None, log: Log) -> dict:
    m = detect(p)
    m["stem"] = f"ref_{p.stem}"
    entry = {"name": p.name, "kind": "video", "file": str(p), "duration": round(m["duration"], 2),
             "size": [m.get("display_width"), m.get("display_height")]}
    if m["kind"] != "video":
        entry["kind"] = "audio"
        return entry
    s = sheets_and_scenes(m, p, prev_dir, font, log)
    cuts = len(s["shot_changes"])
    entry.update({"shot_changes_per_min": s["shot_changes_per_min"],
                  "average_shot_s": round(m["duration"] / (cuts + 1), 2),
                  "sheets": [str(prev_dir / x) for x in s["sheets"]]})
    if m.get("hdr"):
        entry["warning"] = "HDR: the sheets look flat — judge colors loosely"
    return entry


def main() -> None:
    if not REFS.is_dir() or not any(f.is_file() and not f.name.startswith(".") for f in REFS.rglob("*")):
        print("no references (references/ is empty): the design comes from the material alone")
        return
    prev_dir = OUT / "references"
    prev_dir.mkdir(parents=True, exist_ok=True)
    out = OUT / "references.json"
    old = {e["file"]: e for e in json.loads(out.read_text(encoding="utf-8"))["references"]} if out.exists() else {}
    log, font = Log(OUT / "ingest.log"), find_font()
    refs = []
    for p in sorted((f for f in REFS.rglob("*") if f.is_file() and not f.name.startswith(".")),
                    key=lambda f: f.name.lower()):
        mtime = round(p.stat().st_mtime, 2)
        if str(p) in old and old[str(p)].get("mtime") == mtime:
            refs.append(old[str(p)])
            continue
        ext = p.suffix.lower()
        if ext in VIEWABLE | PHOTO_EXT:
            e = photo(p, prev_dir)
        elif ext in VIDEO_EXT | AUDIO_EXT:
            e = video(p, prev_dir, font, log)
        else:
            e = {"name": p.name, "kind": "other", "file": str(p)}
        e["mtime"] = mtime
        refs.append(e)
    out.write_text(json.dumps({"references": refs}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(refs)} reference(s) → {out}")
    for e in refs:
        if e["kind"] == "video":
            print(f"  video  {e['name']}: {e['duration']:.0f} s · {e['shot_changes_per_min']} shot changes/min "
                  f"(a shot every {e['average_shot_s']} s) · look at: {', '.join(e['sheets'])}")
        elif e["kind"] == "photo":
            print(f"  photo  {e['name']}: look at {e.get('view') or '—'}")
        else:
            print(f"  {e['kind']:<6} {e['name']}: open it")
        if e.get("warning"):
            print(f"         WARNING: {e['warning']}")


if __name__ == "__main__":
    main()
