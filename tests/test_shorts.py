# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""Shorts from a long video (shorts.py): chosen stretches → their own cut, framing, captions, render.

    uv run tests/test_shorts.py

On the Spanish retakes fixture (as a video): a range leaves its retakes out, a hook opens the
short, two shorts sharing a line are flagged, a first line that leans on what came before is
flagged; make gives each its own cut (without the long video's chapters or montage block),
captions (.srt named after it) and video; covers and copy are looked up per short.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
FIX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(SCRIPTS))
import shorts  # noqa: E402


def uv(cwd: Path, *a: str) -> subprocess.CompletedProcess:
    return subprocess.run(["uv", "run", str(SCRIPTS / a[0]), *a[1:]], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main() -> None:
    checks = {}
    proj = Path(tempfile.mkdtemp(prefix="vt-shorts-"))
    for d in ("00-raw", "01-ingest", "02-work", "03-delivery"):
        (proj / d).mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=0x305070:s=640x360:r=30",
                    "-i", str(FIX / "retakes_es.wav"), "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(proj / "00-raw" / "talk.mp4")], check=True)
    tr = json.loads((FIX / "retakes_es.transcript.json").read_text(encoding="utf-8").replace("retakes_es.wav", "talk.mp4"))
    (proj / "01-ingest" / "transcript.json").write_text(json.dumps(tr, ensure_ascii=False), encoding="utf-8")
    (proj / "01-ingest" / "material.json").write_text(json.dumps({"files": [
        {"name": "talk.mp4", "file": str(proj / "00-raw" / "talk.mp4"), "kind": "video", "duration": 31.0, "fps": 30,
         "display_width": 640, "display_height": 360}]}))
    # The project's font (fonts.py downloads it in a real project): any bold TTF here, for the test only.
    font = next((f for d in (Path("C:/Windows/Fonts"), Path("/Library/Fonts"), Path("/usr/share/fonts"))
                 if d.exists() for f in sorted(d.rglob("*.ttf")) if "bd" in f.stem.lower() or "bold" in f.stem.lower()), None)
    if font:
        (proj / "02-work" / "fonts.json").write_text(json.dumps([{"file": str(font), "name": font.stem, "family": font.stem,
                                                                  "weight": 700, "italic": False}]))
    # The long video's edit, with chapters and a montage block a short must not inherit.
    (proj / "02-work" / "edl.json").write_text(json.dumps({
        "version": 1, "output": {"width": 640, "height": 360, "fps": 30}, "name": "charla",
        "segments": [{"source": str(proj / "00-raw" / "talk.mp4"), "name": "talk.mp4", "in": 2.1, "out": 31.0}],
        "chapters": [{"title": "Intro", "anchor": {"name": "talk.mp4", "time": 2.2}}],
        "montage": {"where": "start", "segments": [{"source": str(proj / "00-raw" / "talk.mp4"), "name": "talk.mp4",
                                                     "in": 0, "out": 1, "role": "montage"}]}}))

    r = uv(proj, "shorts.py", "add", "--name", "pasos", "--keep", "L1-L5", "--hook", "L5")
    data = json.loads((proj / "02-work" / "shorts.json").read_text(encoding="utf-8"))
    sh = data["shorts"][0]
    checks[f"add: a range leaves its retakes out ({sh['lines']})"] = r.returncode == 0 and sh["lines"] == [2, 3, 5]
    checks["add: a 13 s short is flagged as short for a feed"] = "feed short usually" in r.stdout
    r = uv(proj, "shorts.py", "add", "--name", "final", "--keep", "L5-L9")
    sh2 = json.loads((proj / "02-work" / "shorts.json").read_text(encoding="utf-8"))["shorts"][1]
    checks[f"add: two shorts sharing a line are flagged; L8 (a retake) left out ({sh2['lines']})"] = (
        "shares 1 line(s) with 'pasos'" in r.stdout and 8 not in sh2["lines"])
    fake = {"lang": "es", "files": {"a": {"segments": [{"words": [
        {"word": " Y", "start": 0.0, "end": 0.2}, {"word": " además", "start": 0.2, "end": 0.6},
        {"word": " funciona", "start": 0.6, "end": 1.0}]}]}}}
    sents = {1: {"line": 1, "file": "a", "start": 0.0, "end": 1.0}}
    _, warn = shorts.checks({"name": "x", "lines": [1]}, fake, sents, [])
    checks["add: a first line leaning on what came before (“Y además…”) is flagged; an end mid-thought too"] = (
        any("starts with “y”" in w for w in warn) and any("doesn't end a sentence" in w for w in warn))

    r = uv(proj, "shorts.py", "make", "--only", "pasos", "--format", "vertical")
    if r.returncode:
        print(r.stdout[-3000:], r.stderr[-3000:])
    edl = json.loads((proj / "02-work" / "edl_pasos.json").read_text(encoding="utf-8"))
    lines = [ln for s in edl["segments"] for ln in s.get("lines", [])]
    checks[f"make: its own cut, hook first ({lines})"] = r.returncode == 0 and lines[:1] == [5] and 2 in lines
    checks["make: not the long video's chapters or montage block"] = (
        "chapters" not in edl and "montage" not in edl and not any(s.get("role") == "montage" for s in edl["segments"]))
    checks[f"make: vertical ({edl['output']['width']}×{edl['output']['height']})"] = (
        edl["output"]["height"] > edl["output"]["width"])
    vids = sorted((proj / "03-delivery").glob("*_v*_pasos.mp4"))
    srts = sorted((proj / "03-delivery").glob("*_pasos.srt"))
    checks[f"make: its video and .srt ({[p.name for p in vids + srts]})"] = bool(vids) and bool(srts)
    r = uv(proj, "shorts.py", "list")
    checks["list: shows what's made"] = "pasos" in r.stdout and vids and vids[-1].name in r.stdout

    (proj / "03-delivery" / "copy_pasos.txt").write_text("## reels\nUn paso que nadie hace.\n#edicion\n",
                                                         encoding="utf-8")
    r = uv(proj, "qa.py", "--variant", "pasos", "--platform", "reels", "--no-listen")
    report = (proj / "03-delivery" / "qa" / "qa.md")
    txt = report.read_text(encoding="utf-8") if report.exists() else r.stdout
    checks["qa --variant: finds the short without a long video, reads copy_<short>.txt, looks for its covers"] = (
        r.returncode == 0 and "caption length" in txt and "reels: covers" in txt)
    checks["qa --variant: the .srt ends inside the video"] = "PASS  subtitles: inside the video" in r.stdout

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    n = sum(bool(v) for v in checks.values())
    print(f"{n}/{len(checks)} pass · {proj}")
    if n == len(checks):
        shutil.rmtree(proj, ignore_errors=True)
    sys.exit(0 if n == len(checks) else 1)


if __name__ == "__main__":
    main()
