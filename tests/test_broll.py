# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""B-roll tests: anchored placement, overlay, sound underneath, re-cut, hook candidates.

    uv run tests/test_broll.py

A gray main clip with a continuous tone; a red b-roll clip anchored to second 7 of
the main clip. After cutting [1–4] + [6–10], the red must show at timeline 4.0–5.0
while the tone keeps playing. Re-cut to [1–3] + [6–10]: the red follows to 3.0–4.0.
An anchor that was cut out is skipped with a warning. --cut centers b-roll on a join.
"""
from __future__ import annotations

import json
import re
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
W, H, FPS = 320, 180, 30


def run(cwd: Path, *a: str) -> str:
    p = subprocess.run(a, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode:
        raise RuntimeError(p.stdout + p.stderr)
    return p.stdout


def color_at(video: Path, t: float) -> tuple[float, float, float]:
    raw = subprocess.run([FF, "-v", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", "-vf",
                          "scale=32:18", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
    return tuple(np.frombuffer(raw, np.uint8).reshape(-1, 3).mean(0))


def red(c) -> bool:
    return c[0] > 150 and c[1] < 90


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-broll-"))
    checks = {}
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=gray:s=1280x720:r={FPS}:d=12",
                    "-f", "lavfi", "-i", "aevalsrc='0.3*sin(2*PI*440*t)':s=48000:d=12", "-shortest",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "main.mp4"],
                   cwd=tmp, check=True)
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=red:s=1280x720:r={FPS}:d=5",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "red.mp4"], cwd=tmp, check=True)
    (tmp / "01-ingest").mkdir()
    (tmp / "01-ingest" / "material.json").write_text(json.dumps({"files": [
        {"name": "main.mp4", "file": str(tmp / "main.mp4"), "kind": "video", "duration": 12,
         "display_width": 1280, "display_height": 720, "fps": FPS},
        {"name": "red.mp4", "file": str(tmp / "red.mp4"), "kind": "video", "duration": 5,
         "display_width": 1280, "display_height": 720, "fps": FPS, "broll_candidate": True}]}))
    (tmp / "02-work").mkdir()

    def edl(cuts, broll):
        (tmp / "02-work" / "edl.json").write_text(json.dumps({
            "version": 1, "output": {"width": W, "height": H, "fps": FPS},
            "segments": [{"source": str(tmp / "main.mp4"), "name": "main.mp4", "in": a, "out": b} for a, b in cuts],
            "broll": broll}))

    item = {"clip": "red.mp4", "source": str(tmp / "red.mp4"), "in": 1.0, "dur": 1.0,
            "anchor": {"name": "main.mp4", "time": 7.0}, "offset": 0.0}
    edl([(1, 4), (6, 10)], [item])
    run(tmp, "uv", "run", str(SCRIPTS / "render.py"), "--out", "a.mp4", "--no-grade")
    c = [color_at(tmp / "a.mp4", t) for t in (3.5, 4.5, 5.5)]
    checks["b-roll shows at its anchor (timeline 4.0–5.0)"] = not red(c[0]) and red(c[1]) and not red(c[2])
    x = np.frombuffer(subprocess.run([FF, "-v", "error", "-i", str(tmp / "a.mp4"), "-ac", "1", "-ar", "48000",
                                      "-f", "s16le", "-"], capture_output=True).stdout, np.int16) / 32768
    rms = lambda a, b: float(np.sqrt((x[int(a * 48000):int(b * 48000)] ** 2).mean()))  # noqa: E731
    checks["the edit's sound keeps playing under the b-roll"] = abs(rms(4.1, 4.9) / rms(5.1, 5.9) - 1) < 0.1

    edl([(1, 3), (6, 10)], [item])
    run(tmp, "uv", "run", str(SCRIPTS / "render.py"), "--out", "b.mp4", "--no-grade")
    c = [color_at(tmp / "b.mp4", t) for t in (2.5, 3.5, 4.5)]
    checks["re-cut: b-roll follows its anchor (now 3.0–4.0)"] = not red(c[0]) and red(c[1]) and not red(c[2])

    gone = {**item, "anchor": {"name": "main.mp4", "time": 5.0}}
    edl([(1, 3), (6, 10)], [gone])
    log = run(tmp, "uv", "run", str(SCRIPTS / "render.py"), "--out", "c.mp4", "--no-grade")
    checks["anchor cut out: skipped with a warning"] = "was cut out" in log and not red(color_at(tmp / "c.mp4", 2.5))

    edl([(1, 3), (6, 10)], [])
    out = run(tmp, "uv", "run", str(SCRIPTS / "broll.py"), "place", "--clip", "red.mp4", "--cut", "1", "--dur", "1.0")
    m = re.search(r"\[(\d+):(\d+\.\d)–(\d+):(\d+\.\d)\]", out)
    checks[f"--cut centers the b-roll on the join ({m.group(0) if m else out.strip()[:40]})"] = bool(
        m and float(m.group(2)) == 1.5 and float(m.group(4)) == 2.5)

    # Hook candidates on the retake fixture: the promise/conflict line ranks first.
    hk = tmp / "hk"
    (hk / "01-ingest").mkdir(parents=True)
    shutil.copy(FIX / "retakes_es.transcript.json", hk / "01-ingest" / "transcript.json")
    out = run(hk, "uv", "run", str(SCRIPTS / "hook.py"), "--top", "1")
    checks["hook: 'el error que todos cometen' ranks first"] = "error que todos cometen" in out

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
