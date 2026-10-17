# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""Synthetic regression tests for ingest.py (no faces or speech: those need real footage).

    uv run tests/test_ingest.py [--keep]

Generates small clips that reproduce each ingest trap, runs ingest.py on them
and checks what it detected.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
FFMPEG = shutil.which("ffmpeg")
D = 10  # seconds per clip
OFFSET = 1.234
# Irregular pink-noise bursts: distinctive enough for cross-correlation.
AUDIO = f"anoisesrc=d={D + 2}:c=pink:r=48000:a=0.5,volume='0.03+0.97*gt(sin(7*t)*sin(3.1*t+1),0.55)':eval=frame"
VIDEO = f"testsrc2=s=640x360:r=30:d={D}"


def ff(*args: str) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-v", "error", "-y", *args], check=True)


def make_fixtures(d: Path) -> None:
    raw = d / "00-raw"
    raw.mkdir(parents=True)
    h264 = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    x265_10 = ["-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le", "-x265-params", "log-level=error"]
    src = ["-f", "lavfi", "-i", VIDEO, "-f", "lavfi", "-i", AUDIO, "-shortest"]

    ff(*src, *h264, "-c:a", "aac", str(raw / "cfr.mp4"))
    # VFR: 5 s at 30 fps followed by 5 s at 12 fps, timestamps kept as-is.
    ff("-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=5", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=12:d=5",
       "-f", "lavfi", "-i", AUDIO, "-filter_complex", "[0][1]concat=n=2:v=1[v]", "-map", "[v]", "-map", "2:a",
       "-shortest", "-fps_mode", "vfr", *h264, "-c:a", "aac", str(raw / "vfr.mp4"))
    ff(*src, "-vf", "format=yuv420p,setparams=colorspace=bt709:color_trc=bt709:color_primaries=bt709,"
       "zscale=t=arib-std-b67:p=bt2020:m=bt2020nc,format=yuv420p10le", *x265_10,
       "-color_primaries", "bt2020", "-color_trc", "arib-std-b67", "-colorspace", "bt2020nc",
       "-c:a", "aac", str(raw / "hlg.mov"))
    ff("-display_rotation", "90", "-i", str(raw / "cfr.mp4"), "-c", "copy", str(raw / "rotated.mp4"))
    ff(*src, "-vf", "eq=contrast=0.45:saturation=0.15", *x265_10, "-c:a", "aac", str(raw / "flat10.mov"))
    ff("-i", str(raw / "cfr.mp4"), "-c", "copy", "-metadata", "comment=Gamma: S-Log3", str(raw / "loghint.mp4"))
    ff("-f", "lavfi", "-i", VIDEO, "-f", "lavfi", "-i", AUDIO, "-f", "lavfi", "-i", f"anoisesrc=d={D + 2}:a=0.3",
       "-filter_complex", "[1:a]asplit[a1][a2];[a1][2:a]amix=inputs=2:normalize=0[noisy]",
       "-map", "0:v", "-map", "[noisy]", "-map", "[a2]", "-shortest", *h264, "-c:a", "pcm_s16le",
       str(raw / "twotracks.mov"))
    # External recorder that started OFFSET s after the camera.
    ff("-f", "lavfi", "-i", AUDIO, "-af", f"atrim=start={OFFSET},asetpts=PTS-STARTPTS", "-t", "8",
       str(raw / "recorder.wav"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="keep the temp folder for inspection")
    args = ap.parse_args()
    if not FFMPEG:
        sys.exit("ffmpeg not found")
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-test-"))
    make_fixtures(tmp)
    subprocess.run(["uv", "run", str(SCRIPTS / "ingest.py"), "00-raw", "--out", "01-ingest", "--no-transcribe"],
                   cwd=tmp, check=True)
    res = json.loads((tmp / "01-ingest" / "material.json").read_text(encoding="utf-8"))
    f = {m["name"]: m for m in res["files"]}

    checks = {
        "cfr: not VFR, not HDR": not f["cfr.mp4"]["vfr"] and not f["cfr.mp4"]["hdr"],
        "cfr: no normalized copy": "normalized" not in f["cfr.mp4"],
        "cfr: not flagged as flat": not f["cfr.mp4"]["faces"]["image"]["flat"],
        "vfr: detected": f["vfr.mp4"]["vfr"],
        "vfr: normalized copy exists": Path(tmp / f["vfr.mp4"]["normalized"]["file"]).exists(),
        "hlg: detected": f["hlg.mov"]["hdr"] == "HLG",
        "hlg: converted": any("SDR" in n for n in f["hlg.mov"]["normalized"]["notes"]),
        "rotated: 90° and vertical": abs(f["rotated.mp4"]["rotation"]) == 90
                                     and f["rotated.mp4"]["orientation"] == "vertical",
        "flat10: flagged flat": f["flat10.mov"]["faces"]["image"]["flat"],
        "loghint: S-Log3 from metadata": "S-Log3" in (f["loghint.mp4"]["log_hint"] or ""),
        "twotracks: suggests clean track 2": f["twotracks.mov"].get("suggested_audio_track") == 1,
        "contact sheets written": all(m["shots"]["sheets"] for m in res["files"] if m["kind"] == "video"),
        "recorder: synced to a video": bool(res["external_audio"] and res["external_audio"][0].get("video")),
        f"recorder: offset ≈ +{OFFSET} s": res["external_audio"]
                                           and abs(res["external_audio"][0].get("offset_s", 99) - OFFSET) < 0.01,
    }
    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    if res["external_audio"]:
        print("recorder result:", res["external_audio"][0])
    print(f"\nfiles in {tmp}" if args.keep else "")
    if not args.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
