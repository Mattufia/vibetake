# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""The stage (composition grammar), end to end through Remotion:

    uv run tests/test_stage.py        # needs Node; the first run installs the Remotion project (minutes)

Footage whose color changes every frame, cut by cutlist; a stage from 1 s to 5 s with a custom
frame and a page element. The still at 3 s must show the page background, the frame's title bar,
the page element in its corner, and inside the box the clean edit's exact frame at 3 s (the next
frame has another color, so this proves frame sync). Before and after the stage the final render
is the full-frame picture.
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
W, H = 1080, 1920

FRAME = """
import React from "react";
import { AbsoluteFill } from "remotion";
const Win: React.FC<any> = ({ children }) => (
  <div style={{ width: "100%", height: "100%", display: "flex", flexDirection: "column" }}>
    <div style={{ height: 60, background: "#00ff00", flex: "none" }} />
    <div style={{ flex: 1, minHeight: 0 }}>{children}</div>
  </div>);
const Corner: React.FC<any> = () => (
  <AbsoluteFill><div style={{ position: "absolute", right: 40, bottom: 40, width: 80, height: 80,
    background: "#0000ff" }} /></AbsoluteFill>);
export const CUSTOM: Record<string, React.FC<any>> = { Win, Corner };
"""


def run(cwd: Path, *a: str) -> str:
    return subprocess.run(["uv", "run", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True).stdout


def image(path: Path, t: float | None = None) -> np.ndarray:
    seek = ["-ss", f"{t:.4f}"] if t is not None else []
    raw = subprocess.run([FF, "-v", "error", *seek, "-i", str(path), "-frames:v", "1", "-vf", f"scale={W}:{H}",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(int)


def near(a, b, tol: int) -> bool:
    return int(np.abs(np.asarray(a) - np.asarray(b)).max()) < tol


def main() -> None:
    if not shutil.which("node"):
        print("SKIP  no Node.js: the stage needs Remotion")
        return
    # A short path: Remotion's headless browser fails past Windows' 260-character limit.
    tmp = Path(tempfile.mkdtemp(prefix="vt-stage-"))
    ing = tmp / "01-ingest"
    ing.mkdir()
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=black:s=320x180:r=30:d=31,format=rgb24,geq=r='mod(N*8\\,256)':g='mod(N*3\\,256)':b='128'",
                    "-i", str(FIX / "retakes_es.wav"), "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-crf", "1", "-pix_fmt", "yuv420p", "-c:a", "aac", "talk.mp4"], cwd=tmp, check=True)
    tr = json.loads((FIX / "retakes_es.transcript.json").read_text(encoding="utf-8"))
    tr["files"] = {"talk.mp4": tr["files"]["retakes_es.wav"]}
    for s in tr["sentences"]:
        s["file"] = "talk.mp4"
    (ing / "transcript.json").write_text(json.dumps(tr), encoding="utf-8")
    (ing / "material.json").write_text(json.dumps({"files": [
        {"name": "talk.mp4", "file": str(tmp / "talk.mp4"), "kind": "video", "duration": 31,
         "display_width": 320, "display_height": 180, "fps": 30}]}))
    run(tmp, str(SCRIPTS / "cutlist.py"), "--format", "vertical")
    run(tmp, str(SCRIPTS / "graphics.py"), "cues")
    w = tmp / "02-work"
    (w / "design.json").write_text(json.dumps({"colors": {"ink": "#111111", "paper": "#f3f2ee", "accent": "#ff5500"}}))
    (w / "graphics.json").write_text(json.dumps({"elements": [
        {"type": "stage", "from": {"t": 1}, "until": {"t": 5}, "background": "paper",
         "footage": {"x": 0.5, "y": 0.45, "width": 0.8, "height": 0.4, "radius": 12, "shadow": "soft", "frame": "Win"}},
        {"type": "custom", "from": {"t": 1}, "until": {"t": 5}, "component": "Corner", "page": True}]}))
    run(tmp, str(SCRIPTS / "graphics.py"), "init")
    (w / "remotion" / "src" / "custom" / "index.tsx").write_text(FRAME, encoding="utf-8")
    run(tmp, str(SCRIPTS / "graphics.py"), "still", "3.0")

    checks = {}
    layer = image(w / "graphics-stills" / "layer_003.00.png")
    top = int((0.45 - 0.2) * H)
    clean, nxt = image(w / "footage.mp4", 3.0), image(w / "footage.mp4", 3.0 + 1 / 30)
    checks["the clean edit was rendered for the stage and copied into the project"] = (
        (w / "footage.mp4").exists() and (w / "remotion" / "public" / "footage.mp4").exists())
    checks["page background"] = near(layer[100, W // 2], [0xF3, 0xF2, 0xEE], 4)
    checks["custom frame around the picture (title bar)"] = near(layer[top + 30, W // 2], [0, 255, 0], 10)
    checks["page element gets the whole frame"] = near(layer[H - 80, W - 80], [0, 0, 255], 10)
    inside = layer[top + 400, W // 2]
    checks[f"picture frame-synced to the edit ({inside.tolist()} vs {clean[H // 2, W // 2].tolist()}, "
           f"next frame {nxt[H // 2, W // 2].tolist()})"] = (
        near(inside, clean[H // 2, W // 2], 8) and not near(inside, nxt[H // 2, W // 2], 8))

    run(tmp, str(SCRIPTS / "graphics.py"), "render")
    run(tmp, str(SCRIPTS / "render.py"), "--preview", "--out", "final.mp4", "--no-grade", "--raw-audio")
    final, cl = tmp / "final.mp4", w / "footage.mp4"
    checks["outside the stage the final is the full-frame picture"] = all(
        near(image(final, t)[H // 2, 60], image(cl, t)[H // 2, 60], 12) for t in (0.5, 5.5))
    checks["during the stage the final shows the page"] = near(image(final, 3.0)[100, W // 2], [0xF3, 0xF2, 0xEE], 10)

    (w / "graphics.json").write_text('{"elements": []}', encoding="utf-8")
    run(tmp, str(SCRIPTS / "graphics.py"), "sync")
    checks["without a stage the footage leaves the project"] = not (w / "remotion" / "public" / "footage.mp4").exists()

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
