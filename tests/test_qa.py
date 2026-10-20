# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Delivery QA tests: a good file passes; planted faults are caught.

    uv run tests/test_qa.py
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
ENC = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-color_primaries", "bt709",
       "-color_trc", "bt709", "-colorspace", "bt709", "-c:a", "aac", "-ar", "48000"]
# Moving picture + speech-like modulated tone, mastered close to −14 LUFS.
VIDEO = "testsrc2=s=1080x1920:r=30:d={d}"
AUDIO = "aevalsrc='0.35*sin(2*PI*220*t)*(0.6+0.4*sin(2*PI*3*t))':s=48000:d={d}"


def ff(cwd: Path, *a: str) -> None:
    subprocess.run([FF, "-hide_banner", "-v", "error", "-y", *a], cwd=cwd, check=True)


def qa(cwd: Path, video: str, *extra: str) -> tuple[int, str]:
    p = subprocess.run(["uv", "run", str(SCRIPTS / "qa.py"), video, *extra], cwd=cwd, capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout


def status(out: str, check: str) -> str:
    return next((ln.split()[0] for ln in out.splitlines() if check in ln), "missing")


def master(cwd: Path, src: str, dst: str) -> None:
    """Bring the audio to −14 LUFS the same way render.py does."""
    sys.path.insert(0, str(SCRIPTS))
    from _common import Log
    import audio
    audio.master(cwd / src, cwd / dst, "social", Log(cwd / "log.txt"))


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-qa-"))
    checks = {}
    d = 8
    ff(tmp, "-f", "lavfi", "-i", VIDEO.format(d=d), "-f", "lavfi", "-i", AUDIO.format(d=d), "-shortest", *ENC,
       "-movflags", "+faststart", "raw.mp4")
    master(tmp, "raw.mp4", "good.mp4")
    (tmp / "good.srt").write_text("1\n00:00:00,500 --> 00:00:02,500\nUna línea corta\n\n"
                                  "2\n00:00:03,000 --> 00:00:05,000\nOtra línea\n", encoding="utf-8")
    code, out = qa(tmp, "good.mp4", "--platform", "reels")
    fails = [ln for ln in out.splitlines() if ln.startswith("FAIL")]
    checks[f"good file passes (exit {code})"] = code == 0 and not fails
    checks["good file: thumbnails missing is only a warning"] = status(out, "delivery: thumbnails") == "WARN"

    # Black gap of 2 s in the middle.
    ff(tmp, "-i", "good.mp4", "-vf", "drawbox=c=black:t=fill:enable='between(t,3,5)'", "-c:a", "copy",
       *ENC[:-4], "black.mp4")
    code, out = qa(tmp, "black.mp4")
    checks["2 s of black in the middle → FAIL"] = status(out, "picture: black frames") == "FAIL" and code == 1

    # Frozen picture for 3 s.
    ff(tmp, "-i", "good.mp4", "-filter_complex", "[0:v]split[a][b];[a][b]freezeframes=first=60:last=150:replace=60",
       "-map", "0:a", "-c:a", "copy", *ENC[:-4], "frozen.mp4")
    code, out = qa(tmp, "frozen.mp4")
    checks["frozen picture → flagged"] = status(out, "picture: frozen") == "WARN"

    # One second of digital silence in the middle (a dropout).
    ff(tmp, "-i", "good.mp4", "-c:v", "copy", "-af", "volume=enable='between(t,3,4)':volume=0", "-c:a", "aac",
       "-movflags", "+faststart", "dead.mp4")
    code, out = qa(tmp, "dead.mp4")
    checks["digital silence → WARN"] = status(out, "sound: no digital silence") == "WARN"
    _, out = qa(tmp, "good.mp4")
    checks["room sound → no digital-silence warning"] = status(out, "sound: no digital silence") == "PASS"

    # Quiet master (−8 dB) and no faststart.
    ff(tmp, "-i", "good.mp4", "-c:v", "copy", "-af", "volume=-8dB", "-c:a", "aac", "quiet.mp4")
    code, out = qa(tmp, "quiet.mp4")
    checks["loudness 8 dB low → FAIL"] = status(out, "sound: loudness") == "FAIL"
    checks["no faststart → WARN"] = status(out, "format: faststart") == "WARN"

    # Bad subtitles: a 60-character line and a 9 s cue.
    (tmp / "bad.srt").write_text("1\n00:00:00,500 --> 00:00:09,500\n" + "x" * 60 + "\n", encoding="utf-8")
    code, out = qa(tmp, "good.mp4", "--srt", "bad.srt")
    checks["subtitle rules broken → WARN"] = (status(out, "subtitles: line length") == "WARN"
                                              and status(out, "subtitles: cue duration") == "WARN"
                                              and status(out, "subtitles: inside the video") == "FAIL")

    # Captions drawn by the graphics layer: checked too, with Studio's yShift/scale applied.
    (tmp / "02-work" / "remotion" / "src").mkdir(parents=True)
    (tmp / "02-work" / "captions.json").write_text(json.dumps({"x": 0.46, "width": 0.7, "join": " ", "groups": [
        {"start": 0.5, "end": 2, "y": 0.68, "size": 0.045, "words": [
            {"text": "Una", "t": 0.5, "end": 0.8}, {"text": "línea", "t": 0.8, "end": 1.2}]}]}), encoding="utf-8")
    _, out = qa(tmp, "good.mp4")
    ok_in = status(out, "subtitles: drawn in the layer") == "PASS"
    (tmp / "02-work" / "remotion" / "src" / "Root.tsx").write_text(
        'defaultProps={{ captionStyle: { animation: "pop", yShift: 0.2, scale: 1.4 } }}', encoding="utf-8")
    _, out = qa(tmp, "good.mp4")
    checks["layer captions: inside → PASS; moved down in Studio → WARN"] = (
        ok_in and status(out, "subtitles: drawn in the layer") == "WARN")
    shutil.rmtree(tmp / "02-work")

    # Too long for Shorts.
    ff(tmp, "-f", "lavfi", "-i", "color=gray:s=108x192:r=30:d=200", "-f", "lavfi", "-i", AUDIO.format(d=200),
       "-shortest", *ENC, "long_raw.mp4")
    master(tmp, "long_raw.mp4", "long.mp4")
    code, out = qa(tmp, "long.mp4", "--platform", "shorts")
    checks["3 min 20 s for Shorts → FAIL"] = status(out, "shorts: length") == "FAIL"
    checks["108 px wide → resolution WARN"] = status(out, "shorts: resolution") == "WARN"

    # Cleanup only when nothing failed.
    (tmp / "02-work" / "render").mkdir(parents=True)
    (tmp / "02-work" / "render" / "part_000.mkv").write_bytes(b"0" * 1000)
    (tmp / "02-work" / "preview.mp4").write_bytes(b"0" * 1000)
    qa(tmp, "black.mp4", "--cleanup")
    kept = (tmp / "02-work" / "preview.mp4").exists()
    qa(tmp, "good.mp4", "--srt", "good.srt", "--cleanup")
    checks["cleanup: skipped on FAIL, done on PASS"] = kept and not (tmp / "02-work" / "preview.mp4").exists() \
        and not (tmp / "02-work" / "render").exists()

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
