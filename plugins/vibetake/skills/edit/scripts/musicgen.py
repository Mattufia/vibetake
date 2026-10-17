# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Background music synthesized from code: no samples, no licenses, exact length.

    uv run musicgen.py --mood calm|upbeat|lofi|cinematic --duration 45 [--bpm 96] [--key A] [--seed 3]
                       [--out 02-work/music/<mood>.wav]

Chords, bass, an arpeggio and drums are computed note by note (detuned saw pads,
plucked keys, a sine bass, synthesized kick/snare/hat, a small reverb), so the
result is free to use anywhere and fits the edit to the frame. It also writes the
beat times (<out>.beats.json) for cutting on the beat. Simple beds, not produced
songs: listen before using it, and prefer a real track when the video needs one.
Then: uv run audio.py music --file <out>
"""
from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import _common  # noqa: E402,F401  (UTF-8 console output on Windows)

SR = 48000
NOTES = {n: i for i, n in enumerate(["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"])}
MOODS = {
    #            bpm  progression (scale degrees, minor=lowercase)        drums   swing  brightness
    "calm":      (78, ["I", "V", "vi", "IV"],                             "soft", 0.0, 1800),
    "upbeat":    (112, ["I", "V", "vi", "IV"],                            "full", 0.0, 4200),
    "lofi":      (82, ["ii7", "V7", "Imaj7", "vi7"],                      "lofi", 0.18, 1500),
    "cinematic": (70, ["i", "VI", "III", "VII"],                          "none", 0.0, 2600),
}
DEGREES = {"I": (0, "maj"), "ii": (2, "min"), "iii": (4, "min"), "IV": (5, "maj"), "V": (7, "maj"),
           "vi": (9, "min"), "i": (0, "min"), "III": (3, "maj"), "VI": (8, "maj"), "VII": (10, "maj")}
CHORDS = {"maj": [0, 4, 7], "min": [0, 3, 7], "maj7": [0, 4, 7, 11], "min7": [0, 3, 7, 10], "7": [0, 4, 7, 10]}


def hz(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def chord(sym: str, root: int) -> list[int]:
    base = sym.rstrip("7").replace("maj", "")
    deg, quality = DEGREES[base]
    if sym.endswith("maj7"):
        quality = "maj7"
    elif sym.endswith("7"):
        quality = "min7" if quality == "min" else "7"
    return [root + deg + x for x in CHORDS[quality]]


def env(n: int, a: float, r: float) -> np.ndarray:
    t = np.arange(n) / SR
    e = np.minimum(1, t / max(a, 1e-3))
    tail = np.clip((n / SR - t) / max(r, 1e-3), 0, 1)
    return e * tail


def lowpass_fast(x: np.ndarray, cutoff: float) -> np.ndarray:
    """One-pole low-pass, applied via FFT of its impulse response (fast for long signals)."""
    a = np.exp(-2 * np.pi * cutoff / SR)
    h = (1 - a) * a ** np.arange(int(SR * 0.05))
    n = len(x) + len(h)
    return np.fft.irfft(np.fft.rfft(x, n) * np.fft.rfft(h, n), n)[:len(x)]


def saw(f: float, n: int, rng) -> np.ndarray:
    t = np.arange(n) / SR
    phase = rng.random()
    return 2 * ((f * t + phase) % 1) - 1


def pad(notes: list[int], n: int, rng, bright: float) -> np.ndarray:
    out = np.zeros(n)
    for m in notes:
        for d in (-0.08, 0.0, 0.07):                     # three detuned voices per note
            out += saw(hz(m + d), n, rng)
    return lowpass_fast(out / (3 * len(notes)), bright) * env(n, 0.6, 0.8)


def pluck(m: int, n: int) -> np.ndarray:
    t = np.arange(n) / SR
    f = hz(m)
    tone = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t) + 0.12 * np.sin(6 * np.pi * f * t)
    return tone * np.exp(-t * 5.5) * env(n, 0.004, 0.05)


def bass(m: int, n: int) -> np.ndarray:
    t = np.arange(n) / SR
    return (np.sin(2 * np.pi * hz(m) * t) + 0.2 * np.sin(4 * np.pi * hz(m) * t)) * env(n, 0.01, 0.12)


def kick(rng) -> np.ndarray:
    n = int(SR * 0.35)
    t = np.arange(n) / SR
    f = 45 + 90 * np.exp(-t * 30)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)


def snare(rng) -> np.ndarray:
    n = int(SR * 0.22)
    t = np.arange(n) / SR
    noise = rng.standard_normal(n)
    noise -= lowpass_fast(noise, 1500)
    return (0.6 * noise + 0.5 * np.sin(2 * np.pi * 190 * t)) * np.exp(-t * 18)


def hat(rng) -> np.ndarray:
    n = int(SR * 0.05)
    noise = rng.standard_normal(n)
    return (noise - lowpass_fast(noise, 7000)) * np.exp(-np.arange(n) / SR * 60)


def reverb(x: np.ndarray, rng, seconds: float = 1.6, wet: float = 0.22) -> np.ndarray:
    n = int(SR * seconds)
    ir = rng.standard_normal(n) * np.exp(-np.arange(n) / SR * (6.9 / seconds))
    ir = lowpass_fast(ir, 5000)
    ir /= np.sqrt((ir ** 2).sum())
    m = len(x) + n
    tail = np.fft.irfft(np.fft.rfft(x, m) * np.fft.rfft(ir, m), m)[:len(x)]
    return x + wet * tail


def place(buf: np.ndarray, sig: np.ndarray, at: int, gain: float) -> None:
    if at >= len(buf):
        return
    end = min(len(buf), at + len(sig))
    buf[at:end] += gain * sig[:end - at]


def generate(mood: str, duration: float, bpm: float | None, key: str, seed: int) -> tuple[np.ndarray, list[float]]:
    rng = np.random.default_rng(seed)
    base_bpm, prog, drums, swing, bright = MOODS[mood]
    bpm = bpm or base_bpm
    beat = 60 / bpm
    bar = 4 * beat
    root = 57 + NOTES[key]                                # A3-ish register for chords
    total = int(SR * duration)
    music = np.zeros(total + SR * 3)
    drum = np.zeros_like(music)
    beats = []
    bars = int(np.ceil(duration / bar))
    for b in range(bars):
        notes = chord(prog[b % len(prog)], root)
        start = int(b * bar * SR)
        n = int(bar * SR)
        place(music, pad(notes, n + int(0.8 * SR), rng, bright), start, 0.5)
        place(music, bass(notes[0] - 24, int(beat * 2 * SR)), start, 0.55)
        place(music, bass(notes[0] - 24, int(beat * 2 * SR)), start + int(2 * beat * SR), 0.5)
        arp = [notes[i % len(notes)] + 12 for i in (0, 1, 2, 1, 0, 2, 1, 2)]
        for i, m in enumerate(arp):
            off = (i * beat / 2 + (swing * beat / 2 if i % 2 else 0)) * SR
            place(music, pluck(m, int(beat * SR)), start + int(off), 0.22 if mood != "cinematic" else 0.1)
        for q in range(4):
            t = start + int(q * beat * SR)
            beats.append(round(t / SR, 3))
            if drums == "none" or t >= total:
                continue
            if drums in ("full", "lofi") or (drums == "soft" and q in (0, 2)):
                place(drum, kick(rng), t, 0.9 if q in (0, 2) else (0.0 if drums == "lofi" else 0.6))
            if q in (1, 3) and drums != "soft":
                place(drum, snare(rng), t + int((swing * beat / 2) * SR), 0.45 if drums == "full" else 0.3)
            if drums in ("full", "lofi"):
                for h in (0, 1):
                    place(drum, hat(rng), t + int((h * beat / 2 + (swing * beat / 2 if h else 0)) * SR),
                          0.18 if drums == "full" else 0.12)
    mix = reverb(music, rng) + 0.8 * drum
    if mood == "lofi":
        mix = lowpass_fast(mix, 3200) + 0.004 * rng.standard_normal(len(mix)) * (rng.random(len(mix)) > 0.9995)
    mix = mix[:total]
    fade = int(SR * min(2.0, duration / 4))
    mix[-fade:] *= np.linspace(1, 0, fade) ** 1.5
    mix /= np.max(np.abs(mix)) + 1e-9
    # Stereo: a little width from a 12 ms delayed copy on one side.
    d = int(SR * 0.012)
    left, right = mix, np.r_[np.zeros(d), mix[:-d]] * 0.9 + mix * 0.1
    return np.stack([left, right], 1) * 0.89, [b for b in beats if b < duration]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mood", choices=tuple(MOODS), default="calm")
    ap.add_argument("--duration", type=float, required=True, help="seconds (match the edit)")
    ap.add_argument("--bpm", type=float)
    ap.add_argument("--key", default="A", choices=tuple(NOTES))
    ap.add_argument("--seed", type=int, default=1, help="another seed, another variation")
    ap.add_argument("--out")
    args = ap.parse_args()
    out = Path(args.out or f"02-work/music/{args.mood}.wav")
    out.parent.mkdir(parents=True, exist_ok=True)
    audio, beats = generate(args.mood, args.duration, args.bpm, args.key, args.seed)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2")
    with wave.open(str(out), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    out.with_suffix(".beats.json").write_text(json.dumps({"bpm": args.bpm or MOODS[args.mood][0],
                                                          "beats": beats}), encoding="utf-8")
    print(f"{out} ({args.duration:.1f} s, {args.mood}, {args.bpm or MOODS[args.mood][0]} bpm, key {args.key}, "
          f"seed {args.seed}) · {len(beats)} beats → {out.with_suffix('.beats.json')}")


if __name__ == "__main__":
    sys.exit(main())
