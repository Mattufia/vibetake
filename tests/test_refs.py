# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""refs.py tests: the user's references become viewable, with a reference video's rhythm.

    uv run tests/test_refs.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SCRIPT = Path(__file__).resolve().parents[1] / "plugins" / "vibetake" / "skills" / "edit" / "scripts" / "refs.py"
FF = shutil.which("ffmpeg")


def ff(cwd: Path, *a: str) -> None:
    subprocess.run([FF, "-hide_banner", "-v", "error", "-y", *a], cwd=cwd, check=True)


def run(cwd: Path) -> str:
    p = subprocess.run(["uv", "run", str(SCRIPT)], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p.stdout + p.stderr


def main() -> None:
    checks = {}
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-refs-"))
    checks["no references → says so"] = "no references" in run(tmp)

    refs = tmp / "references"
    refs.mkdir()
    ff(refs, "-f", "lavfi", "-i", "color=red:s=640x360", "-frames:v", "1", "portada.png")
    ff(refs, "-f", "lavfi", "-i", "color=blue:s=640x360", "-frames:v", "1", "paleta.tif")
    # A 12 s reference reel with a hard cut every 3 s (4 different shots).
    ff(refs, "-f", "lavfi", "-i", "testsrc2=s=360x640:r=30:d=3", "-f", "lavfi", "-i", "smptebars=s=360x640:r=30:d=3",
       "-f", "lavfi", "-i", "mandelbrot=s=360x640:r=30,trim=duration=3", "-f", "lavfi", "-i", "color=black:s=360x640:r=30:d=3",
       "-filter_complex", "[0][1][2][3]concat=n=4:v=1[v]", "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
       "reel.mp4")
    (refs / "notas.txt").write_text("me gusta el ritmo")

    out = run(tmp)
    data = json.loads((tmp / "01-ingest" / "references.json").read_text(encoding="utf-8"))
    by = {e["name"]: e for e in data["references"]}
    checks["a PNG is looked at as is"] = by.get("portada.png", {}).get("view", "").endswith("portada.png")
    tif = by.get("paleta.tif", {})
    checks["a TIFF gets a JPG preview"] = tif.get("view", "").endswith(".jpg") and Path(tmp / tif["view"]).exists()
    reel = by.get("reel.mp4", {})
    checks[f"reference video: rhythm measured ({reel.get('shot_changes_per_min')}/min, "
           f"a shot every {reel.get('average_shot_s')} s)"] = (
        reel.get("kind") == "video" and abs(reel.get("average_shot_s", 0) - 3) < 0.3
        and all((tmp / s).exists() for s in reel.get("sheets", [])) and reel.get("sheets"))
    checks["other files listed to open"] = by.get("notas.txt", {}).get("kind") == "other" and "open it" in out
    run(tmp)
    checks["unchanged → reused"] = json.loads((tmp / "01-ingest" / "references.json").read_text(
        encoding="utf-8")) == data

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
