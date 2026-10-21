# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26", "opencv-python-headless>=4.9"]
# ///
"""joins.py tests: render.py --preview lays out every join and flags only clear mistakes.

    uv run tests/test_joins.py

A 20 s clip: brighter from 10 s, quieter from 15 s. Seven pieces give six joins:
  J1 cut 1 s with "eh bueno" in it     no flag, says what was cut
  J2 into a 0.1 s piece                 flash
  J3 out of it                          no flag
  J4 into the brighter part             light jump
  J5 into the quieter part              level jump
  J6 back to the start, under b-roll    level jump, no light flag (the cut is hidden)
The small-jump rule (needs a real face) is checked on its own.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
FF = shutil.which("ffmpeg")


def ff(cwd: Path, *a: str) -> None:
    subprocess.run([FF, "-hide_banner", "-v", "error", "-y", *a], cwd=cwd, check=True)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-joins-"))
    raw = tmp / "00-raw"
    raw.mkdir()
    ff(raw, "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=20,eq=brightness=0.3:enable='gte(t,10)'",
       "-f", "lavfi", "-i", "aevalsrc='(0.3-0.25*gte(t,15))*sin(2*PI*220*t)':s=48000:d=20",
       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "clip.mp4")
    ff(raw, "-f", "lavfi", "-i", "color=red:s=640x360:r=30:d=3", "-c:v", "libx264", "-preset", "ultrafast",
       "-pix_fmt", "yuv420p", "red.mp4")
    src = str(raw / "clip.mp4")
    pieces = [(1.0, 3.0), (4.0, 6.0), (6.0, 6.1), (8.0, 9.5), (11.0, 13.0), (16.0, 18.0), (3.0, 4.0)]
    edl = {"version": 1, "output": {"width": 640, "height": 360, "fps": 30},
           "segments": [{"source": src, "name": "clip.mp4", "in": a, "out": b} for a, b in pieces],
           # Over J6 (timeline 9.6 s): anchored 1.5 s into the 16–18 piece (timeline 9.1 s), 1 s long.
           "broll": [{"clip": "red.mp4", "source": str(raw / "red.mp4"), "in": 0.0, "dur": 1.0,
                      "anchor": {"name": "clip.mp4", "time": 17.5}, "offset": 0.0}]}
    (tmp / "02-work").mkdir()
    (tmp / "02-work" / "edl.json").write_text(json.dumps(edl), encoding="utf-8")
    (tmp / "01-ingest").mkdir()
    words = [{"word": " eh", "start": 3.2, "end": 3.4}, {"word": " bueno", "start": 3.45, "end": 3.8}]
    (tmp / "01-ingest" / "transcript.json").write_text(json.dumps(
        {"files": {"clip.mp4": {"segments": [{"start": 3.2, "end": 3.8, "words": words}]}}}), encoding="utf-8")

    p = subprocess.run(["uv", "run", str(SCRIPTS / "render.py"), "--preview", "--no-grade"], cwd=tmp,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    jd = tmp / "02-work" / "joins"
    md = (jd / "joins.md").read_text(encoding="utf-8") if (jd / "joins.md").exists() else ""
    print(md or p.stdout + p.stderr)
    block = {}
    for chunk in md.split("\nJ")[1:]:
        n = chunk.split()[0]
        block[n] = chunk

    checks = {
        "the preview lays out the joins on its own": "6 joins" in md,
        "J1: says what was cut, no flag": 'cut 1.0 s: "eh bueno"' in block.get("1", "") and "⚠" not in block.get("1", ""),
        "J2: a 0.1 s piece is a flash": "flash: the piece after" in block.get("2", ""),
        "J3: no flag": "⚠" not in block.get("3", ""),
        "J4: brighter → light jump": "light jump (brighter" in block.get("4", ""),
        "J5: quieter → level jump, not light": "level jump (quieter" in block.get("5", "")
        and "light jump" not in block.get("5", ""),
        "J6: under b-roll → no picture flag, sound still checked": "under b-roll" in block.get("6", "")
        and "light jump" not in block.get("6", "") and "level jump (louder" in block.get("6", "")
        and "jumps back" in block.get("6", ""),
        "a sheet with the frames of every join": bool(list(jd.glob("sheet_*.jpg"))),
    }
    reel = jd / "joins.mp4"
    if reel.exists():
        d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                  str(reel)], capture_output=True, text=True).stdout.strip() or 0)
        # Windows of ±1 s, merged where joins are close: much shorter than the 10.6 s edit, all joins in it.
        checks[f"joins.mp4: every join, short ({d:.1f} s)"] = 5.0 < d < 10.0
    else:
        checks["joins.mp4 written"] = False

    sys.path.insert(0, str(SCRIPTS))
    from joins import jump_flag
    checks["same zoom, the face moved: small jump"] = bool(jump_flag((300, 200, 100), (320, 200, 101)))
    checks["the zoom changed (punch-in): no flag"] = not jump_flag((300, 200, 100), (320, 205, 112))
    checks["the face barely moved: no flag"] = not jump_flag((300, 200, 100), (303, 201, 100))

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if "--keep" in sys.argv:
        print(f"kept: {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
