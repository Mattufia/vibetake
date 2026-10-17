# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Audio tests: cleanup on the raw file, loudness master, music ducking.

    uv run tests/test_audio.py

The retake fixture's voice plus background noise, as a video:
1. audio.py clean lowers the noise floor, sets speech to −20 LUFS and keeps the
   timing exactly (no delay against the original).
2. render.py masters to the destination: social −14 LUFS / ≤ −1 dBTP, podcast −16.
3. A 60 Hz music bed is ducked while the voice talks and comes back in the pauses.
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
    return p.stdout + p.stderr


def pcm(path: Path, rate: int = 16000) -> np.ndarray:
    raw = subprocess.run([FF, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(rate), "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768


def db_windows(x: np.ndarray, win: int = 800) -> np.ndarray:
    n = len(x) // win
    return 20 * np.log10(np.sqrt((x[:n * win].reshape(n, win) ** 2).mean(1)) + 1e-9)


def loudness(path: Path) -> tuple[float, float]:
    err = subprocess.run([FF, "-hide_banner", "-nostats", "-i", str(path), "-af",
                          "ebur128=peak=true:framelog=verbose", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stderr
    s = err[err.rfind("Summary:"):]
    return (float(re.search(r"I:\s+(-?[\d.]+) LUFS", s).group(1)),
            float(re.search(r"Peak:\s+(-?[\d.]+) dBFS", s).group(1)))


def tone_level(x: np.ndarray, rate: int, f: float, a: float, b: float) -> float:
    seg = x[int(a * rate):int(b * rate)]
    t = np.arange(len(seg)) / rate
    return 20 * np.log10(abs(np.dot(seg, np.exp(-2j * np.pi * f * t))) * 2 / len(seg) + 1e-9)


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-audio-"))
    checks = {}
    (tmp / "00-raw").mkdir()
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=320x180:r=30:d=30",
                    "-i", str(FIX / "retakes_es.wav"), "-f", "lavfi", "-i", "anoisesrc=c=pink:a=0.006:d=30",
                    "-filter_complex", "[1:a]volume=0.5[v];[v][2:a]amix=inputs=2:normalize=0:duration=first[a]",
                    "-map", "0:v", "-map", "[a]", "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "00-raw/talk.mov"], cwd=tmp, check=True)
    run(tmp, "uv", "run", str(SCRIPTS / "ingest.py"), "00-raw", "--no-transcribe")
    run(tmp, "uv", "run", str(SCRIPTS / "audio.py"), "clean")
    clean = json.loads((tmp / "01-ingest" / "audio" / "clean.json").read_text())["talk.mov"]
    clean["file"] = str(tmp / clean["file"])           # paths are relative to the project folder
    raw, cl = pcm(tmp / "00-raw" / "talk.mov"), pcm(Path(clean["file"]))
    floor_raw, floor_cl = np.percentile(db_windows(raw), 10), np.percentile(db_windows(cl) - clean["gain_db"], 10)
    checks[f"clean: noise floor down ≥ 6 dB ({floor_raw:.1f} → {floor_cl:.1f} before leveling)"] = (
        floor_raw - floor_cl >= 6)
    lufs_clean = loudness(Path(clean["file"]))[0]
    checks[f"clean: speech leveled to −20 LUFS ({lufs_clean})"] = abs(lufs_clean + 20) < 1.0
    n = min(len(raw), len(cl))
    ea, eb = np.abs(raw[:n]), np.abs(cl[:n])
    c = np.fft.irfft(np.fft.rfft(ea - ea.mean(), 2 * n) * np.conj(np.fft.rfft(eb - eb.mean(), 2 * n)))
    lag = int(np.argmax(c))
    lag = lag if lag < n else lag - 2 * n
    checks[f"clean: same timing as the original (lag {lag / 16:.1f} ms)"] = abs(lag) <= 32
    checks["clean: same length"] = abs(len(raw) - len(cl)) <= 16
    rt = clean.get("roomtone")
    if rt:
        rt_pcm = pcm(tmp / rt if not Path(rt).is_absolute() else Path(rt))
        rt_db = 20 * np.log10(np.sqrt((rt_pcm ** 2).mean()) + 1e-12)
        speech_db = 20 * np.log10(np.sqrt((cl ** 2).mean()) + 1e-12)
        checks[f"room tone: the room's air, quiet but not dead ({rt_db:.1f} dB vs speech {speech_db:.1f})"] =             -90 < rt_db < speech_db - 15
    else:
        checks["room tone: sample saved"] = False

    (tmp / "02-work").mkdir(exist_ok=True)
    edl = {"version": 1, "output": {"width": 320, "height": 180, "fps": 30},
           "segments": [{"source": str(tmp / "00-raw" / "talk.mov"), "name": "talk.mov", "in": 0.0, "out": 30.0}]}
    (tmp / "02-work" / "edl.json").write_text(json.dumps(edl))
    run(tmp, "uv", "run", str(SCRIPTS / "render.py"), "--out", "social.mp4", "--no-grade")
    i, tp = loudness(tmp / "social.mp4")
    checks[f"master social: −14 LUFS, ≤ −1 dBTP ({i}, {tp})"] = abs(i + 14) <= 0.5 and tp <= -1.0
    run(tmp, "uv", "run", str(SCRIPTS / "render.py"), "--out", "podcast.mp4", "--no-grade", "--loudness", "podcast")
    i, tp = loudness(tmp / "podcast.mp4")
    checks[f"master podcast: −16 LUFS ({i})"] = abs(i + 16) <= 0.5

    # Music: a 60 Hz bed (below the voice after the high-pass) ducked under speech.
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "aevalsrc='0.3*sin(2*PI*60*t)':s=48000:d=40",
                    "music.wav"], cwd=tmp, check=True)
    run(tmp, "uv", "run", str(SCRIPTS / "audio.py"), "music", "--file", "music.wav", "--fade-in", "0.1")
    run(tmp, "uv", "run", str(SCRIPTS / "render.py"), "--out", "music.mp4", "--no-grade")
    x = pcm(tmp / "music.mp4", 48000)
    talking = tone_level(x, 48000, 60, 2.6, 4.4)       # "…mostrar el error que todos cometen…"
    pause = tone_level(x, 48000, 60, 5.4, 6.0)         # the 1.5 s pause after it (release done)
    checks[f"music ducked while talking ({talking - pause:+.1f} dB vs the pause)"] = pause - talking >= 8

    # Synthesized bed: exact length, no clipping, beats on the tempo grid.
    run(tmp, "uv", "run", str(SCRIPTS / "musicgen.py"), "--mood", "upbeat", "--duration", "12.34",
        "--bpm", "120", "--out", "gen.wav")
    g = pcm(tmp / "gen.wav", 48000)
    beats = json.loads((tmp / "gen.beats.json").read_text())["beats"]
    checks[f"musicgen: exact length ({len(g) / 48000:.3f} s)"] = abs(len(g) / 48000 - 12.34) < 0.001
    checks["musicgen: no clipping"] = float(np.abs(g).max()) < 0.99
    checks["musicgen: beats every 0.5 s at 120 bpm"] = bool(beats) and np.allclose(np.diff(beats), 0.5, atol=0.002)

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
