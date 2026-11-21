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
    videos   → contact sheets (a regular grid and one frame per shot), the rhythm (length,
               shot changes per minute, average/median/shortest shot, first cut), the look
               (brightness, contrast, saturation, warmth) and the sound (loudness, tempo)
    others   → listed (a PDF, a link file): open them
Already done and unchanged → skipped.
"""
from __future__ import annotations

import json
import re
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


def photo(p: Path, prev_dir: Path, stem: str) -> dict:
    entry = {"name": p.name, "kind": "photo", "file": str(p)}
    try:
        s = next(x for x in ffprobe(p)["streams"] if x.get("codec_type") == "video")
        entry["size"] = [s.get("width"), s.get("height")]
    except Exception:            # noqa: BLE001 — a format ffmpeg can't read
        pass
    if p.suffix.lower() in VIEWABLE:
        entry["view"] = str(p)
        return entry
    dst = prev_dir / f"{stem}.jpg"
    r = subprocess.run([tool("ffmpeg"), "-hide_banner", "-v", "error", "-y", "-i", str(p), "-frames:v", "1",
                        "-vf", "scale='min(1600,iw)':-2", "-q:v", "3", str(dst)], capture_output=True, text=True)
    if r.returncode == 0 and dst.exists():
        entry["view"] = str(dst)
    else:
        entry["warning"] = "can't be opened here: ask for it as JPG or PNG"
    return entry


def look(p: Path) -> dict:
    """The reference's picture in numbers (one frame a second), to match the grade to it:
    brightness and contrast (0–255 luma), saturation, warmth (red-minus-blue chroma: + warm)."""
    err = subprocess.run([tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(p), "-an", "-vf",
                          "fps=1,scale=320:-2,signalstats,metadata=print", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    val = lambda k: [float(x) for x in re.findall(rf"lavfi\.signalstats\.{k}=([\d.]+)", err)]  # noqa: E731
    mean = lambda xs: round(sum(xs) / len(xs), 1) if xs else None  # noqa: E731
    y, lo, hi, sat, u, v = (val(k) for k in ("YAVG", "YLOW", "YHIGH", "SATAVG", "UAVG", "VAVG"))
    out = {"brightness": mean(y), "contrast": mean([b - a for a, b in zip(lo, hi)]), "saturation": mean(sat),
           "warmth": mean([b - a for a, b in zip(u, v)])}
    if None in out.values():
        return out
    words = ["bright" if out["brightness"] > 140 else "dark" if out["brightness"] < 80 else "mid-key",
             "punchy" if out["contrast"] > 190 else "flat" if out["contrast"] < 130 else "normal contrast",
             "saturated" if out["saturation"] > 45 else "muted" if out["saturation"] < 20 else "natural color",
             "warm" if out["warmth"] > 6 else "cold" if out["warmth"] < -6 else "neutral"]
    out["reads_as"] = ", ".join(words)      # ponytail: rough thresholds; the sheets have the last word
    return out


def sound(p: Path) -> dict | None:
    """How loud it is, and the music's tempo if there's a beat to find."""
    from audio import find_beats, measure
    try:
        m = measure(p)
    except Exception:            # noqa: BLE001 — no audio stream
        return None
    out = {"lufs": m.get("I")}
    try:
        b = find_beats(p)
        out["bpm"] = round(b["bpm"])
    except (Exception, SystemExit):   # noqa: BLE001 — too short, or no steady beat
        pass
    return out


def video(p: Path, prev_dir: Path, font: str | None, log: Log, stem: str) -> dict:
    m = detect(p)
    m["stem"] = stem
    entry = {"name": p.name, "kind": "video", "file": str(p), "duration": round(m["duration"], 2),
             "size": [m.get("display_width"), m.get("display_height")]}
    if m["kind"] != "video":
        entry["kind"] = "audio"
        return entry
    s = sheets_and_scenes(m, p, prev_dir, font, log)
    cuts = len(s["shot_changes"])
    marks = [0.0, *s["shot_changes"], m["duration"]]
    shots = sorted(b - a for a, b in zip(marks, marks[1:]))
    entry.update({"shot_changes_per_min": s["shot_changes_per_min"],
                  "average_shot_s": round(m["duration"] / (cuts + 1), 2),
                  "median_shot_s": round(shots[len(shots) // 2], 2), "shortest_shot_s": round(shots[0], 2),
                  "first_cut_s": round(marks[1], 2),
                  "look": look(p), "sound": sound(p),
                  "sheets": [str(prev_dir / x) for x in s["sheets"]]})
    if m.get("hdr"):
        entry["warning"] = "HDR: the sheets look flat — judge colors loosely"
    return entry


def listed(root: Path) -> list[Path]:
    """Files in references/, without macOS leftovers (._x.mp4, __MACOSX/) or hidden files."""
    return [f for f in root.rglob("*") if f.is_file()
            and not any(x.startswith(".") or x == "__MACOSX" for x in f.relative_to(root).parts)]


def main() -> None:
    if not REFS.is_dir() or not listed(REFS):
        print("no references (references/ is empty): the design comes from the material alone")
        return
    prev_dir = OUT / "references"
    prev_dir.mkdir(parents=True, exist_ok=True)
    out = OUT / "references.json"
    old = {e["file"]: e for e in json.loads(out.read_text(encoding="utf-8"))["references"]} if out.exists() else {}
    log, font = Log(OUT / "ingest.log"), find_font()
    refs, stems = [], {}
    for p in sorted(listed(REFS), key=lambda f: (f.name.lower(), str(f).lower())):
        # reel.mp4 and reel.mov (or two folders' reel.mp4) each keep their own sheets.
        k = p.stem.lower()
        stems[k] = stems.get(k, 0) + 1
        stem = f"ref_{p.stem}" if stems[k] == 1 else f"ref_{p.stem}_{stems[k]}"
        mtime = round(p.stat().st_mtime, 2)
        if str(p) in old and old[str(p)].get("mtime") == mtime:
            refs.append(old[str(p)])
            continue
        ext = p.suffix.lower()
        if ext in VIEWABLE | PHOTO_EXT:
            e = photo(p, prev_dir, stem.removeprefix("ref_"))
        elif ext in VIDEO_EXT | AUDIO_EXT:
            try:
                e = video(p, prev_dir, font, log, stem)
            except Exception as ex:  # noqa: BLE001 — a broken file doesn't stop the other references
                reason = next((ln for ln in reversed(str(ex).splitlines()) if ln.strip()), ex.__class__.__name__)
                log(f"{p.name}: unreadable, skipped ({reason.strip()})")
                e = {"name": p.name, "kind": "other", "file": str(p),
                     "warning": "unreadable (damaged or incomplete file): ask for it again"}
        else:
            e = {"name": p.name, "kind": "other", "file": str(p)}
        e["mtime"] = mtime
        refs.append(e)
    out.write_text(json.dumps({"references": refs}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(refs)} reference(s) → {out}")
    for e in refs:
        if e["kind"] == "video":
            snd = e.get("sound") or {}
            print(f"  video  {e['name']}: {e['duration']:.0f} s · {e['shot_changes_per_min']} shot changes/min "
                  f"(a shot every {e['average_shot_s']} s, first cut at {e.get('first_cut_s', '?')} s) · "
                  f"{(e.get('look') or {}).get('reads_as', '')}"
                  + (f" · music ~{snd['bpm']} BPM" if snd.get("bpm") else "")
                  + f" · look at: {', '.join(e['sheets'])}")
        elif e["kind"] == "photo":
            print(f"  photo  {e['name']}: look at {e.get('view') or '—'}")
        else:
            print(f"  {e['kind']:<6} {e['name']}: open it")
        if e.get("warning"):
            print(f"         WARNING: {e['warning']}")


if __name__ == "__main__":
    main()
