# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Sound effects: designed from code (no samples, no licenses) or the user's, anchored to the edit.

    uv run sfx.py list                                     # every sound and what shapes it
    uv run sfx.py make                                     # the basic set → 02-work/sfx/
    uv run sfx.py design --kind whoosh --name slow --pitch -5 --dur 1.4 --reverb 0.3 [--variants 3]
    uv run sfx.py design --name logo --layers "riser,impact@0:+2,sparkle@0.05:-6"   # sounds stacked on their event
    uv run sfx.py place --kind whoosh --cut 3              # on the cut before segment 3
    uv run sfx.py place --kind pop --line L5 --word ventas # when that word is said
    uv run sfx.py place --kind hit --pitch -3 --reverb .4 --line L5   # shaped right here
    uv run sfx.py place --file mine.wav --line L7          # the user's own sound
    uv run sfx.py check | clear

Shape any sound: --pitch (semitones) --dur (seconds) --bright (0.5 dark, 2 bright) --drive --reverb --echo
--reverse --pan (−1…1) --seed (another take). A designed sound keeps its name and can be placed by it.

Every sound is placed by its *event point*: the start for pop, click, hit and ding;
the peak for whoosh (its middle) and riser (its end) — so the sound lands on the
frame of the cut, the word or the graphic. Anchors are moments of the source (like
b-roll), so re-cutting doesn't break them. Each sound is leveled at placement to
~10 dB under the cleaned voice (--weight minor: 8 dB lower still, for the secondary sounds;
--gain fine-tunes); render.py mixes them over the music.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import _synth as sy  # noqa: E402
from _common import die, load_json as load, tc, tool  # noqa: E402
from _timeline import anchor_from, to_timeline, total  # noqa: E402

SR = 48000
TARGET_RMS_DB = -30.0      # cleaned voice sits at about -20: SFX ~10 dB under it
MINOR_DB = -8.0            # --weight minor: secondary sounds this far under the main ones
KINDS = ("whoosh", "pop", "click", "hit", "riser", "ding")     # the basic set `make` writes


def _env(n: int, attack: float, release: float) -> np.ndarray:
    a, r = max(1, int(attack * SR)), max(1, int(release * SR))
    e = np.ones(n)
    e[:a] = np.linspace(0, 1, a) ** 2
    e[-r:] *= np.linspace(1, 0, r) ** 2
    return e


def _bandpass_sweep(noise: np.ndarray, f0: float, f1: float) -> np.ndarray:
    """Noise through a resonant band-pass whose center sweeps f0 → f1 (state-variable filter)."""
    out = np.empty_like(noise)
    low = band = 0.0
    fc = np.geomspace(f0, f1, len(noise))
    q = 0.7
    for i, x in enumerate(noise):
        f = 2 * np.sin(np.pi * fc[i] / SR)
        low += f * band
        high = x - low - q * band
        band += f * high
        out[i] = band
    return out


def _tone(freq, n: int) -> np.ndarray:
    return np.sin(2 * np.pi * np.cumsum(np.broadcast_to(np.asarray(freq, float), (n,))) / SR)


def _bells(notes, gap: float, decay: float = 5.0, tail: float = 0.7) -> np.ndarray:
    """A short phrase of bell tones: (midi, velocity) every `gap` seconds."""
    n = int((len(notes) * gap + tail) * SR)
    y = np.zeros(n)
    for i, (m, v) in enumerate(notes):
        k = int(SR * tail)
        t = sy.times(k)
        f = sy.hz(m)
        s = (np.sin(2 * np.pi * f * t) + 0.3 * np.sin(4 * np.pi * f * t) + 0.12 * np.sin(2 * np.pi * f * 5.4 * t)) \
            * np.exp(-t * decay) * _env(k, 0.002, 0.03)
        at = int(i * gap * SR)
        y[at:at + k] += v * s[:n - at]
    return y


def _grains(n: int, rate: float, length: float, rng, low: float, high: float) -> np.ndarray:
    """Many tiny filtered ticks at random: rain, fire, applause, typing all start here."""
    y = np.zeros(n)
    for at in rng.integers(0, max(1, n - 1), int(rate * n / SR)):
        k = int(SR * length * rng.uniform(0.5, 1.6))
        end = min(n, at + k)
        t = sy.times(end - at)
        f = rng.uniform(low, high)
        y[at:end] += rng.uniform(0.2, 1) * np.sin(2 * np.pi * f * t) * np.exp(-t / (length / 4))
    return y


