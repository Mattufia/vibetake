# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Editing tests: cutlist.py and render.py.

    uv run tests/test_edit.py

1. Sync: a clip with a white flash and a beep at the same instants, cut into
   segments and rendered — every flash must still meet its beep (within a frame).
2. No clicks: a continuous tone cut at arbitrary phases — no sample jumps.
3. cutlist on the retake fixture's transcript: retakes and fillers are left out
   and every cut lands in silence.
"""
from __future__ import annotations

import json
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
FPS = 30


def ff(cwd: Path, *a: str) -> str:
    return subprocess.run([FF, "-hide_banner", "-y", *a], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True).stderr


def script(cwd: Path, name: str, *a: str) -> str:
    return subprocess.run(["uv", "run", str(SCRIPTS / name), *a], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True).stdout


def pcm(path: Path) -> np.ndarray:
    raw = subprocess.run([FF, "-v", "error", "-i", str(path), "-ac", "1", "-ar", "48000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768


def write_edl(path: Path, src: Path, cuts: list[tuple[float, float]], w=320, h=180) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"version": 1, "output": {"width": w, "height": h, "fps": FPS},
                                "segments": [{"source": str(src), "name": src.name, "in": a, "out": b}
                                             for a, b in cuts]}))


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-edit-"))
    checks = {}

    # 1. Flash + beep at k + 0.5 s.
    ff(tmp, "-f", "lavfi", "-i", f"color=black:s=320x180:r={FPS}:d=12,"
       "drawbox=c=white:t=fill:enable='lt(mod(t-0.5+10,1),0.066)'",
       "-f", "lavfi", "-i", "aevalsrc='0.5*sin(2*PI*1000*t)*lt(mod(t-0.5+10,1),0.066)':s=48000:d=12",
       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "sync.mp4")
    cuts = [(0.3, 2.8), (4.2, 6.73), (8.11, 9.9)]
    write_edl(tmp / "02-work" / "edl.json", tmp / "sync.mp4", cuts)
    script(tmp, "render.py", "--out", "out.mp4", "--no-grade")
    log = ff(tmp, "-i", "out.mp4", "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-")
    ys = [float(v) for v in __import__("re").findall(r"YAVG=([\d.]+)", log)]
    flashes = [i / FPS for i in range(len(ys)) if ys[i] > 100 and (i == 0 or ys[i - 1] <= 100)]
    x = pcm(tmp / "out.mp4")
    env = np.abs(x[:len(x) // 48 * 48]).reshape(-1, 48).mean(1)   # 1 ms
    beeps = [i / 1000 for i in range(1, len(env)) if env[i] > 0.03 and env[i - 1] <= 0.03]
    pairs = [min(abs(f - b) for b in beeps) for f in flashes] if beeps else [9]
    checks[f"sync: {len(flashes)} flashes each within one frame of its beep"] = (
        len(flashes) == 8 and max(pairs) < 1 / FPS)   # 0.5–2.5 (3) + 4.5–6.5 (3) + 8.5–9.5 (2)
    d = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(tmp / "out.mp4")],
                                  capture_output=True, text=True, check=True).stdout)["streams"]
    vd, ad = (float(s["duration"]) for s in d)
    expected = sum(round((b - a) * FPS) / FPS for a, b in cuts)
    checks["duration: picture = sound = sum of frame-rounded segments"] = (
        abs(vd - expected) < 1.5 / FPS and abs(ad - expected) < 0.03)

    # 2. Continuous tone cut at arbitrary phases.
    ff(tmp, "-f", "lavfi", "-i", "color=gray:s=320x180:r=30:d=8", "-f", "lavfi", "-i",
       "sine=f=440:r=48000:d=8,volume=0.5", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
       "-c:a", "pcm_s16le", "tone.mov")
    write_edl(tmp / "02-work" / "edl.json", tmp / "tone.mov", [(0.0, 1.237), (2.519, 3.771), (5.003, 6.6)])
    script(tmp, "render.py", "--out", "tone.mp4", "--no-grade")
    y = pcm(tmp / "tone.mp4")
    natural = 2 * np.pi * 440 / 48000 * 0.5
    checks["no clicks at cuts (no sample jump above the tone's own slope)"] = float(
        np.abs(np.diff(y)).max()) < natural * 1.6

    # 3. cutlist on the retake transcript.
    ing = tmp / "proj" / "01-ingest"
    ing.mkdir(parents=True)
    shutil.copy(FIX / "retakes_es.transcript.json", ing / "transcript.json")
    (ing / "material.json").write_text(json.dumps({"files": [
        {"name": "retakes_es.wav", "file": str(FIX / "retakes_es.wav"), "kind": "audio"}]}))
    script(tmp / "proj", "cutlist.py", "--keep", "auto")
    edl = json.loads((tmp / "proj" / "02-work" / "edl.json").read_text(encoding="utf-8"))
    text = " ".join(s["text"] for s in edl["segments"]).lower()
    checks["retakes left out ('mos...', 'no, perdón')"] = "mos..." not in text and "perdón" not in text
    checks["finals kept"] = all(k in text for k in ("mostrar el error", "transcripción", "antes de exportar"))
    checks["fillers left out ('bueno', 'eh')"] = "bueno" not in text and " eh" not in f" {text}"
    src = pcm(FIX / "retakes_es.wav")
    level = lambda t: 20 * np.log10(np.sqrt((src[int(t * 48000) - 240:int(t * 48000) + 240] ** 2).mean()) + 1e-9)  # noqa: E731
    edges = [t for s in edl["segments"] for t in (s["in"], s["out"])]
    checks[f"every cut in silence (loudest of {len(edges)} cut points ≤ −40 dB)"] = max(map(level, edges)) <= -40

    # Pauses at joins sound like speech: ~0.4 s after a sentence (normal pace).
    tr = json.loads((FIX / "retakes_es.transcript.json").read_text(encoding="utf-8"))
    words = sorted((w for f in tr["files"].values() for s in f["segments"] for w in s["words"]),
                   key=lambda w: w["start"])
    # Measured on the audio (what's heard), not on Whisper's word edges, which can be off.
    env = 20 * np.log10(np.sqrt((src[: len(src) // 480 * 480].reshape(-1, 480) ** 2).mean(1)) + 1e-9)
    speech = env > env.max() - 30

    def sound_end(t: float) -> float:          # last speech frame before t
        i = int(t * 100)
        while i > 0 and not speech[i]:
            i -= 1
        return (i + 1) / 100

    def sound_start(t: float) -> float:        # first speech frame after t
        i = int(t * 100)
        while i < len(speech) - 1 and not speech[i]:
            i += 1
        return i / 100
    gaps = []
    for a, b in zip(edl["segments"], edl["segments"][1:]):
        last = max((w for w in words if w["end"] <= a["out"] + 0.3), key=lambda w: w["end"])
        if last["word"].strip().endswith((".", "?", "!")):
            gaps.append(round((a["out"] - sound_end(a["out"])) + (sound_start(b["in"]) - b["in"]), 2))
    checks[f"pause after each sentence ≈ 0.4 s ({gaps})"] = bool(gaps) and all(0.33 <= g <= 0.47 for g in gaps)

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print("flash→beep offsets (ms):", [round(p * 1000) for p in pairs])
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
