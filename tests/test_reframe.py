# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Reframing tests: the camera-operator logic and the rendered crop.

    uv run tests/test_reframe.py

1. Operator: small jitter inside the dead zone → the camera holds; a big jump →
   it gets there without exceeding the speed limit; it never leaves the frame.
2. End to end: a white bar stands in for a face and jumps from left to right at
   t = 4 s. Rendered vertical, the bar must be centered before the jump, off-center
   right after it (the camera eases, it doesn't snap), and centered again later.
3. Punch-in alternates at a jump cut; a 1080p source cropped to vertical warns
   about the upscale.
4. Auto mode: a face that barely moves gets one fixed crop, a face that crosses the
   frame gets the operator, a vertical source for a vertical edit isn't reframed.
5. Impact zoom: right after the cut the bar is wider (closer) than once settled.
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
sys.path.insert(0, str(SCRIPTS))
import reframe  # noqa: E402

FF = shutil.which("ffmpeg")


def run(cwd: Path, *a: str) -> str:
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          check=True).stdout


def bar_position(video: Path, t: float) -> float:
    """Horizontal position (0-1) of the bright bar in the frame at t."""
    w, h = 108, 192
    raw = subprocess.run([FF, "-v", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", "-vf",
                          f"scale={w}:{h},format=gray", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    img = np.frombuffer(raw, np.uint8).reshape(h, w).astype(float)
    col = img.mean(0)
    return float((np.argmax(col) + 0.5) / w)


def main() -> None:
    checks = {}

    # 1. Operator logic on synthetic targets (crop 0.3 wide).
    rng = np.random.default_rng(1)
    jitter = 0.5 + rng.normal(0, 0.01, 100)
    cam = reframe.operate(jitter, 0.3)
    checks["jitter inside the dead zone: camera holds"] = float(cam.max() - cam.min()) < 0.005
    step = np.r_[np.full(30, 0.3), np.full(100, 0.4)]          # moves, but stays inside the crop
    cam = reframe.operate(step, 0.3)
    speed = np.abs(np.diff(cam)) * reframe.RATE
    checks["move inside the crop: reaches the target"] = abs(cam[-1] - 0.4) < 0.3 * reframe.DEADZONE
    checks["move inside the crop: eased, never faster than the limit"] = (
        float(speed.max()) <= reframe.MAX_SPEED * 0.3 + 1e-6 and speed[30] < 0.5 * float(speed.max()))
    jump = reframe.operate(np.r_[np.full(30, 0.3), np.full(30, 0.75)], 0.3)
    checks["face leaves the crop: the camera cuts to it"] = abs(jump[31] - 0.75) < 0.01
    edge = reframe.operate(np.full(50, 0.99), 0.3)
    checks["never leaves the frame"] = float(edge.max()) <= 1 - 0.15 + 1e-9

    # 2. End to end with a bar that jumps at t = 4 s.
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-reframe-"))
    W, H, D = 1920, 1080, 10
    # Two fixed pieces joined (drawbox doesn't re-evaluate its position over time).
    subprocess.run([FF, "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"color=black:s={W}x{H}:r=30:d=4,drawbox=x=536:y=300:w=80:h=400:c=white:t=fill",
                    "-f", "lavfi", "-i", f"color=black:s={W}x{H}:r=30:d={D - 4},"
                    "drawbox=x=690:y=300:w=80:h=400:c=white:t=fill",
                    "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo:d={D}",
                    "-filter_complex", "[0][1]concat=n=2:v=1[v]", "-map", "[v]", "-map", "2:a", "-shortest",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(tmp / "bar.mp4")],
                   check=True)
    ing = tmp / "01-ingest"
    ing.mkdir()
    samples = [{"t": t / 2, "faces": [[(0.3 if t / 2 < 4 else 0.38) - 0.02, 0.28, 0.04, 0.37, 0.99]]}
               for t in range(2 * D)]
    (ing / "faces.json").write_text(json.dumps({"fps": 2, "files": {"bar.mp4": samples}}))
    (ing / "material.json").write_text(json.dumps({"files": [
        {"name": "bar.mp4", "file": str(tmp / "bar.mp4"), "kind": "video", "duration": D,
         "display_width": W, "display_height": H, "fps": 30}]}))
    work = tmp / "02-work"
    work.mkdir()
    edl = {"version": 1, "output": {"width": 1080, "height": 1920, "fps": 30},
           "segments": [{"source": str(tmp / "bar.mp4"), "name": "bar.mp4", "in": 0.0, "out": D}]}
    (work / "edl.json").write_text(json.dumps(edl))
    out = run(tmp, "uv", "run", str(SCRIPTS / "reframe.py"), "--mode", "operator")
    run(tmp, "uv", "run", str(SCRIPTS / "render.py"), "--out", "v.mp4", "--no-grade")
    before, just_after, later = (bar_position(tmp / "v.mp4", t) for t in (2.0, 4.15, 8.5))
    checks[f"bar centered before the jump ({before:.2f})"] = abs(before - 0.5) < 0.12
    checks[f"camera eases, doesn't snap (right after: {just_after:.2f})"] = just_after > 0.53
    checks[f"bar centered again later ({later:.2f})"] = abs(later - 0.5) < 0.12
    checks["1080p → vertical warns about the upscale"] = "upscaled" in out

    # 3. Punch-in at a jump cut.
    edl["segments"] = [{"source": str(tmp / "bar.mp4"), "name": "bar.mp4", "in": 0.0, "out": 3.0},
                       {"source": str(tmp / "bar.mp4"), "name": "bar.mp4", "in": 3.5, "out": 6.0}]
    (work / "edl.json").write_text(json.dumps(edl))
    run(tmp, "uv", "run", str(SCRIPTS / "reframe.py"), "--zoom", "1.12")
    z = [s["frame"]["zoom"] for s in json.loads((work / "edl.json").read_text())["segments"]]
    checks[f"punch-in alternates at a jump cut ({z})"] = z[0] == 1.0 and z[1] > 1.0

    # 4. Auto mode on synthetic face targets (crop 0.32 wide, like 1080p → 9:16).
    still = 0.5 + rng.normal(0, 0.01, 50)
    cross = np.linspace(0.2, 0.8, 50)
    nan = np.full(50, np.nan)
    checks["auto: face barely moves → fixed crop"] = reframe.choose_framing("auto", False, still, nan, 0.32, 1) == "fixed"
    checks["auto: face crosses the frame → operator"] = \
        reframe.choose_framing("auto", False, cross, nan, 0.32, 1) == "operator"
    checks["auto: same aspect as the output → not reframed"] = \
        reframe.choose_framing("auto", True, cross, nan, 1, 1) == "full"
    checks["fixed crop sits at the middle of the travel"] = \
        abs(reframe.fixed_center(np.r_[0.4, 0.5], 0.32) - 0.45) < 1e-6

    # 5. Impact zoom: second segment enters closer, then settles.
    run(tmp, "uv", "run", str(SCRIPTS / "reframe.py"), "--punch", "auto", "--bump", "0.08", "--drift", "0")
    segs = json.loads((work / "edl.json").read_text())["segments"]
    checks["bump only after a cut, auto picks fixed for the still bar"] = (
        segs[0]["frame"]["bump"] == 0 and segs[1]["frame"]["bump"] == 0.08
        and all(s["frame"]["mode"] == "fixed" for s in segs))
    run(tmp, "uv", "run", str(SCRIPTS / "render.py"), "--out", "b.mp4", "--no-grade")

    def bar_width(t: float) -> int:
        raw = subprocess.run([FF, "-v", "error", "-ss", str(t), "-i", str(tmp / "b.mp4"), "-frames:v", "1", "-vf",
                              "format=gray", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
        img = np.frombuffer(raw, np.uint8).reshape(1920, 1080)
        return int((img[960] > 128).sum())
    hit, settled = bar_width(3.0 + 1 / 30), bar_width(4.0)
    checks[f"impact zoom: closer on entry ({hit} px) than settled ({settled} px)"] = hit > settled * 1.04

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
