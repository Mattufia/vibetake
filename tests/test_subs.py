# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Subtitle tests.

    uv run tests/test_subs.py

On the retake fixture, cut with cutlist (retakes and fillers out):
1. .srt: ≤2 lines of ≤42 characters, 1–7 s, readable speed, no overlaps, nothing
   that was cut (no "mos...", no "Bueno", no "perdón").
2. Burned-in: groups of ≤3 words, one highlight event per word.
3. Face avoidance: a face low in the frame pushes the captions above it; a face high
   leaves them low. Always inside the vertical safe zone.
4. Burn-in renders: text pixels appear where the caption is placed.
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


def run(cwd: Path, *a: str) -> str:
    p = subprocess.run(a, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode:
        raise RuntimeError(p.stdout + p.stderr)
    return p.stdout


def srt_cues(text: str) -> list[dict]:
    cues = []
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        a, b = lines[1].split(" --> ")
        sec = lambda s: sum(float(x) * m for x, m in zip(s.replace(",", ".").split(":"), (3600, 60, 1)))  # noqa: E731
        cues.append({"start": sec(a), "end": sec(b), "lines": lines[2:]})
    return cues


def ass_positions(text: str) -> list[int]:
    return [int(y) for y in re.findall(r"\\pos\(\d+,(\d+)\)", text)]


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-subs-"))
    checks = {}
    ing = tmp / "01-ingest"
    ing.mkdir()
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "color=0x405060:s=1920x1080:r=30:d=31",
                    "-i", str(FIX / "retakes_es.wav"), "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "talk.mp4"], cwd=tmp, check=True)
    tr = json.loads((FIX / "retakes_es.transcript.json").read_text(encoding="utf-8"))
    tr["files"] = {"talk.mp4": tr["files"]["retakes_es.wav"]}
    for s in tr["sentences"]:
        s["file"] = "talk.mp4"
    (ing / "transcript.json").write_text(json.dumps(tr), encoding="utf-8")
    (ing / "material.json").write_text(json.dumps({"files": [
        {"name": "talk.mp4", "file": str(tmp / "talk.mp4"), "kind": "video", "duration": 31,
         "display_width": 1920, "display_height": 1080, "fps": 30}]}))
    run(tmp, "uv", "run", str(SCRIPTS / "cutlist.py"), "--format", "vertical")

    # 1–2 without faces.
    run(tmp, "uv", "run", str(SCRIPTS / "subtitles.py"), "--engine", "ass", "--font", "Arial Black")
    cues = srt_cues(next((tmp / "03-delivery").glob("*.srt")).read_text(encoding="utf-8"))
    all_text = " ".join(" ".join(c["lines"]) for c in cues).lower()
    checks["srt: ≤2 lines of ≤42 characters"] = all(len(c["lines"]) <= 2 and all(len(x) <= 42 for x in c["lines"])
                                                     for c in cues)
    checks["srt: 1–7 s each, no overlaps"] = all(1 - 1e-3 <= c["end"] - c["start"] <= 7 for c in cues) and all(
        a["end"] <= b["start"] for a, b in zip(cues, cues[1:]))
    cps = max(len(" ".join(c["lines"])) / (c["end"] - c["start"]) for c in cues)
    checks[f"srt: readable speed (max {cps:.1f} chars/s)"] = cps <= 20
    checks["srt: nothing that was cut"] = not any(x in all_text for x in ("mos...", "bueno", "perdón"))
    ass = (tmp / "02-work" / "subs.ass").read_text(encoding="utf-8")
    events = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    groups = {re.sub(r"\{[^}]*\}", "", e.split(",,", 1)[1]) for e in events}
    checks["burned-in: groups of ≤3 words"] = all(len(g.split()) <= 3 for g in groups)
    n_words = sum(len(g.split()) for g in groups)
    checks["burned-in: one highlight event per word"] = len(events) == n_words

    # 3. Face low in the frame → captions above it; face high → captions stay low.
    def with_face(y: float) -> list[int]:
        samples = [{"t": t / 2, "faces": [[0.45, y, 0.10, 0.18, 0.99]]} for t in range(62)]
        (ing / "faces.json").write_text(json.dumps({"fps": 2, "files": {"talk.mp4": samples}}))
        run(tmp, "uv", "run", str(SCRIPTS / "subtitles.py"), "--engine", "ass", "--font", "Arial Black")
        return ass_positions((tmp / "02-work" / "subs.ass").read_text(encoding="utf-8"))
    low, high = with_face(0.62), with_face(0.15)
    face_top_px = (0.62 - 0.3 * 0.18) * 1920
    checks["face low: captions move above it"] = all(y < face_top_px for y in low)
    checks["face high: captions stay low (default)"] = all(abs(y - 0.70 * 1920) < 2 for y in high)
    checks["always inside the safe zone"] = all(0.13 * 1920 <= y <= 0.77 * 1920 for y in low + high)

    # 4. Burn-in renders text where it's placed.
    (ing / "faces.json").unlink()
    run(tmp, "uv", "run", str(SCRIPTS / "subtitles.py"), "--engine", "ass", "--font", "Arial Black")
    run(tmp, "uv", "run", str(SCRIPTS / "render.py"), "--preview", "--out", "p.mp4", "--no-grade")
    t = cues[0]["start"] + 0.3
    raw = subprocess.run([FF, "-v", "error", "-ss", str(t), "-i", str(tmp / "p.mp4"), "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True).stdout
    img = np.frombuffer(raw, np.uint8).reshape(960, 540)
    band, top = img[int(0.66 * 960):int(0.74 * 960)], img[int(0.30 * 960):int(0.40 * 960)]
    checks["burn-in: caption pixels in the caption band, not elsewhere"] = (band.max() > 200 and band.min() < 40
                                                                            and top.max() < 150)

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
