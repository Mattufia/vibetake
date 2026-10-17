# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""Extract full-resolution stills at given times.

    uv run still.py <file> 12.5 01:03.2 1:02:03 [--out 01-ingest/stills]

Times accept seconds, MM:SS(.s) or H:MM:SS(.s). For HDR footage pass the
normalized copy (01-ingest/normalized/) so the still shows the SDR result.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import run, tool  # noqa: E402


def seconds(t: str) -> float:
    parts = [float(p) for p in t.split(":")]
    return sum(p * 60 ** i for i, p in enumerate(reversed(parts)))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file")
    ap.add_argument("times", nargs="+")
    ap.add_argument("--out", default="01-ingest/stills")
    args = ap.parse_args()
    src, out = Path(args.file), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for t in args.times:
        s = seconds(t)
        dst = out / f"{src.stem}_{s:09.3f}.png"
        run([tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{s:.3f}", "-i", str(src), "-frames:v", "1", str(dst)])
        print(dst)


if __name__ == "__main__":
    main()
