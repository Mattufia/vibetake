# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Sound and delivery extras: bleeped words, the beat of a track, voice effects, chapters, GIFs.

    uv run tests/test_audio_fx.py

The bleep and the voice run on the Spanish retakes fixture (with its transcript); the beat on a
synthetic drum track whose beats are known; chapters and the GIF on the rendered results.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
FIX = ROOT / "tests" / "fixtures"
FF = shutil.which("ffmpeg")
SR = 48000


def uv(cwd: Path, script: str, *a: str) -> str:
    return subprocess.run(["uv", "run", str(SCRIPTS / script), *a], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True).stdout


def render(cwd: Path, segs: list[dict], out: str, **extra) -> np.ndarray:
    (cwd / "02-work" / "edl.json").write_text(json.dumps(
        {"version": 1, "output": {"width": 320, "height": 180, "fps": 30},
         "segments": [{"source": str(cwd / "src.mp4"), "name": "retakes_es.wav", **s} for s in segs], **extra}))
    uv(cwd, "render.py", "--out", out, "--no-grade")
    raw = subprocess.run([FF, "-v", "error", "-i", str(cwd / out), "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def share(x: np.ndarray, lo: float, hi: float) -> float:
    """Share of the energy between lo and hi Hz."""
    p = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(len(x), 1 / SR)
    return float(p[(f >= lo) & (f < hi)].sum() / (p.sum() or 1))


def rms(x: np.ndarray) -> np.ndarray:
    """Loudness every 20 ms."""
    return np.sqrt((x[:len(x) // 960 * 960].reshape(-1, 960) ** 2).mean(1))


def main() -> None:
    checks = {}
    tmp = Path(tempfile.mkdtemp(prefix="vt-afx-"))
    (tmp / "01-ingest").mkdir()
    (tmp / "02-work").mkdir()
    shutil.copy(FIX / "retakes_es.transcript.json", tmp / "01-ingest" / "transcript.json")
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=20", "-i",
                    str(FIX / "retakes_es.wav"), "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "src.mp4"], cwd=tmp, check=True)

    # Bleep: "error" is said at 3.21–3.41 of the source → 1.03–1.23 in a cut starting at 2.18.
    line = [{"in": 2.18, "out": 4.77}]
    plain = render(tmp, line, "plain.mp4")
    beep = render(tmp, line, "beep.mp4", bleep={"words": ["error"]})
    mute = render(tmp, line, "mute.mp4", bleep={"words": ["err*"], "sound": "mute"})
    w = slice(int(1.06 * SR), int(1.2 * SR))
    rest = slice(int(1.5 * SR), int(2.2 * SR))
    checks[f"bleep: a 1 kHz tone over the word ({share(beep[w], 950, 1050):.2f} of it), the rest untouched"] = (
        share(beep[w], 950, 1050) > 0.8 and share(plain[w], 950, 1050) < 0.2
        and np.corrcoef(rms(beep[rest]), rms(plain[rest]))[0, 1] > 0.98)   # loudness is re-mastered: compare shapes
    checks["bleep mute (a prefix pattern): the word is silent, the speech around it is not"] = (
        np.abs(mute[w]).max() < 0.005 < np.abs(plain[w]).max() and np.abs(mute[rest]).max() > 0.05)
    uv(tmp, "subtitles.py", "--no-burn", "--srt", "beep.srt")
    srt = (tmp / "beep.srt").read_text(encoding="utf-8")
    checks["bleep: the subtitles star the word"] = "error" not in srt and "*" in srt and "mostrar" in srt

    # Voice: the phone cuts the low end; deep keeps the length.
    phone = render(tmp, [{**line[0], "voice": "phone"}], "phone.mp4")
    deep = render(tmp, [{**line[0], "voice": "deep"}], "deep.mp4")
    lo_p, lo_ph = share(plain[: 2 * SR], 0, 250), share(phone[: 2 * SR], 0, 250)
    checks[f"voice phone: energy under 250 Hz {lo_p:.2f} → {lo_ph:.2f}"] = lo_ph < lo_p / 4
    checks[f"voice deep: same length ({len(deep) / SR:.2f} s vs {len(plain) / SR:.2f} s), lower"] = (
        abs(len(deep) - len(plain)) < 0.05 * SR and share(deep[: 2 * SR], 0, 250) > lo_p)

    # Chapters: anchored to source moments, written into the MP4 and as text.
    segs = [{"in": 2.18, "out": 4.77}, {"in": 6.67, "out": 8.87}, {"in": 16.71, "out": 19.0}]
    chs = [{"title": "Intro", "anchor": {"name": "retakes_es.wav", "time": 2.5}},
           {"title": "Paso 1", "anchor": {"name": "retakes_es.wav", "time": 6.67}},
           {"title": "Cortes = ritmo", "anchor": {"name": "retakes_es.wav", "time": 16.71}},
           {"title": "Cortado", "anchor": {"name": "retakes_es.wav", "time": 12.0}}]
    render(tmp, segs, "ch.mp4", chapters=chs)
    got = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-of", "json", str(tmp / "ch.mp4")],
                                    capture_output=True, text=True, encoding="utf-8", check=True).stdout)["chapters"]
    starts = [float(c["start_time"]) for c in got]
    checks[f"chapters: in the MP4 at {[round(s, 2) for s in starts]} (cut-out one dropped)"] = (
        [c["tags"]["title"] for c in got] == ["Intro", "Paso 1", "Cortes = ritmo"]
        and abs(starts[1] - 2.59) < 0.05 and abs(starts[2] - 4.79) < 0.05)
    txt = (tmp / "ch.chapters.txt").read_text(encoding="utf-8")
    checks["chapters: the YouTube text starts at 0:00"] = txt.startswith("0:00 Intro\n0:02 Paso 1")

    # GIF: a stretch of the video, looping.
    log = uv(tmp, "deliver.py", "gif", "--video", "ch.mp4", "--to", "1.5", "--width", "160")
    gif = tmp / "ch_0s.gif"
    frames = int(json.loads(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-show_streams", "-of", "json",
                                            str(gif)], capture_output=True, text=True, check=True).stdout)
                 ["streams"][0]["nb_read_frames"])
    checks[f"gif: {frames} frames at 15 fps, loops forever"] = (
        20 <= frames <= 25 and b"NETSCAPE2.0\x03\x01\x00\x00" in gif.read_bytes() and "WARNING" not in log)

    # Beat: 100 BPM drums (accent on 1, first beat at 0.37 s) over noise.
    sr, beat, t0 = 44100, 0.6, 0.37
    x = np.random.default_rng(0).normal(0, 0.02, sr * 20)
    for i in range(int((20 - t0) / beat)):
        k, n = int((t0 + i * beat) * sr), int(0.12 * sr)
        amp, env = (0.9 if i % 4 == 0 else 0.45), np.exp(-np.arange(n) / (0.02 * sr))
        x[k:k + n] += amp * env * (np.sin(2 * np.pi * (60 if i % 4 == 0 else 180) * np.arange(n) / sr)
                                   + 0.3 * np.random.default_rng(i).normal(0, 1, n))
    with wave.open(str(tmp / "drums.wav"), "wb") as f:
        f.setnchannels(1), f.setsampwidth(2), f.setframerate(sr)
        f.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())
    uv(tmp, "audio.py", "beats", "--file", "drums.wav")
    r = json.loads((tmp / "drums.beats.json").read_text())
    truth = t0 + beat * np.arange(40)
    err = max(np.abs(truth - b).min() for b in r["beats"][:-1])    # ponytail: the last beat, at the file's end, can be ~50 ms off
    checks[f"beats: {r['bpm']} BPM (really 100), worst beat {1000 * err:.0f} ms off, bars on the accent"] = (
        abs(r["bpm"] - 100) < 1 and err < 0.025 and len(r["beats"]) > 25
        and abs((r["bars"][0] - t0) / (4 * beat) - round((r["bars"][0] - t0) / (4 * beat))) < 0.05)

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
