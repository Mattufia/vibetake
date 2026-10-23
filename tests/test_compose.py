# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26", "opencv-python-headless>=4.9"]
# ///
"""Composition in render.py: whip/push transitions, picture-in-picture and split screen,
graphics behind the person.

    uv run tests/test_compose.py

Synthetic sources made with ffmpeg; every check measures the rendered pixels. The person's mask
is handed in ready (as matte.py would leave it), so the test needs no rembg nor GPU.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
sys.path.insert(0, str(SCRIPTS))
FF = shutil.which("ffmpeg")
W, H, FPS = 320, 180, 30
OUT = {"width": W, "height": H, "fps": FPS}


def ff(cwd: Path, *a: str) -> None:
    subprocess.run([FF, "-hide_banner", "-v", "error", "-y", *a], cwd=cwd, check=True)


def render(tmp: Path, segs: list[dict], out: str) -> list[np.ndarray]:
    (tmp / "02-work" / "edl.json").write_text(json.dumps({"version": 1, "output": OUT, "segments": segs}))
    p = subprocess.run(["uv", "run", str(SCRIPTS / "render.py"), "--out", out, "--no-grade", "--no-subs"], cwd=tmp,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode:
        sys.exit(p.stdout + p.stderr)
    cap, fr = cv2.VideoCapture(str(tmp / out)), []
    while True:
        ok, f = cap.read()
        if not ok:
            return fr
        fr.append(f.astype(np.float32))


def seg(tmp: Path, src: str, **kw) -> dict:
    return {"source": str(tmp / src), "name": src, "in": 0.0, "out": 2.0, **kw}


def color(f: np.ndarray, x: int, y: int) -> str:
    b, g, r = f[y - 2:y + 3, x - 2:x + 3].reshape(-1, 3).mean(0)
    return max((("red", r - g - b), ("green", g - r - b), ("blue", b - r - g)), key=lambda c: c[1])[0] if max(r, g, b) > 100 else "dark"


def main() -> None:
    checks = {}
    tmp = Path(tempfile.mkdtemp(prefix="vt-compose-"))
    (tmp / "02-work").mkdir()
    sil = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest"]
    enc = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p", "-c:a", "aac"]
    ff(tmp, "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={FPS}:d=4", *sil, *enc, "a.mp4")
    for c in ("red", "blue"):
        ff(tmp, "-f", "lavfi", "-i", f"color={c}:s={W}x{H}:r={FPS}:d=3", *sil, *enc, f"{c}.mp4")

    # Whip: the push's slide, blurred along the move in the middle of the transition only.
    pair = lambda typ: [seg(tmp, "a.mp4"), seg(tmp, "a.mp4", **{"in": 1.0, "out": 3.0,  # noqa: E731
                                                                 "transition": {"type": typ, "dur": 0.5}})]
    push, whip = render(tmp, pair("push"), "push.mp4"), render(tmp, pair("whip"), "whip.mp4")
    gx = lambda f: np.abs(np.diff(f.mean(2), axis=1)).mean()  # noqa: E731
    gy = lambda f: np.abs(np.diff(f.mean(2), axis=0)).mean()  # noqa: E731
    checks[f"whip: across detail {gx(whip[60]) / gx(push[60]):.2f} of the push's mid-transition, "
           f"up-down {gy(whip[60]) / gy(push[60]):.2f}"] = (
        gx(whip[60]) < 0.5 * gx(push[60]) and gy(whip[60]) > 0.6 * gy(push[60]))
    checks["whip: same picture as the push outside the transition"] = (
        np.abs(whip[30] - push[30]).mean() < 1 and np.abs(whip[100] - push[100]).mean() < 1)

    # Picture-in-picture top right (32% wide, 4% margin) and a split screen.
    f = render(tmp, [seg(tmp, "red.mp4", pip={"source": str(tmp / "blue.mp4"), "corner": "top-right"})], "pip.mp4")[30]
    box = (W - 7 - 102, 7, 102, 58)          # x, y, w, h
    checks["pip: the other source in the top-right box, the main picture around it"] = (
        color(f, box[0] + 51, box[1] + 29) == "blue" and color(f, box[0] - 10, 30) == "red"
        and color(f, 160, box[1] + box[3] + 10) == "red" and color(f, 40, 150) == "red")
    f = render(tmp, [seg(tmp, "red.mp4", split={"source": str(tmp / "blue.mp4"), "side": "left"})], "split.mp4")[30]
    checks["split left: the other source on the left half, the main on the right"] = (
        color(f, 60, 90) == "blue" and color(f, 260, 90) == "red")

    # Behind: the graphics layer (a blue box) between the background and the person (left half).
    ff(tmp, "-f", "lavfi", "-i", f"color=black:s={W}x{H}:r={FPS}:d=2,format=rgba,"
       "geq=r=0:g=0:b='255*between(X,40,279)*between(Y,40,139)':a='255*between(X,40,279)*between(Y,40,139)'", "-c:v", "png", "02-work/graphics.mov")
    s = seg(tmp, "red.mp4", behind=True)
    from render import matte_paths
    pic, mask = (tmp / p for p in matte_paths(s, OUT, {}))
    pic.parent.mkdir(parents=True)
    ff(tmp, "-f", "lavfi", "-i", f"color=green:s={W}x{H}:r={FPS}:d=2", "-c:v", "libx264", "-crf", "8",
       "-pix_fmt", "yuv420p", str(pic))
    ff(tmp, "-f", "lavfi", "-i", f"color=black:s={W}x{H}:r={FPS}:d=2,drawbox=x=0:y=0:w={W // 2}:h={H}:color=white:t=fill",
       "-c:v", "ffv1", "-pix_fmt", "gray", str(mask))
    front = render(tmp, [seg(tmp, "red.mp4")], "front.mp4")[30]
    back = render(tmp, [s], "behind.mp4")[30]
    checks["behind: without it, the graphics cover the person"] = color(front, 80, 90) == "blue"
    checks["behind: the person over the graphics, the graphics over the background beside them"] = (
        color(back, 80, 90) == "green" and color(back, 240, 90) == "blue" and color(back, 300, 20) == "red")

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
