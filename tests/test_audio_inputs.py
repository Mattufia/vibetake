# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Audio input tests: picking and fixing the source of the sound (ingest.py + audio.py clean).

    uv run tests/test_audio_inputs.py

Synthetic "speech" (bursts of band-limited noise, like syllables, with pauses):

1. A mic on the left channel only → "one channel"; the cleaned voice is centered (mono).
2. One mic per person on left and right → "separate mics"; real stereo → left alone.
3. A camera file with two tracks, the second much cleaner → the second is picked.
4. One separate recording covering two clips → each clip found in it, at its offset.
5. The cleanup uses that recording for the clip, aligned to the picture.
6. A clip that's only ambience keeps its sound: no noise reduction, no gain.
7. The voice level is measured where someone speaks, so it lands at −20 LUFS.
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
sys.path.insert(0, str(SCRIPTS))
import ingest  # noqa: E402

FF = shutil.which("ffmpeg")
SR = 48000


def speech(dur: float, seed: int, level: float = 0.3) -> tuple[np.ndarray, list[tuple[float, float]]]:
    """Syllable-like bursts with pauses; returns the signal and where the 'words' are."""
    rng = np.random.default_rng(seed)
    x = np.zeros(int(dur * SR), np.float32)
    words, t = [], 0.3
    while t < dur - 0.5:
        n = rng.integers(2, 6)                      # a phrase of a few syllables
        start = t
        for _ in range(n):
            ln = rng.uniform(0.12, 0.3)
            a, b = int(t * SR), int(min(t + ln, dur) * SR)
            burst = np.convolve(rng.standard_normal(b - a), np.ones(6) / 6, mode="same")
            env = np.sin(np.linspace(0, np.pi, b - a)) ** 0.5
            x[a:b] += (burst * env * level * rng.uniform(0.6, 1.0)).astype(np.float32)
            t += ln + rng.uniform(0.04, 0.12)
        words.append((round(start, 3), round(t, 3)))
        t += rng.uniform(0.5, 1.4)                  # a pause between phrases
    return x, words


def noise(dur: float, seed: int, level: float) -> np.ndarray:
    return (np.random.default_rng(seed).standard_normal(int(dur * SR)) * level).astype(np.float32)


def write_wav(path: Path, chans: list[np.ndarray]) -> None:
    n = min(len(c) for c in chans)
    data = np.stack([c[:n] for c in chans], 1).astype(np.float32)
    subprocess.run([FF, "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", str(len(chans)), "-i", "-",
                    "-c:a", "pcm_s24le", str(path)], input=data.tobytes(), check=True)


def video(path: Path, dur: float, tracks: list[Path]) -> None:
    ins = sum((["-i", str(t)] for t in tracks), [])
    maps = sum((["-map", f"{i + 1}:a"] for i in range(len(tracks))), [])
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=gray:s=320x180:r=25:d={dur}", *ins,
                    "-map", "0:v", *maps, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "256k", "-shortest", str(path)], check=True)


def meta(path: Path, kind: str = "video") -> dict:
    info = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
                                      str(path)], capture_output=True, text=True, check=True).stdout)
    a = [s for s in info["streams"] if s["codec_type"] == "audio"]
    return {"name": path.name, "file": str(path), "kind": kind, "duration": float(info["format"]["duration"]),
            "audio_tracks": [{"index": i, "channels": s.get("channels")} for i, s in enumerate(a)]}