def _extra(kind: str, rng, d: float | None) -> tuple[np.ndarray, float]:
    """(mono or stereo, event seconds). d: the length asked for, on the sounds that have one."""
    def size(default: float) -> int:
        return int((d or default) * SR)
    noise = rng.standard_normal
    if kind in ("swoosh", "whip", "swipe"):
        default, f0, f1, q = {"swoosh": (1.0, 180, 2600, 1.0), "whip": (0.28, 900, 7000, 1.2),
                              "swipe": (0.2, 2500, 8000, 1.4)}[kind]
        n = size(default)
        u = np.linspace(-1, 1, n)
        body = sy.sweep(noise(n), f0, f1, "bp", q) * np.exp(-(u / 0.42) ** 2)
        p = 0.5 + 0.45 * u
        return np.stack([body * (1 - p), body * p], 1), n / SR / 2
    if kind == "tick":
        n = size(0.012)
        return sy.highpass(noise(n), 3000) * np.exp(-sy.times(n) * 700), 0.0
    if kind == "tap":
        n = size(0.08)
        t = sy.times(n)
        return np.sin(2 * np.pi * 420 * t) * np.exp(-t * 90) + 0.25 * noise(n) * np.exp(-t * 500), 0.0
    if kind == "thud":
        n = size(0.35)
        t = sy.times(n)
        return _tone(60 + 70 * np.exp(-t * 40), n) * np.exp(-t * 15) + 0.1 * sy.lowpass(noise(n), 500) * np.exp(-t * 40), 0.0
    if kind == "boom":
        n = size(2.2)
        t = sy.times(n)
        body = _tone(34 + 60 * np.exp(-t * 16), n) * np.exp(-t * 2.2)
        rumble = sy.lowpass(noise(n), 240) * np.exp(-t * 2.6) * 0.8
        return np.tanh(2.2 * (body + rumble)), 0.0
    if kind == "impact":
        n = size(1.8)
        t = sy.times(n)
        sub = _tone(42 + 110 * np.exp(-t * 26), n) * np.exp(-t * 3.4)
        crack = sy.highpass(noise(n), 1400) * np.exp(-t * 34) * 0.55
        tail = sy.bandpass(noise(n), 700, 0.6) * np.exp(-t * 5) * 0.32
        return np.tanh(1.9 * (sub + crack + tail)), 0.0
    if kind == "sub_drop":
        n = size(1.6)
        t = sy.times(n)
        return np.tanh(1.5 * _tone(95 * 2 ** (-2.2 * t / t[-1]), n)) * _env(n, 0.004, 0.3), 0.0
    if kind == "downer":
        n = size(1.3)
        k = np.linspace(0, 1, n)
        tone = _tone(1300 * 2 ** (-4.5 * k), n) * 0.5
        air = sy.sweep(noise(n), 6000, 220, "bp", 0.9)
        return (tone + sy.norm(air)) * (1 - k) ** 1.5 * _env(n, 0.004, 0.05), 0.0
    if kind == "reverse_cymbal":
        n = size(1.6)
        t = sy.times(n)
        y = sy.highpass(noise(n), 3400) * np.exp(-t * 3.2)
        return y[::-1] * _env(n, 0.05, 0.004), (n - 1) / SR
    if kind == "tape_stop":
        n = size(0.9)
        k = np.linspace(0, 1, n)
        y = sum(a * _tone(f * (1 - k) ** 1.6 + 8, n) for f, a in ((220, 1), (330, 0.6), (440, 0.4)))
        return np.tanh(y) * (1 - k ** 3), 0.0
    if kind == "glitch":
        n = size(0.35)
        y = np.zeros(n)
        at = 0
        while at < n:
            k = int(SR * rng.uniform(0.008, 0.05))
            end = min(n, at + k)
            pick = rng.integers(3)
            piece = _tone(rng.uniform(180, 5200), end - at) if pick == 0 else (
                np.sign(_tone(rng.uniform(70, 900), end - at)) if pick == 1 else noise(end - at))
            y[at:end] = piece * rng.uniform(0.25, 1) * (rng.random() > 0.18)
            at = end
        return sy.crush(y, 7, 12000), 0.0
    if kind == "stutter":
        n = size(0.5)
        k = int(0.045 * SR)
        cell = (_tone(620, k) + 0.5 * noise(k)) * _env(k, 0.001, 0.012)
        return np.tile(cell, n // k + 1)[:n] * np.linspace(1, 0.25, n), 0.0
    if kind == "static":
        n = size(0.6)
        gate = np.repeat(rng.random(n // 240 + 1) > 0.3, 240)[:n]
        return sy.highpass(noise(n), 1800) * gate * _env(n, 0.005, 0.04), 0.0
    if kind == "scratch":
        n = size(0.42)
        t = sy.times(n)
        speed = np.abs(np.sin(2 * np.pi * 5 * t)) ** 1.2
        return sy.bandpass(noise(n), 1400, 0.7) * speed + 0.5 * _tone(180 + 800 * speed, n) * speed, 0.0
    if kind == "blip":
        n = size(0.07)
        return _tone(1200, n) * _env(n, 0.002, 0.03), 0.0
    if kind == "notify":
        return _bells([(84, 1), (91, 0.8)], 0.11, 6, 0.6), 0.0
    if kind == "success":
        return _bells([(76, .7), (79, .8), (84, 1)], 0.09, 4.2, 0.9), 0.18
    if kind == "error":
        n = size(0.42)
        k = n // 2
        cell = (np.sign(_tone(160, k)) * 0.5 + _tone(233, k) * 0.5) * _env(k, 0.003, 0.03)
        return np.r_[cell, cell, np.zeros(n - 2 * k)], 0.0
    if kind == "coin":
        return _bells([(88, .7), (93, 1)], 0.07, 7, 0.55), 0.07
    if kind == "sparkle":
        notes = [(int(rng.choice([84, 86, 88, 91, 93, 96, 98, 100])), float(rng.uniform(.3, 1))) for _ in range(9)]
        return _bells(notes, 0.055, 7, 0.6), 0.0
    if kind == "magic":
        notes = [(72 + s, 0.45 + i * 0.05) for i, s in enumerate((0, 4, 7, 12, 16, 19, 24, 28))]
        y = _bells(notes, 0.07, 4, 0.9)
        air = sy.norm(sy.sweep(noise(len(y)), 1200, 9000, "bp", 1)) * np.linspace(0, 0.25, len(y))
        return y + air * _env(len(y), 0.02, 0.4), 0.49
    if kind == "laser":
        n = size(0.3)
        k = np.linspace(0, 1, n)
        return np.sign(_tone(3200 * 2 ** (-4 * k), n)) * (1 - k) ** 2, 0.0
    if kind == "shutter":
        n = size(0.16)
        t = sy.times(n)
        click = lambda at: np.exp(-np.maximum(t - at, 0) * 420) * (t >= at)  # noqa: E731
        return sy.bandpass(noise(n), 2600, 0.7) * (click(0) + 0.8 * click(0.075)), 0.0
    if kind == "typing":
        n = size(1.2)
        y = np.zeros(n)
        at = 0
        while at < n:
            k = int(0.03 * SR)
            end = min(n, at + k)
            t = sy.times(end - at)
            y[at:end] += rng.uniform(.5, 1) * (sy.bandpass(noise(end - at), rng.uniform(1600, 3400), .8)
                                               + 0.6 * np.sin(2 * np.pi * 310 * t)) * np.exp(-t * 190)
            at += int(SR * rng.uniform(0.06, 0.19))
        return y, 0.0
    if kind == "bubble":
        n = size(0.16)
        t = sy.times(n)
        return _tone(380 * 2 ** (t * 13), n) * np.exp(-t * 30) * _env(n, 0.002, 0.02), 0.0
    if kind == "droplet":
        n = size(0.25)
        t = sy.times(n)
        return _tone(800 + 1500 * np.minimum(t * 25, 1), n) * np.exp(-t * 24) * _env(n, 0.001, 0.02), 0.0
    if kind == "heartbeat":
        n = size(0.9)
        t = sy.times(n)
        beat = lambda at, a: a * np.sin(2 * np.pi * 52 * np.maximum(t - at, 0)) * np.exp(-np.maximum(t - at, 0) * 17) * (t >= at)  # noqa: E731
        return beat(0, 1) + beat(0.24, 0.7), 0.0
    if kind == "bell":
        n = size(2.4)
        return sy.note("bell", 76, n / SR * 0.5, 0.9, int(rng.integers(999)))[:n], 0.0
    if kind == "gong":
        n = size(4.0)
        t = sy.times(n)
        y = sum(a * np.sin(2 * np.pi * f * t + rng.random() * 6) * np.exp(-t * dcy)
                for f, a, dcy in ((92, 1, 0.9), (147, .7, 1.1), (211, .6, 1.4), (318, .45, 1.8), (477, .35, 2.4), (733, .2, 3.2)))
        return y * _env(n, 0.012, 0.3) + 0.3 * sy.bandpass(noise(n), 1500, .6) * np.exp(-t * 7), 0.0
    if kind == "drumroll":
        n = size(1.6)
        k = int(SR * 0.05)
        y, at = np.zeros(n), 0
        while at < n:
            end = min(n, at + k)
            t = sy.times(end - at)
            y[at:end] += (at / n * 0.75 + 0.25) * sy.highpass(noise(end - at), 1400) * np.exp(-t * 80)
            at += k
        return y, (n - 1) / SR
    # Ambiences: textures that take any length with --dur.
    if kind == "wind":
        n = size(6.0)
        slow = sy.lowpass(noise(n), 0.5, 1)
        gust = 0.55 + 0.45 * (slow - slow.min()) / (np.ptp(slow) + 1e-9)
        return sy.bandpass(noise(n), 520, 0.5) * gust * _env(n, 0.5, 0.8), 0.0
    if kind == "rain":
        n = size(6.0)
        return (sy.highpass(noise(n), 2200) * 0.22 + 0.9 * _grains(n, 180, 0.012, rng, 1800, 7000)) * _env(n, 0.4, 0.6), 0.0
    if kind == "ocean":
        n = size(8.0)
        t = sy.times(n)
        swell = (0.55 + 0.45 * np.sin(2 * np.pi * t / 5.6 + rng.random() * 6)) ** 2
        return sy.lowpass(noise(n), 1600) * swell * _env(n, 0.6, 0.9), 0.0
    if kind == "fire":
        n = size(6.0)
        return (0.5 * sy.lowpass(noise(n), 320) + 1.1 * _grains(n, 16, 0.008, rng, 900, 4200)) * _env(n, 0.3, 0.5), 0.0
    if kind == "applause":
        n = size(3.5)
        return _grains(n, 900, 0.008, rng, 700, 3600) * _env(n, 0.25, 0.9), 0.0
    if kind == "room":
        n = size(5.0)
        return sy.lowpass(noise(n), 380) * 0.5 + 0.08 * _tone(60, n), 0.0
    die(f"unknown kind {kind} (see: sfx.py list)")


EXTRA = ("swoosh", "whip", "swipe", "tick", "tap", "thud", "boom", "impact", "sub_drop", "downer", "reverse_cymbal",
         "tape_stop", "glitch", "stutter", "static", "scratch", "blip", "notify", "success", "error", "coin", "sparkle",
         "magic", "laser", "shutter", "typing", "bubble", "droplet", "heartbeat", "bell", "gong", "drumroll",
         "wind", "rain", "ocean", "fire", "applause", "room")
ALL_KINDS = KINDS + EXTRA
GROUPS = {"movement": "whoosh swoosh whip swipe", "ui": "pop click tick tap blip notify success error coin ding",
          "impact": "hit thud impact boom sub_drop gong", "tension": "riser reverse_cymbal drumroll heartbeat downer",
          "digital": "glitch stutter static laser tape_stop scratch", "magic": "sparkle magic bell bubble droplet",
          "foley": "shutter typing", "ambience (any length with --dur)": "wind rain ocean fire applause room"}


def synth(kind: str, seed: int = 7, dur: float | None = None) -> tuple[np.ndarray, float]:
    """(stereo samples, event point in seconds)."""
    rng = np.random.default_rng(seed)
    if kind == "whoosh":
        n = int((dur or 0.7) * SR)
        t = np.linspace(-1, 1, n)
        body = _bandpass_sweep(rng.normal(0, 1, n), 300, 4000) * np.exp(-(t / 0.45) ** 2)
        pan = 0.5 + 0.4 * t                                  # passes left to right
        y, ev = np.stack([body * (1 - pan), body * pan], 1), n / SR / 2
    elif kind == "pop":
        n = int((dur or 0.09) * SR)
        t = np.arange(n) / SR
        f = 900 * np.exp(-t * 35) + 250
        m = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 55)
        y, ev = np.stack([m, m], 1), 0.0
    elif kind == "click":
        n = int((dur or 0.025) * SR)
        t = np.arange(n) / SR
        m = (np.sin(2 * np.pi * 2800 * t) + 0.5 * rng.normal(0, 1, n)) * np.exp(-t * 300)
        y, ev = np.stack([m, m], 1), 0.0
    elif kind == "hit":
        n = int((dur or 0.6) * SR)
        t = np.arange(n) / SR
        f = 55 + 90 * np.exp(-t * 30)
        thump = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7)
        snap = rng.normal(0, 1, n) * np.exp(-t * 60) * 0.35
        m = np.tanh(1.6 * (thump + snap))
        y, ev = np.stack([m, m], 1), 0.0
    elif kind == "riser":
        n = int((dur or 1.8) * SR)
        t = np.arange(n) / SR
        k = t / t[-1]
        tone = np.sin(2 * np.pi * np.cumsum(200 * 2 ** (2.5 * k)) / SR) * 0.35
        air = _bandpass_sweep(rng.normal(0, 1, n), 400, 7000) * 0.8
        m = (tone + air) * k ** 2 * _env(n, 0.01, 0.03)
        y, ev = np.stack([m, m * 0.95], 1), t[-1]
    elif kind == "ding":
        n = int((dur or 1.4) * SR)
        t = np.arange(n) / SR
        m = sum(a * np.sin(2 * np.pi * f * t) * np.exp(-t * d)
                for f, a, d in ((1318.5, 1.0, 3.2), (2637, 0.35, 5), (3955, 0.15, 8), (1976, 0.25, 4)))
        m *= _env(n, 0.002, 0.05)
        y, ev = np.stack([m, m], 1), 0.0
    elif kind in EXTRA:
        y, ev = _extra(kind, rng, dur)
        y = sy.stereo(y)
    else:
        die(f"unknown kind {kind} (see: sfx.py list)")
    y = y / (np.abs(y).max() + 1e-9) * 0.9
    return y.astype(np.float32), float(ev)


def shape(y: np.ndarray, ev: float, pitch: float = 0.0, bright: float = 1.0, drive: float = 0.0, reverb: float = 0.0,
          echo: float = 0.0, reverse: bool = False, pan: float = 0.0, seed: int = 7) -> tuple[np.ndarray, float]:
    """The same sound changed: its event point follows it."""
    y = np.asarray(y, float)
    if pitch:
        ratio = 2 ** (pitch / 12)                               # played faster or slower, like tape
        n = max(16, int(len(y) / ratio))
        pos = np.arange(n) * ratio
        y = np.stack([np.interp(pos, np.arange(len(y)), y[:, c]) for c in (0, 1)], 1)
        ev /= ratio
    if reverse:
        y, ev = y[::-1].copy(), max(0.0, len(y) / SR - ev)
    if bright != 1.0:
        y = sy.shelf(y, 2400, 12 * np.log2(max(bright, 0.05)))
    if drive:
        y = sy.saturate(sy.norm(y), drive)
    if reverb or echo:
        tail = int(SR * (0.7 + 2.4 * reverb + (0.9 if echo else 0)))
        y = np.r_[y, np.zeros((tail, 2))]
        wet = np.zeros_like(y)
        if echo:
            wet += sy.echo(y, 0.19, 0.45) * echo
        if reverb:
            wet += sy.reverb(y, 0.5 + 2.4 * reverb, seed) * (0.6 + reverb)
        y = y + wet
        y[-int(SR * 0.05):] *= np.linspace(1, 0, int(SR * 0.05))[:, None]
    if pan:
        a = (np.clip(pan, -1, 1) + 1) * np.pi / 4
        y = y * np.array([[np.cos(a) * np.sqrt(2), np.sin(a) * np.sqrt(2)]])
    return (sy.norm(y, 0.9).astype(np.float32), float(ev))


def layered(spec: str, seed: int, dur: float | None) -> tuple[np.ndarray, float]:
    """'riser,impact@0:+2,sparkle@0.05:-6': sounds aligned on their event points (+ @offset s, :dB)."""
    items = []
    for i, raw in enumerate(x.strip() for x in spec.split(",") if x.strip()):
        m = re.fullmatch(r"([a-z_]+)(?:@(-?[\d.]+))?(?::([+-]?[\d.]+))?", raw)
        if not m or m.group(1) not in ALL_KINDS:
            die(f"--layers: can't read '{raw}' — use kind[@seconds][:dB], e.g. impact@0.05:-3 (sfx.py list)")
        y, ev = synth(m.group(1), seed + i, dur if m.group(1) in ("riser", "wind", "rain", "ocean", "fire", "room") else None)
        items.append((y, ev - float(m.group(2) or 0), 10 ** (float(m.group(3) or 0) / 20)))
    if not items:
        die("--layers: nothing to stack")
    event = max(e for _, e, _ in items)
    length = max(int((event - e) * SR) + len(y) for y, e, _ in items)
    out = np.zeros((length, 2))
    for y, e, g in items:
        at = int((event - e) * SR)
        out[at:at + len(y)] += y * g
    return sy.norm(out, 0.9).astype(np.float32), float(event)


def write_wav(path: Path, y: np.ndarray) -> None:
    pcm = (np.clip(y, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def read_audio(path: Path) -> np.ndarray:
    raw = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR), "-f", "f32le",
                          "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def level_db(y: np.ndarray) -> float:
    """Gain that puts the sound's loud part at TARGET_RMS_DB (RMS over the frames above -40 dB of peak)."""
    win = max(1, SR // 100)
    frames = y[: len(y) // win * win].reshape(-1, win) if len(y) >= win else y.reshape(1, -1)
    rms = np.sqrt((frames ** 2).mean(1))
    loud = rms[rms > rms.max() * 0.01] if rms.max() > 0 else rms
    cur = 20 * np.log10(np.sqrt((loud ** 2).mean()) + 1e-9)
    return float(TARGET_RMS_DB - cur)


def _index(folder: Path) -> dict:
    p = folder / "index.json"
    return load(p) if p.exists() else {}


def _save(folder: Path, index: dict) -> None:
    (folder / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")


def _shape_args(args) -> dict:
    return {k: getattr(args, k) for k in ("pitch", "bright", "drive", "reverb", "echo", "reverse", "pan")}


def _shaped(args) -> bool:
    return bool(args.pitch or args.bright != 1.0 or args.drive or args.reverb or args.echo or args.reverse or args.pan
                or args.dur or args.seed != 7)


def _design(folder: Path, index: dict, name: str, kind: str | None, layers: str | None, seed: int, args) -> dict:
    if args.dur is not None and args.dur <= 0:
        die("--dur must be positive")
    if layers:
        y, ev = layered(layers, seed, args.dur)
    else:
        if kind in index and kind not in ALL_KINDS:                # shaping a sound designed before
            info = index[kind]
            if not info.get("recipe"):
                die(f"{kind} has no recipe to reshape")
            y, ev = synth(info["recipe"], seed, args.dur)
        else:
            y, ev = synth(kind, seed, args.dur)
    y, ev = shape(y, ev, seed=seed, **_shape_args(args))
    path = folder / f"{name}.wav"
    write_wav(path, y)
    index[name] = {"file": str(path), "event": round(ev, 4), "dur": round(len(y) / SR, 3),
                   "recipe": None if layers else (index.get(kind, {}).get("recipe") or kind),
                   "layers": layers, "seed": seed, **{k: v for k, v in _shape_args(args).items() if v not in (0.0, 1.0, False)},
                   "length": args.dur}
    return index[name]


def cmd_list(args) -> None:
    for group, names in GROUPS.items():
        print(f"{group + ':':<34}{names.replace(' ', ', ')}")
    print("shape with: --pitch --dur --bright --drive --reverb --echo --reverse --pan --seed; stack with --layers")
    mine = {k: v for k, v in _index(Path(args.dir)).items() if k not in ALL_KINDS}
    if mine:
        print("designed here: " + ", ".join(f"{k} ({v['dur']:.2f} s)" for k, v in mine.items()))


def cmd_make(args) -> None:
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    index = _index(out)                                            # sounds designed earlier stay
    for k in KINDS:
        y, ev = synth(k)
        write_wav(out / f"{k}.wav", y)
        index[k] = {"file": str(out / f"{k}.wav"), "event": ev, "dur": round(len(y) / SR, 3), "recipe": k}
        print(f"- {k}: {len(y) / SR:.2f} s, event at {ev:.2f} s")
    _save(out, index)
    print(f"sounds → {out}")


def cmd_design(args) -> None:
    if not (args.kind or args.layers):
        die("design needs --kind or --layers")
    folder = Path(args.dir)
    folder.mkdir(parents=True, exist_ok=True)
    index = _index(folder)
    if args.kind and args.kind not in ALL_KINDS and args.kind not in index:
        die(f"unknown kind {args.kind} (see: sfx.py list)")
    base = args.name or args.kind or "layered"
    if not re.fullmatch(r"[\w-]+", base):
        die("--name: letters, numbers, - and _ only")
    if base in KINDS and not (args.variants > 1):
        die(f"--name {base} is a basic sound: pick another name")
    for i in range(args.variants):
        name = base if args.variants == 1 else f"{base}_{i + 1}"
        info = _design(folder, index, name, args.kind, args.layers, args.seed + i, args)
        print(f"- {name}: {info['dur']:.2f} s, event at {info['event']:.2f} s → {info['file']}")
    _save(folder, index)


def cmd_place(args) -> None:
    edl_path = Path(args.edl)
    edl = load(edl_path)
    if args.file:
        src = Path(args.file).resolve()
        if not src.exists():
            die(f"{src} doesn't exist")
        event, label = args.event or 0.0, src.name
    else:
        folder = Path(args.dir)
        if not (folder / "index.json").exists():
            cmd_make(args)
        index = _index(folder)
        kind = args.kind
        if kind not in index and kind not in ALL_KINDS:
            die(f"unknown sound {kind} (see: sfx.py list)")
        if _shaped(args):                                          # shaped here: its own file, named by its recipe
            recipe = json.dumps([kind, args.dur, args.seed, _shape_args(args)], sort_keys=True)
            kind = f"{kind}_{hashlib.sha1(recipe.encode()).hexdigest()[:6]}"
            if kind not in index:
                _design(folder, index, kind, args.kind, None, args.seed, args)
                _save(folder, index)
        elif kind not in index:
            _design(folder, index, kind, kind, None, args.seed, args)
            _save(folder, index)
        info = index[kind]
        src, event, label = Path(info["file"]).resolve(), info["event"], kind
    gain = level_db(read_audio(src)) + args.gain + (MINOR_DB if args.weight == "minor" else 0.0)
    item = {"sound": label, "source": str(src), "event": event, "gain_db": round(gain, 2),
            "anchor": anchor_from(args, edl), "offset": args.offset, "note": args.note or ""}
    edl.setdefault("sfx", []).append(item)
    edl_path.write_text(json.dumps(edl, indent=1, ensure_ascii=False), encoding="utf-8")
    a = item["anchor"]
    print(f"placed {label} at {a['name']} @ {a['time']:.2f} s{args.offset:+.2f} s (gain {gain:+.1f} dB)")
    cmd_check(args)


def resolve(segs: list[dict], fps: float, items: list[dict]) -> tuple[list[dict], list[str]]:
    """Timeline start of each sound (its event point on the anchor). Returns placed items and warnings."""
    end = total(segs, fps)
    placed, warnings = [], []
    for s in items:
        at = to_timeline(segs, fps, s["anchor"]["name"], s["anchor"]["time"])
        if at is None:
            warnings.append(f"sfx {s['sound']}: its anchor was cut out — skipped")
            continue
        start = at + s.get("offset", 0.0) - s["event"]
        if start >= end:
            continue
        placed.append({**s, "at": round(at + s.get("offset", 0.0), 3), "start": round(start, 3)})
    placed.sort(key=lambda s: s["at"])
    return placed, warnings


def cmd_check(args) -> None:
    edl = load(Path(args.edl))
    placed, warnings = resolve(edl["segments"], edl["output"]["fps"], edl.get("sfx", []))
    for s in placed:
        print(f"[{tc(s['at'])}] {s['sound']} ({s['gain_db']:+.1f} dB)  {s.get('note', '')}")
    print(f"{len(placed)} sound(s)")
    for x, y in zip(placed, placed[1:]):
        if y["at"] - x["at"] < 0.12:
            warnings.append(f"{x['sound']} and {y['sound']} land {1000 * (y['at'] - x['at']):.0f} ms apart: "
                            "they'll blur into one")
    for w in warnings:
        print("WARNING:", w)


def cmd_clear(args) -> None:
    edl_path = Path(args.edl)
    edl = load(edl_path)
    n = len(edl.pop("sfx", []))
    edl_path.write_text(json.dumps(edl, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"removed {n} sound(s)")


def sfx_graph(first_input: int, items: list[dict], total_s: float) -> tuple[list, list, str | None]:
    """Inputs and filters that lay every placed sound on the timeline, mixed into one bus."""
    if not items:
        return [], [], None
    inputs, graphs, labels = [], [], []
    for j, s in enumerate(items):
        k = first_input + j
        skip = max(0.0, -s["start"])                   # an event right at 0 starts mid-sound
        inputs += ["-ss", f"{skip:.3f}", "-i", s["source"]]
        graphs.append(f"[{k}:a:0]aformat=sample_rates=48000:channel_layouts=stereo,volume={s['gain_db']:.2f}dB,"
                      f"adelay=delays={int(max(s['start'], 0) * 1000)}:all=1,apad,"
                      f"atrim=duration={total_s:.3f}[x{j}]")
        labels.append(f"[x{j}]")
    graphs.append(f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0[sfxbus]" if len(labels) > 1
                  else f"{labels[0]}anull[sfxbus]")
    return inputs, graphs, "sfxbus"


def _add_shape(p) -> None:
    p.add_argument("--pitch", type=float, default=0.0, help="semitones (changes length, like tape)")
    p.add_argument("--dur", type=float, help="length in seconds, on sounds that have one")
    p.add_argument("--bright", type=float, default=1.0, help="0.5 dark, 1 as is, 2 bright")
    p.add_argument("--drive", type=float, default=0.0, help="saturation 0–1")
    p.add_argument("--reverb", type=float, default=0.0, help="room 0–1")
    p.add_argument("--echo", type=float, default=0.0, help="echo 0–1")
    p.add_argument("--reverse", action="store_true")
    p.add_argument("--pan", type=float, default=0.0, help="−1 left … 1 right")
    p.add_argument("--seed", type=int, default=7, help="another take of the same sound")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ingest", default="01-ingest")
    ap.add_argument("--edl", default="02-work/edl.json")
    ap.add_argument("--dir", default="02-work/sfx", help="where synthesized sounds live")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("make")
    sub.add_parser("list")
    d = sub.add_parser("design")
    d.add_argument("--kind", help="a sound from `list`, or one designed before")
    d.add_argument("--name", help="what to call it (then: place --kind <name>)")
    d.add_argument("--layers", help='stack on the event: "riser,impact@0:+2,sparkle@0.05:-6"')
    d.add_argument("--variants", type=int, default=1, help="several takes: <name>_1, _2…")
    _add_shape(d)
    p = sub.add_parser("place")
    p.add_argument("--kind", help="a sound from `list`, or a designed one")
    p.add_argument("--file", help="the user's own sound instead of a synthesized one")
    p.add_argument("--event", type=float, help="with --file: second of the sound that lands on the anchor")
    p.add_argument("--line", help="transcript line, e.g. L5")
    p.add_argument("--word", help="word of that line (default: its first word)")
    p.add_argument("--cut", type=int, help="the cut before this segment (0-based index in edl.json)")
    p.add_argument("--offset", type=float, default=0.0, help="seconds relative to the anchor")
    p.add_argument("--gain", type=float, default=0.0, help="dB over the default level (~10 dB under the voice)")
    p.add_argument("--weight", choices=["main", "minor"], default="main",
                   help="main: the sound that matters; minor: secondary ones, well under it")
    p.add_argument("--note")
    _add_shape(p)
    sub.add_parser("check")
    sub.add_parser("clear")
    args = ap.parse_args()
    if args.cmd == "place":
        if not (args.kind or args.file):
            die("place needs --kind or --file")
        if not (args.line or args.cut is not None):
            die("place needs --line (and optionally --word) or --cut")
    if args.cmd == "design" and args.variants < 1:
        die("--variants: at least 1")
    {"make": cmd_make, "list": cmd_list, "design": cmd_design, "place": cmd_place, "check": cmd_check,
     "clear": cmd_clear}[args.cmd](args)


if __name__ == "__main__":
    main()
