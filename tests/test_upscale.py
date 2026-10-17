# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""AI upscaling tests (upscale.py + render.py). Skipped if realesrgan-ncnn-vulkan isn't installed.

    uv run tests/test_upscale.py

A 720p horizontal clip cropped to vertical is stretched; upscale.py lists it, a trial
leaves a comparison and stores nothing, the full run makes render.py use the AI
picture (sharper than the plain stretch, same duration, sound from the original),
and a change to the segment makes it stale (render falls back to the original).
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
FF = shutil.which("ffmpeg")
HOME_TOOL = Path.home() / ".vibetake" / "tools" / "realesrgan"


def run(cwd: Path, *a: str) -> str:
    p = subprocess.run(["uv", "run", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if p.returncode:
        raise SystemExit(f"{a[0]} failed:\n{p.stdout}\n{p.stderr}")
    return p.stdout + p.stderr


def frame(video: Path, t: float) -> np.ndarray:
    raw = subprocess.run([FF, "-v", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", "-vf",
                          "scale=540:960", "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True,
                         check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(960, 540).astype(float)


def main() -> None:
    if not (shutil.which("realesrgan-ncnn-vulkan") or any(HOME_TOOL.glob("realesrgan-ncnn-vulkan*"))):
        print("SKIP  realesrgan-ncnn-vulkan not installed")
        return
    checks = {}
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-up-"))
    ing, work = tmp / "01-ingest", tmp / "02-work"
    ing.mkdir()
    work.mkdir()
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=3", "-f", "lavfi",
                    "-i", "sine=f=440:d=3:r=48000", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "clip.mp4"], cwd=tmp, check=True)
    (ing / "material.json").write_text(json.dumps({"files": [
        {"name": "clip.mp4", "file": str(tmp / "clip.mp4"), "kind": "video", "duration": 3,
         "display_width": 1280, "display_height": 720, "fps": 30}]}))
    edl = {"version": 1, "output": {"width": 540, "height": 960, "fps": 30},
           "segments": [{"source": str(tmp / "clip.mp4"), "name": "clip.mp4", "in": 0.0, "out": 1.0}]}
    (work / "edl.json").write_text(json.dumps(edl))
    run(tmp, str(SCRIPTS / "reframe.py"), "--punch", "off")

    out = run(tmp, str(SCRIPTS / "upscale.py"), "list")
    checks["list: the stretched segment shows"] = "seg 0" in out

    run(tmp, str(SCRIPTS / "upscale.py"), "run", "--seg", "0", "--test", "0.3")
    checks["trial: comparison still, nothing stored"] = (work / "upscale" / "seg_0_compare.png").exists() and \
        "upscaled" not in json.loads((work / "edl.json").read_text())["segments"][0]

    run(tmp, str(SCRIPTS / "render.py"), "--out", "plain.mp4", "--no-grade")
    run(tmp, str(SCRIPTS / "upscale.py"), "run", "--seg", "0")
    seg = json.loads((work / "edl.json").read_text())["segments"][0]
    checks["run: stored on the segment"] = Path(seg.get("upscaled", {}).get("file", "")).exists()
    log = run(tmp, str(SCRIPTS / "render.py"), "--out", "ai.mp4")
    # The AI picture smooths stair-stepping and compression noise (it isn't "sharper" by a
    # gradient measure): check the render shows it, not the plain stretch.
    ai, plain, up = (frame(p, 0.5) for p in (tmp / "ai.mp4", tmp / "plain.mp4", Path(seg["upscaled"]["file"])))
    d_up, d_plain = float(np.abs(ai - up).mean()), float(np.abs(ai - plain).mean())
    checks[f"render uses the AI picture (off by {d_up:.1f} from it, {d_plain:.1f} from the stretch)"] =         d_up < d_plain / 2
    pr = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration",
                                    "-of", "json", str(tmp / "ai.mp4")], capture_output=True, text=True).stdout)
    durs = {s["codec_type"]: float(s["duration"]) for s in pr["streams"]}
    checks[f"same duration, sound kept ({durs})"] = "audio" in durs and abs(durs["video"] - 1.0) < 0.05

    e = json.loads((work / "edl.json").read_text())
    e["segments"][0]["out"] = 0.9
    (work / "edl.json").write_text(json.dumps(e))
    log = run(tmp, str(SCRIPTS / "render.py"), "--out", "stale.mp4")
    checks["a changed segment makes it stale (falls back, warns)"] = "stale" in log

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
