# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""finish.py: lists without deleting, then frees the heavy files and keeps the finals.

    uv run tests/test_finish.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SCRIPT = Path(__file__).resolve().parents[1] / "plugins" / "vibetake" / "skills" / "edit" / "scripts" / "finish.py"

GONE = ["01-ingest/proxy.mp4", "02-work/preview.mp4", "02-work/color/clip.wav",
        "02-work/remotion/node_modules/x/index.js", "03-delivery/promo_v1.mp4", "03-delivery/promo_v2.mp4",
        "03-delivery/promo_v1_hookB.mp4"]
KEPT = ["00-raw/clip.mov", "brand/logo.png", "project.md", "01-ingest/transcript.json", "02-work/edl.json",
        "02-work/remotion/src/Main.tsx", "03-delivery/promo_v3.mp4", "03-delivery/promo_v2_hookB.mp4",
        "03-delivery/promo.srt", "03-delivery/cover_reels_1.jpg", "03-delivery/copy.txt", "03-delivery/qa/qa.md"]


def run(root: Path, *a: str) -> subprocess.CompletedProcess:
    return subprocess.run(["uv", "run", str(SCRIPT), "--root", str(root), *a], capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main() -> None:
    checks = {}
    root = Path(tempfile.mkdtemp(prefix="vt-finish-"))
    (root / "03-delivery").mkdir()
    checks["no final video → refuses"] = run(root).returncode != 0
    for f in GONE + KEPT:
        (root / f).parent.mkdir(parents=True, exist_ok=True)
        (root / f).write_bytes(b"x" * 1000)
    r = run(root)
    checks["without --yes: lists, deletes nothing"] = (
        r.returncode == 0 and "would free" in r.stdout and all((root / f).exists() for f in GONE + KEPT))
    r = run(root, "--yes")
    checks["--yes: heavy files, node_modules and older versions gone"] = not [f for f in GONE if (root / f).exists()]
    checks["--yes: originals, finals and the edit's text files kept"] = all((root / f).exists() for f in KEPT)
    checks["--yes: empty folders removed"] = not (root / "02-work" / "color").exists()
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(root, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
