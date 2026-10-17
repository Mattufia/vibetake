# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""Dump the metadata that identifies a camera and its color mode.

    uv run camera_report.py <clip> [--mode "D-Log M"]

Paste the output into an issue to add or verify a camera in
references/log-formats.md. Use an original file straight from the camera
(messaging apps, cloud photo services and editors strip metadata).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import ffprobe, run, tool  # noqa: E402

INTERESTING = re.compile(r"make|model|log|gamma|color|colour|profile|picture|transfer|primar|matrix|"
                         r"hdr|encoder|handler|software|firmware|lut|look", re.I)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("clip")
    ap.add_argument("--mode", help="the color mode you recorded in, as the camera names it")
    args = ap.parse_args()
    clip = Path(args.clip)

    info = ffprobe(clip)
    print(f"# camera report: {clip.name}")
    if args.mode:
        print(f"declared mode: {args.mode}")
    print("\n## ffprobe")
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            keys = ("codec_name", "profile", "pix_fmt", "width", "height", "r_frame_rate", "avg_frame_rate",
                    "color_range", "color_space", "color_transfer", "color_primaries")
            print(json.dumps({k: s.get(k) for k in keys}, ensure_ascii=False))
            print("side data:", json.dumps(s.get("side_data_list", []), ensure_ascii=False))
            print("stream tags:", json.dumps(s.get("tags", {}), ensure_ascii=False))
    print("format tags:", json.dumps(info.get("format", {}).get("tags", {}), ensure_ascii=False))

    exe = tool("exiftool", required=False)
    print("\n## exiftool")
    if not exe:
        print("(exiftool not installed: install it for a more complete report)")
        return
    data = json.loads(run([exe, "-j", "-G1", "-a", "-s", "-ee3", str(clip)], check=False).stdout or "[{}]")[0]
    for k, v in data.items():
        if INTERESTING.search(k) and not k.startswith("System:") and len(str(v)) < 300:
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
