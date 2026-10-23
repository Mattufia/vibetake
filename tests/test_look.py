# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26", "opencv-python-headless>=4.9"]
# ///
"""Picture effects in render.py: fit over a blurred copy, mirror/rotate/tilt, chroma key,
blurred boxes that follow what they hide (redact.py), stabilization, the look.

    uv run tests/test_look.py

Synthetic sources made with ffmpeg; every check measures the rendered pixels.
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
FF = shutil.which("ffmpeg")
W, H, FPS = 320, 180, 30


def ff(cwd: Path, *a: str) -> None:
    subprocess.run([FF, "-hide_banner", "-v", "error", "-y", *a], cwd=cwd, check=True)


def render(tmp: Path, src: str, out: str, output: dict | None = None, **seg) -> list[np.ndarray]:
    (tmp / "02-work").mkdir(exist_ok=True)
    s = {"source": str(tmp / src), "name": src, "in": 0.0, "out": 2.0, **seg}
    (tmp / "02-work" / "edl.json").write_text(json.dumps(
        {"version": 1, "output": {"width": W, "height": H, "fps": FPS, **(output or {})}, "segments": [s]}))
    subprocess.run(["uv", "run", str(SCRIPTS / "render.py"), "--out", out, "--no-grade"], cwd=tmp,
                   capture_output=True, check=True)
    return read(tmp / out)


def read(p: Path) -> list[np.ndarray]:
    cap, out = cv2.VideoCapture(str(p)), []
    while True:
        ok, f = cap.read()
        if not ok:
            return out
        out.append(f.astype(np.float32))


def motion(fr: list[np.ndarray]) -> float:
    """Mean frame-to-frame shift of the picture (px)."""
    g = [cv2.cvtColor(f, cv2.COLOR_BGR2GRAY) for f in fr]
    return float(np.mean([np.hypot(*cv2.phaseCorrelate(a, b)[0]) for a, b in zip(g, g[1:])]))


def main() -> None:
    checks = {}
    tmp = Path(tempfile.mkdtemp(prefix="vt-look-"))
    sil = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest"]
    enc = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p", "-c:a", "aac"]
    ff(tmp, "-f", "lavfi", "-i", f"testsrc2=s=240x180:r={FPS}:d=3", *sil, *enc, "box.mp4")       # 4:3
    ff(tmp, "-f", "lavfi", "-i", f"testsrc2=s={W}x{H}:r={FPS}:d=3", *sil, *enc, "wide.mp4")
    ref = render(tmp, "wide.mp4", "plain_wide.mp4")

    # Fit: the 4:3 picture whole in the middle, a blurred darker copy at the sides; or bars.
    fr = render(tmp, "box.mp4", "fit.mp4", fit="blur")[30]
    side, mid = fr[:, :30], fr[:, 60:260]
    checks["fit blur: the sides are a soft copy (not black), the middle stays sharp"] = (
        side.mean() > 20 and cv2.Laplacian(side, cv2.CV_32F).var() < cv2.Laplacian(mid, cv2.CV_32F).var() / 4)
    fr = render(tmp, "box.mp4", "pad.mp4", output={"fit": "black"})[30]
    checks["fit black (for the whole edit): bars at the sides"] = fr[:, :30].mean() < 20 < fr[:, 60:260].mean()

    # Mirror, half turn, tilt.
    fr = render(tmp, "wide.mp4", "flip.mp4", flip="h")[30]
    checks["flip h: the picture mirrored"] = np.abs(fr - ref[30][:, ::-1]).mean() < 2
    fr = render(tmp, "wide.mp4", "r180.mp4", rotate=180)[30]
    checks["rotate 180: upside down"] = np.abs(fr - ref[30][::-1, ::-1]).mean() < 2
    fr = render(tmp, "wide.mp4", "r90.mp4", rotate=90)
    checks["rotate 90: renders at the output's size"] = fr[0].shape[:2] == (H, W)
    fr = render(tmp, "wide.mp4", "tilt.mp4", rotate=8)[30]
    corners = [fr[:4, :4], fr[:4, -4:], fr[-4:, :4], fr[-4:, -4:]]
    checks["tilt 8°: rotated and zoomed so no black corner shows"] = (
        min(c.mean() for c in corners) > 15 and np.abs(fr - ref[30]).mean() > 10)

    # Chroma: a green screen with a white square → the square over red.
    ff(tmp, "-f", "lavfi", "-i", f"color=0x00ff00:s={W}x{H}:r={FPS}:d=3,"
       "drawbox=x=120:y=50:w=80:h=80:c=white:t=fill", *sil, *enc, "green.mp4")
    fr = render(tmp, "green.mp4", "key.mp4", key={"color": "green", "bg": "red"})[30]
    b, g, r = fr[20, 20]
    checks[f"chroma: the green turns into the background (BGR {b:.0f},{g:.0f},{r:.0f}), the square stays"] = (
        r > 180 and g < 70 and fr[90, 160].min() > 200)

    # Redact: a textured square moving over noise, tracked from a box at the start.
    ff(tmp, "-f", "lavfi", "-i", f"nullsrc=s={W}x{H}:r={FPS}:d=3,geq=random(1)*80:128:128",
       "-f", "lavfi", "-i", f"testsrc=s=40x40:r={FPS}:d=3", "-filter_complex",
       "[0][1]overlay=x='40+t*80':y=70", *enc, "-an", "move.mp4")
    (tmp / "02-work").mkdir(exist_ok=True)
    (tmp / "02-work" / "edl.json").write_text(json.dumps(
        {"version": 1, "output": {"width": W, "height": H, "fps": FPS},
         "segments": [{"source": str(tmp / "move.mp4"), "name": "move.mp4", "in": 0.0, "out": 2.0}]}))
    log = subprocess.run(["uv", "run", str(SCRIPTS / "redact.py"), "1", "--box",
                          f"{40 / W},{70 / H},{40 / W},{40 / H}"], cwd=tmp, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", check=True).stdout
    seg = json.loads((tmp / "02-work" / "edl.json").read_text(encoding="utf-8"))["segments"][0]
    keys = seg["redact"][0]["keys"]
    end_x = np.interp(1.5, [k[0] for k in keys], [k[1] for k in keys]) * W
    checks[f"redact.py: the box follows the square (at 1.5 s x={end_x:.0f}, really 160)"] = (
        abs(end_x - 160) < 12 and (tmp / "02-work" / "redact_1.jpg").exists() and "WARNING" not in log)
    fr = render(tmp, "move.mp4", "red.mp4", redact=seg["redact"])
    x = round(40 + 1.5 * 80)
    square = fr[45][72:108, x + 4:x + 36]
    checks["redact: the square is blurred where it is (1.5 s), the rest untouched"] = (
        cv2.Laplacian(square, cv2.CV_32F).var() < 60 and cv2.Laplacian(fr[45][10:60, 10:60], cv2.CV_32F).var() > 300)

    # Stabilize: a window shaking over a still picture.
    ff(tmp, "-f", "lavfi", "-i", f"testsrc2=s=640x360:r={FPS}:d=3,"
       "crop=480:270:'80+30*sin(t*11)':'45+20*sin(t*17)',scale=640:360", *sil, *enc, "shaky.mp4")
    shaky = render(tmp, "shaky.mp4", "plain.mp4")
    steady = render(tmp, "shaky.mp4", "stab.mp4", stabilize=True)
    a, b = motion(shaky[5:-5]), motion(steady[5:-5])
    checks[f"stabilize: shake down from {a:.1f} to {b:.1f} px a frame"] = b < a / 2

    # The look, over the whole edit.
    plain = ref[30:40]
    fr = render(tmp, "wide.mp4", "look.mp4", output={"look": {"vignette": 1, "grain": 1, "bw": True}})[30:40]
    ycc = cv2.cvtColor(fr[0].astype(np.uint8), cv2.COLOR_BGR2YCrCb).astype(np.float32)
    corner = lambda f: f[:20, :20].mean() / max(f[80:100, 150:170].mean(), 1)  # noqa: E731
    checks["look: black and white, darker corners, grain that moves"] = (
        np.abs(ycc[..., 1:] - 128).max() < 8 and corner(fr[0]) < corner(plain[0]) * 0.8
        and np.std([f[80:100, 150:170].mean() for f in fr]) > np.std([f[80:100, 150:170].mean() for f in plain]))

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
