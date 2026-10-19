# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Regression tests for bugs that broke real edits.

    uv run tests/test_fixes.py

1. Re-cutting keeps what was placed on the edit (b-roll, sound effects, music); a new cut
   file (a variant) starts from the main edit's placements.
2. render.py refuses a master with captions or a graphics layer made for another cut
   (a preview only warns; --allow-stale overrides); captions made for this cut render.
3. HDR material without a grade is tone-mapped at render (never washed out); without its
   normalized copy, analysis tone-maps the original on the fly.
4. A log LUT passed for a project isn't applied to the files that aren't log.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
FIX = ROOT / "tests" / "fixtures"
FF = shutil.which("ffmpeg")
sys.path.insert(0, str(SCRIPTS))
from _common import pick_tonemap, pre_filter, sdr_source  # noqa: E402
from _timeline import signature  # noqa: E402


def ff(cwd: Path, *a: str) -> None:
    subprocess.run([FF, "-hide_banner", "-v", "error", "-y", *a], cwd=cwd, check=True)


def script(cwd: Path, name: str, *a: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["uv", "run", str(SCRIPTS / name), *a], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=check)


def mean_luma(path: Path) -> float:
    raw = subprocess.run([FF, "-v", "error", "-i", str(path), "-frames:v", "1", "-vf", "scale=64:36",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True).stdout
    return float(np.frombuffer(raw, np.uint8).mean())


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-fixes-"))
    checks = {}

    # 1. Re-cut keeps placements.
    proj = tmp / "proj"
    (proj / "01-ingest").mkdir(parents=True)
    shutil.copy(FIX / "retakes_es.transcript.json", proj / "01-ingest" / "transcript.json")
    (proj / "01-ingest" / "material.json").write_text(json.dumps({"files": [
        {"name": "retakes_es.wav", "file": str(FIX / "retakes_es.wav"), "kind": "audio"}]}))
    script(proj, "cutlist.py", "--keep", "auto")
    ep = proj / "02-work" / "edl.json"
    edl = json.loads(ep.read_text(encoding="utf-8"))
    edl.update({"name": "demo", "broll": [{"clip": "x.mp4", "line": "L2"}], "sfx": [{"kind": "pop", "cut": 1}],
                "music": [{"file": "m.wav", "gain": -3}]})
    ep.write_text(json.dumps(edl), encoding="utf-8")
    out = script(proj, "cutlist.py", "--keep", "auto", "--pace", "tight").stdout
    again = json.loads(ep.read_text(encoding="utf-8"))
    checks["re-cut keeps b-roll, sfx, music and the name"] = (
        again.get("broll") and again.get("sfx") and again.get("music") and again.get("name") == "demo"
        and "kept from the previous edit" in out)
    script(proj, "cutlist.py", "--keep", "auto", "--out", "02-work/edl_B.json")
    variant = json.loads((proj / "02-work" / "edl_B.json").read_text(encoding="utf-8"))
    checks["a variant cut starts with the main edit's placements"] = bool(variant.get("broll"))

    # 2. Stale captions / graphics.
    ff(tmp, "-f", "lavfi", "-i", "color=gray:s=320x180:r=30:d=4", "-f", "lavfi", "-i", "sine=f=300:d=4",
       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "clip.mp4")
    edl = {"version": 1, "output": {"width": 320, "height": 180, "fps": 30},
           "segments": [{"source": str(tmp / "clip.mp4"), "name": "clip.mp4", "in": 0.5, "out": 3.0,
                         "frame": {"mode": "full", "zoom": 1.0, "crop": [1.0, 1.0], "keys": [[0.0, 0.5, 0.5]],
                                   "bump": 0.0, "drift": 0.0}}]}
    (tmp / "02-work").mkdir(exist_ok=True)
    (tmp / "02-work" / "edl.json").write_text(json.dumps(edl))
    ass = ("[Script Info]\n; vibetake-timeline: {sig}\nScriptType: v4.00+\nPlayResX: 320\nPlayResY: 180\n\n"
           "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
           "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
           "Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Main,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00000000,"
           "&H80000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1\n\n[Events]\nFormat: Layer, Start, End, Style, Name, "
           "MarginL, MarginR, MarginV, Effect, Text\nDialogue: 0,0:00:00.20,0:00:01.50,Main,,0,0,0,,hola\n")
    subs = tmp / "02-work" / "subs.ass"
    subs.write_text(ass.format(sig="0123abcd4567"), encoding="utf-8")
    master = script(tmp, "render.py", "--out", "stale.mp4", "--no-grade", check=False)
    checks["stale captions: the master is refused"] = master.returncode != 0 and "another cut" in (
        master.stdout + master.stderr)
    preview = script(tmp, "render.py", "--preview", "--no-grade", check=False)
    checks["stale captions: a preview renders, with a warning"] = (
        preview.returncode == 0 and "another cut" in preview.stdout + preview.stderr)
    checks["--allow-stale renders the master anyway"] = script(
        tmp, "render.py", "--out", "forced.mp4", "--no-grade", "--allow-stale", check=False).returncode == 0
    subs.write_text(ass.format(sig=signature(edl)), encoding="utf-8")
    checks["captions made for this cut render"] = script(
        tmp, "render.py", "--out", "ok.mp4", "--no-grade", check=False).returncode == 0
    subs.unlink()
    (tmp / "02-work" / "graphics.meta.json").write_text(json.dumps({"timeline": "feedbeef0000", "width": 320,
                                                                    "height": 180}))
    ff(tmp, "-f", "lavfi", "-i", "color=black@0.0:s=320x180:r=30:d=3,format=yuva444p10le", "-c:v", "prores_ks",
       "-profile:v", "4444", "02-work/graphics.mov")
    gm = script(tmp, "render.py", "--out", "g.mp4", "--no-grade", check=False)
    checks["stale graphics layer: the master is refused"] = gm.returncode != 0 and "graphics layer" in (
        gm.stdout + gm.stderr)
    (tmp / "02-work" / "graphics.mov").unlink()
    (tmp / "02-work" / "graphics.meta.json").unlink()

    # 3. HDR without a grade.
    tm = pick_tonemap()[1]
    checks["HDR without a grade gets the tone mapping"] = tm in pre_filter({"hdr": "HLG"}, None)
    checks["HDR with a graded pre-filter isn't tone-mapped twice"] = pre_filter(
        {"hdr": "HLG"}, {"pre": tm}).count(tm) == 1
    checks["SDR material: nothing added"] = pre_filter({"hdr": None}, None) == ""
    checks["normalized copy cleaned up → the original, tone-mapped on the fly"] = sdr_source(
        {"hdr": "HLG", "file": "a.mov", "normalized": {"file": str(tmp / "gone.mov")}}) == ("a.mov", tm)
    ff(tmp, "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=3,format=yuv420p10le", "-c:v", "libx265", "-preset",
       "ultrafast", "-x265-params", "log-level=error:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc",
       "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc", "-pix_fmt",
       "yuv420p10le", "hlg.mov")
    hdr = tmp / "hdr"
    (hdr / "02-work").mkdir(parents=True)
    (hdr / "02-work" / "edl.json").write_text(json.dumps({"version": 1, "output": {"width": 320, "height": 180,
                                                                                   "fps": 30},
        "segments": [{"source": str(tmp / "hlg.mov"), "name": "hlg.mov", "in": 0.2, "out": 2.5, "frame": {}}]}))
    script(hdr, "render.py", "--out", "plain.mp4", "--no-grade")
    (hdr / "01-ingest").mkdir()
    (hdr / "01-ingest" / "material.json").write_text(json.dumps({"files": [
        {"name": "hlg.mov", "file": str(tmp / "hlg.mov"), "hdr": "HLG"}]}))
    script(hdr, "render.py", "--out", "mapped.mp4", "--no-grade")
    a, b = mean_luma(hdr / "plain.mp4"), mean_luma(hdr / "mapped.mp4")
    checks[f"render tone-maps HDR flagged in material.json (luma {a:.0f} → {b:.0f})"] = abs(a - b) > 2

    # 4. A log LUT isn't applied to non-log files.
    n = 17
    v = np.linspace(0, 1, n)
    bb, gg, rr = np.meshgrid(v, v, v, indexing="ij")
    lut = np.stack([rr, gg, bb], -1).reshape(-1, 3) * 0.4          # a "conversion" that darkens a lot
    (tmp / "dark.cube").write_text(f"LUT_3D_SIZE {n}\n" + "\n".join(f"{x:.5f} {y:.5f} {z:.5f}" for x, y, z in lut))
    script(tmp, "grade.py", "analyze", "clip.mp4", "--out", "a1")
    script(tmp, "grade.py", "analyze", "clip.mp4", "--input-lut", "dark.cube", "--out", "a2")
    l1 = json.loads((tmp / "a1" / "color.json").read_text())["clip.mp4"]["median_luma"]
    l2 = json.loads((tmp / "a2" / "color.json").read_text())["clip.mp4"]["median_luma"]
    checks[f"log LUT not applied to a non-log file ({l1} = {l2})"] = abs(l1 - l2) < 0.01

    checks = {k: bool(v) for k, v in checks.items()}
    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"{sum(checks.values())}/{len(checks)} pass · {tmp}")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