def pcm(path: Path, start: float = 0, dur: float | None = None, rate: int = 8000) -> np.ndarray:
    cmd = [FF, "-v", "error", "-ss", str(start), *(["-t", str(dur)] if dur else []), "-i", str(path),
           "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(cmd, capture_output=True, check=True).stdout, np.float32)


def lag_ms(a: np.ndarray, b: np.ndarray, rate: int = 8000) -> float:
    ea, eb = np.abs(a), np.abs(b)
    n = min(len(ea), len(eb))
    ea, eb = ea[:n] - ea[:n].mean(), eb[:n] - eb[:n].mean()
    c = np.fft.irfft(np.fft.rfft(ea, 2 * n) * np.conj(np.fft.rfft(eb, 2 * n)))
    k = int(np.argmax(c))
    return (k if k < n else k - 2 * n) / rate * 1000


def speech_lufs(path: Path, spans: list[tuple[float, float]]) -> float:
    sel = "+".join(f"between(t,{a},{b})" for a, b in spans)
    p = subprocess.run([FF, "-hide_banner", "-nostats", "-i", str(path), "-af",
                        f"aselect='{sel}',asetpts=N/SR/TB,ebur128", "-f", "null", "-"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    s = p.stderr[p.stderr.rfind("Summary:"):]
    return float(re.search(r"I:\s+(-?[\d.]+) LUFS", s).group(1))


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-audioin-"))
    raw = tmp / "00-raw"
    raw.mkdir()
    checks = {}
    D = 12.0

    # 1–2. Channel layouts.
    s1, w1 = speech(D, 1)
    write_wav(tmp / "one.wav", [s1 + noise(D, 2, 0.003), noise(D, 3, 0.00003)])
    video(raw / "one.mp4", D, [tmp / "one.wav"])
    s2, _ = speech(D, 4)
    write_wav(tmp / "two.wav", [s1 + noise(D, 5, 0.003), s2 + noise(D, 6, 0.003)])
    video(raw / "two.mp4", D, [tmp / "two.wav"])
    write_wav(tmp / "st.wav", [s1 + noise(D, 7, 0.003), 0.9 * s1 + noise(D, 8, 0.003)])
    video(raw / "st.mp4", D, [tmp / "st.wav"])
    lay = {n: ingest.channel_layout(raw / f"{n}.mp4", 0, 2) for n in ("one", "two", "st")}
    checks[f"mic on one channel → 'one channel' ({lay['one']['layout']})"] = (
        lay["one"]["layout"] == "one channel" and lay["one"]["live"] == [0])
    checks[f"one mic per person → 'separate mics' ({lay['two']['layout']})"] = lay["two"]["layout"] == "separate mics"
    checks[f"real stereo → left alone ({lay['st']['layout']})"] = lay["st"]["layout"] == "stereo"

    # 3. Two tracks: the camera mic (noisy) and a wireless mic (clean) on track 2.
    write_wav(tmp / "t0.wav", [0.4 * s1 + noise(D, 9, 0.08)])
    write_wav(tmp / "t1.wav", [s1 + noise(D, 10, 0.002)])
    video(raw / "tracks.mp4", D, [tmp / "t0.wav", tmp / "t1.wav"])
    best = ingest.quick_best_track(meta(raw / "tracks.mp4"))
    checks[f"two tracks → the clearer one is picked (track {best + 1})"] = best == 1

    # 4. One separate recording (40 s) covering two clips: clip A = 5–17 s, clip B = 22–34 s.
    long, lw = speech(40.0, 11)
    write_wav(raw / "recorder.wav", [long + noise(40.0, 12, 0.001)])
    cam = 0.35 * long + noise(40.0, 13, 0.02)            # the camera hears the same, worse
    for name, a in (("clipA", 5.0), ("clipB", 22.0)):
        write_wav(tmp / f"{name}.wav", [cam[int(a * SR):int((a + D) * SR)]])
        video(raw / f"{name}.mp4", D, [tmp / f"{name}.wav"])
    vids = [meta(raw / "clipA.mp4"), meta(raw / "clipB.mp4")]
    ext = [meta(raw / "recorder.wav", "audio")]
    report = ingest.match_external(vids, ext)
    offs = {v["name"]: (v.get("external_audio") or {}).get("offset_s") for v in vids}
    checks[f"one recording, two clips: both found at their offsets {offs}"] = (
        offs["clipA.mp4"] is not None and abs(offs["clipA.mp4"] + 5.0) < 0.01
        and offs["clipB.mp4"] is not None and abs(offs["clipB.mp4"] + 22.0) < 0.01
        and len(report[0]["videos"]) == 2)

    # 5–7. Cleanup over a small project.
    amb = noise(D, 14, 0.05)
    write_wav(tmp / "amb.wav", [amb, amb * 0.9])
    video(raw / "amb.mp4", D, [tmp / "amb.wav"])
    files = [meta(raw / f"{n}.mp4") for n in ("one", "clipA", "amb")] + ext
    for m in files:
        if m["kind"] == "video":
            m["audio"] = [{"index": 0, "noise_floor_db": -60.0,
                           "channels": ingest.channel_layout(Path(m["file"]), 0, m["audio_tracks"][0]["channels"])}]
    files[1]["external_audio"] = vids[0]["external_audio"]
    ing = tmp / "01-ingest"
    ing.mkdir()
    (ing / "material.json").write_text(json.dumps({"files": files}), encoding="utf-8")
    clip_words = [(a - 5.0, b - 5.0) for a, b in lw if a >= 5.0 and b <= 17.0]

    def tw(spans):
        return {"track": 0, "segments": [{"words": [{"word": " x", "start": a, "end": b} for a, b in spans]}]}
    (ing / "transcript.json").write_text(json.dumps({"files": {"one.mp4": tw(w1), "clipA.mp4": tw(clip_words),
                                                               "amb.mp4": tw([])}}), encoding="utf-8")
    p = subprocess.run(["uv", "run", str(SCRIPTS / "audio.py"), "clean"], cwd=tmp, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode:
        print(p.stdout[-2000:], p.stderr[-2000:])
    idx = json.loads((ing / "audio" / "clean.json").read_text(encoding="utf-8"))
    one = tmp / idx["one.mp4"]["file"]
    ch = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(one)],
                                   capture_output=True, text=True).stdout)["streams"][0]["channels"]
    checks[f"one channel: the cleaned voice is centered ({ch} channel)"] = ch == 1
    lvl = speech_lufs(one, w1)
    checks[f"voice level measured where someone speaks → {lvl:.1f} LUFS (target −20 ±1.5)"] = abs(lvl + 20) < 1.5
    clip = tmp / idx["clipA.mp4"]["file"]
    lag = lag_ms(pcm(clip, 0, 10), pcm(raw / "recorder.wav", 5.0, 10))
    checks[f"clip A uses the recorder's sound, aligned ({idx['clipA.mp4'].get('sound')}, lag {lag:.1f} ms)"] = (
        "recorder" in idx["clipA.mp4"].get("sound", "") and abs(lag) <= 10)
    checks["the recorder isn't cleaned on its own (it's used through its clips)"] = "recorder.wav" not in idx
    checks[f"ambience kept as recorded (gain {idx['amb.mp4']['gain_db']}, chain {idx['amb.mp4']['chain']})"] = (
        idx["amb.mp4"]["ambient"] and idx["amb.mp4"]["gain_db"] == 0 and "afftdn" not in idx["amb.mp4"]["chain"])

    checks = {k: bool(v) for k, v in checks.items()}
    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"{sum(checks.values())}/{len(checks)} pass · {tmp}")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
