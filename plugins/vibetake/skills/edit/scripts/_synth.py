"""Sound engine shared by musicgen.py and sfx.py: oscillators, filters, effects, instruments
and drum kits, all computed with numpy. No samples, no models, no GPU: same result on any PC."""
from __future__ import annotations

import re
from functools import lru_cache

import numpy as np

SR = 48000
PITCH = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}


def hz(midi) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def pitch_class(name: str) -> int:
    m = re.fullmatch(r"([A-Ga-g])([#b]?)", name.strip())
    if not m:
        raise ValueError(f"not a note name: {name}")
    return (PITCH[m.group(1).upper()] + {"#": 1, "b": -1, "": 0}[m.group(2)]) % 12


def note_number(x) -> float:
    """60, "60" or "C4" → MIDI number."""
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if re.fullmatch(r"-?\d+(\.\d+)?", s):
        return float(s)
    m = re.fullmatch(r"([A-Ga-g][#b]?)(-?\d)", s)
    if not m:
        raise ValueError(f"not a note: {x} (use a MIDI number or a name like C4, F#3)")
    octave = int(m.group(2))
    base = PITCH[m.group(1)[0].upper()] + {"#": 1, "b": -1}.get(m.group(1)[1:], 0)
    return float(12 * (octave + 1) + base)


# ---------------------------------------------------------------- oscillators and envelopes

def times(n: int) -> np.ndarray:
    return np.arange(n) / SR


@lru_cache(maxsize=512)
def _table(kind: str, harmonics: int) -> np.ndarray:
    """One band-limited cycle, so high notes don't alias."""
    spec = np.zeros(1025, complex)
    for k in range(1, min(harmonics, 1023) + 1):
        amp = {"saw": 1 / k, "square": (k % 2) / k, "tri": (k % 2) / k ** 2 * (-1) ** ((k - 1) // 2),
               "sine": float(k == 1)}[kind]
        spec[k] = -1j * amp
    y = np.fft.irfft(spec, 2048)
    return y / (np.abs(y).max() + 1e-12)


def osc(kind: str, f, n: int, phase: float = 0.0) -> np.ndarray:
    freq = np.broadcast_to(np.asarray(f, float), (n,))
    harmonics = max(1, min(128, int(SR / 2 / max(float(freq.max()), 1.0))))
    table = _table(kind, harmonics)
    pos = (phase + np.cumsum(freq) / SR) % 1 * 2048
    i = pos.astype(int) % 2048
    frac = pos - np.floor(pos)
    return table[i] * (1 - frac) + table[(i + 1) % 2048] * frac


def adsr(n: int, a: float, d: float, s: float, r: float) -> np.ndarray:
    e = np.full(n, float(s))
    na = min(n, max(1, int(a * SR)))
    nd = min(n - na, int(d * SR))
    e[:na] = np.linspace(0, 1, na, endpoint=False)
    if nd:
        e[na:na + nd] = np.linspace(1, s, nd)
    elif s < 1 and na < n:
        e[na:] = s
    nr = min(n, max(1, int(r * SR)))
    e[-nr:] *= np.linspace(1, 0, nr)
    return e


def norm(y: np.ndarray, peak: float = 1.0) -> np.ndarray:
    return y / (np.abs(y).max() + 1e-9) * peak


# ---------------------------------------------------------------- filters

def fft_filter(x: np.ndarray, gain) -> np.ndarray:
    """Zero-phase filter: gain(frequencies) → linear gain. Works on mono or (n, 2)."""
    if x.ndim == 2:
        return np.stack([fft_filter(x[:, 0], gain), fft_filter(x[:, 1], gain)], 1)
    n = len(x) + 4096
    freqs = np.fft.rfftfreq(n, 1 / SR)
    return np.fft.irfft(np.fft.rfft(x, n) * gain(freqs), n)[:len(x)]


def lowpass(x: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    return fft_filter(x, lambda f: 1 / np.sqrt(1 + (f / max(cutoff, 1)) ** (2 * order)))


def highpass(x: np.ndarray, cutoff: float, order: int = 2) -> np.ndarray:
    return fft_filter(x, lambda f: 1 - 1 / np.sqrt(1 + (f / max(cutoff, 1)) ** (2 * order)))


def bandpass(x: np.ndarray, center: float, q: float = 1.0) -> np.ndarray:
    return fft_filter(x, lambda f: np.exp(-0.5 * (np.log2((f + 1) / center) * q * 2) ** 2))


def shelf(x: np.ndarray, freq: float, db: float, high: bool = True) -> np.ndarray:
    """A gentle high or low shelf (db at the far end)."""
    def gain(f):
        k = 1 / (1 + (freq / (f + 1)) ** 2) if high else 1 / (1 + ((f + 1) / freq) ** 2)
        return 10 ** (db * k / 20)
    return fft_filter(x, gain)


def peak_eq(x: np.ndarray, freq: float, db: float, q: float = 1.0) -> np.ndarray:
    return fft_filter(x, lambda f: 10 ** (db * np.exp(-0.5 * (np.log2((f + 1) / freq) * q * 2) ** 2) / 20))


def sweep(x: np.ndarray, f0: float, f1: float, mode: str = "lp", q: float = 1.5) -> np.ndarray:
    """A filter whose frequency travels f0 → f1 over the sound (overlap-add)."""
    nfft, hop = 1024, 256
    n = len(x)
    pad = np.r_[np.zeros(nfft), x, np.zeros(nfft)]
    win = np.hanning(nfft)
    freqs = np.fft.rfftfreq(nfft, 1 / SR) + 1
    frames = (len(pad) - nfft) // hop + 1
    centers = np.geomspace(max(f0, 1), max(f1, 1), frames)
    out, weight = np.zeros(len(pad)), np.zeros(len(pad))
    for i in range(0, frames, 64):
        block = range(i, min(i + 64, frames))
        idx = np.array([np.arange(j * hop, j * hop + nfft) for j in block])
        fc = centers[list(block)][:, None]
        if mode == "bp":
            h = np.exp(-0.5 * (np.log2(freqs[None, :] / fc) * q * 2) ** 2)
        elif mode == "hp":
            h = 1 - 1 / np.sqrt(1 + (freqs[None, :] / fc) ** 4)
        else:
            h = 1 / np.sqrt(1 + (freqs[None, :] / fc) ** 4)
        y = np.fft.irfft(np.fft.rfft(pad[idx] * win, axis=1) * h, nfft, axis=1) * win
        for row, at in zip(y, idx[:, 0]):
            out[at:at + nfft] += row
            weight[at:at + nfft] += win ** 2
    return (out / np.maximum(weight, 1e-3))[nfft:nfft + n]


# ---------------------------------------------------------------- effects

def stereo(y: np.ndarray) -> np.ndarray:
    return np.stack([y, y], 1) if y.ndim == 1 else y


def pan(y: np.ndarray, p: float) -> np.ndarray:
    """p: −1 left … +1 right, equal power (center = both channels at 0.707 × √2 = unity)."""
    a = (np.clip(p, -1, 1) + 1) * np.pi / 4
    return np.stack([y * np.cos(a) * np.sqrt(2), y * np.sin(a) * np.sqrt(2)], 1)


def width(y: np.ndarray, amount: float) -> np.ndarray:
    mid, side = (y[:, 0] + y[:, 1]) / 2, (y[:, 0] - y[:, 1]) / 2 * amount
    return np.stack([mid + side, mid - side], 1)


def reverb(x: np.ndarray, seconds: float = 1.6, seed: int = 11, bright: float = 5000.0) -> np.ndarray:
    """The reverb's return only (wet), stereo and decorrelated, so it stays safe in mono."""
    mono = x.mean(1) if x.ndim == 2 else x
    rng = np.random.default_rng(seed)
    ln = max(int(SR * seconds), 256)
    decay = np.exp(-np.arange(ln) / SR * (6.9 / max(seconds, 0.05)))
    shared = rng.standard_normal(ln)
    out = []
    size = len(mono) + ln
    spec = np.fft.rfft(mono, size)
    for _ in range(2):
        ir = (0.75 * shared + 0.45 * rng.standard_normal(ln)) * decay
        ir[:int(SR * 0.012)] = 0                                  # a short pre-delay keeps the attack clear
        ir = lowpass(ir, bright, 1)
        ir /= np.sqrt((ir ** 2).sum()) + 1e-12
        out.append(np.fft.irfft(spec * np.fft.rfft(ir, size), size)[:len(mono)])
    return np.stack(out, 1)


def echo(y: np.ndarray, seconds: float, feedback: float = 0.4, repeats: int = 5) -> np.ndarray:
    """Ping-pong delay return (wet)."""
    y = stereo(y)
    out = np.zeros_like(y)
    d = max(1, int(seconds * SR))
    for k in range(1, repeats + 1):
        at = d * k
        if at >= len(y):
            break
        g = feedback ** (k - 1)
        side = (k + 1) % 2
        out[at:, side] += y[:-at, :].mean(1) * g
    return out


def chorus(y: np.ndarray, depth: float = 0.0035, rate: float = 0.35) -> np.ndarray:
    """Two slowly moving copies, one per side, over the dry center."""
    t = times(len(y))
    idx = np.arange(len(y), dtype=float)
    left = np.interp(idx - SR * (0.012 + depth * (1 + np.sin(2 * np.pi * rate * t))), idx, y)
    right = np.interp(idx - SR * (0.015 + depth * (1 + np.cos(2 * np.pi * rate * 1.13 * t))), idx, y)
    return np.stack([y + 0.5 * left, y + 0.5 * right], 1) / 1.25


def saturate(y: np.ndarray, amount: float) -> np.ndarray:
    if amount <= 0:
        return y
    drive = 1 + 3 * amount
    return np.tanh(y * drive) / np.tanh(drive)


def _env_blocks(y: np.ndarray, hop: int) -> np.ndarray:
    m = np.abs(y).max(1) if y.ndim == 2 else np.abs(y)
    n = len(m) // hop + 1
    return np.array([m[i * hop:(i + 1) * hop].max(initial=0.0) for i in range(n)])


def compress(y: np.ndarray, threshold_db: float = -14.0, ratio: float = 2.5, attack: float = 0.01,
             release: float = 0.16) -> np.ndarray:
    hop = 120
    level = 20 * np.log10(_env_blocks(y, hop) + 1e-6)
    want = -np.maximum(level - threshold_db, 0) * (1 - 1 / ratio)
    a, r = np.exp(-hop / SR / attack), np.exp(-hop / SR / release)
    gain, cur = np.zeros_like(want), 0.0
    for i, w in enumerate(want):
        k = a if w < cur else r
        cur = k * cur + (1 - k) * w
        gain[i] = cur
    g = 10 ** (np.interp(np.arange(len(y)), np.arange(len(gain)) * hop, gain) / 20)
    return y * (g[:, None] if y.ndim == 2 else g)


def limiter(y: np.ndarray, ceiling: float = 0.89) -> np.ndarray:
    """Look-ahead peak limiter: no clipping, no pumping on normal material."""
    hop = 48
    peaks = _env_blocks(y, hop)
    g = np.minimum(1.0, ceiling / (peaks + 1e-9))
    k = 5
    g = np.lib.stride_tricks.sliding_window_view(np.pad(g, k, mode="edge"), 2 * k + 1).min(1)
    g = np.convolve(np.pad(g, k, mode="edge"), np.ones(2 * k + 1) / (2 * k + 1), "valid")
    gain = np.interp(np.arange(len(y)), np.arange(len(g)) * hop, g)
    out = y * (gain[:, None] if y.ndim == 2 else gain)
    return np.clip(out, -ceiling, ceiling)


def crush(y: np.ndarray, bits: int = 10, rate: int = 16000) -> np.ndarray:
    """Lower resolution: tape-and-sampler grit."""
    step = max(1, SR // rate)
    held = y[::step].repeat(step, axis=0)[:len(y)]
    q = 2 ** (bits - 1)
    return np.round(held * q) / q


# ---------------------------------------------------------------- instruments
# Each one: f(midi, samples, velocity, rng) → mono. The last `release` seconds are its tail.

def _partials(f, t, spec, rng) -> np.ndarray:
    y = np.zeros_like(t)
    for ratio, amp, decay in spec:
        if f * ratio < SR / 2.2:
            y += amp * np.sin(2 * np.pi * f * ratio * t + rng.random() * 6.28) * np.exp(-t * decay)
    return y


def _karplus(f: float, n: int, rng, tau: float, blend: float, tone: float) -> np.ndarray:
    """A plucked string: a noise burst circulating in a tuned, damped delay line."""
    period = SR / f
    ln = max(2, int(period - blend))                          # the averaging in the loop delays it by `blend` samples
    stretch = period / (ln + blend)
    m = int(n / stretch) + 2
    y = np.zeros(m)
    burst = rng.uniform(-1, 1, ln)
    burst = lowpass(burst, tone, 1)[:ln]
    y[:ln] = burst - burst.mean()
    damp = np.exp(-ln / (SR * tau))
    for i in range(ln, m, ln):
        prev = y[i - ln:i]
        loop = damp * ((1 - blend) * prev + blend * np.roll(prev, 1))
        j = min(ln, m - i)
        y[i:i + j] = loop[:j]
    return np.interp(np.arange(n) / stretch, np.arange(m), y)


def piano(m, n, v, rng):
    f, t = hz(m), times(n)
    scale = (f / 262) ** 0.35
    y = np.zeros(n)
    for k in range(1, min(15, int(18000 / f)) + 1):
        fk = f * k * np.sqrt(1 + 3e-4 * k * k)
        amp = k ** -1.45 * (1.0 if k < 3 else 0.45 + 0.75 * v)
        y += amp * np.sin(2 * np.pi * fk * t + rng.random()) * np.exp(-t * (0.8 + 0.5 * k) * scale)
    y += 0.05 * rng.standard_normal(n) * np.exp(-t * 240) * v
    return y * adsr(n, 0.002, 0, 1, 0.14)


def epiano(m, n, v, rng):
    f, t = hz(m), times(n)
    index = (0.6 + 1.8 * v) * np.exp(-t * 5)
    body = np.sin(2 * np.pi * f * t + index * np.sin(2 * np.pi * f * t))
    tine = 0.16 * v * np.sin(2 * np.pi * f * 14 * t) * np.exp(-t * 28) if f * 14 < SR / 2.2 else 0
    return (body * np.exp(-t * 1.2) + tine) * adsr(n, 0.003, 0, 1, 0.16)


def organ(m, n, v, rng):
    f, t = hz(m), times(n)
    y = sum(a * np.sin(2 * np.pi * f * r * t) for r, a in ((0.5, 0.5), (1, 1), (2, 0.6), (3, 0.3), (4, 0.2)) if f * r < 20000)
    return y * (1 + 0.08 * np.sin(2 * np.pi * 5.8 * t)) * adsr(n, 0.012, 0, 1, 0.06)


def warm_pad(m, n, v, rng):
    f = hz(m)
    y = sum(osc("saw", f * 2 ** (d / 1200), n, rng.random()) for d in (-9, -3, 4, 10))
    return lowpass(y, 500 + 1500 * v) * adsr(n, 0.55, 0.3, 0.85, 0.7)


def glass_pad(m, n, v, rng):
    f = hz(m)
    y = sum(osc("tri", f * r * 2 ** (d / 1200), n, rng.random()) * a
            for r, d, a in ((1, -5, 1), (1, 6, 1), (2, 3, 0.4), (3, -2, 0.12)))
    return y * adsr(n, 0.7, 0.4, 0.8, 0.9)


def strings(m, n, v, rng):
    f, t = hz(m), times(n)
    vib = 1 + 0.004 * np.sin(2 * np.pi * 5.2 * t + rng.random() * 6) * np.minimum(1, t / 0.5)
    y = sum(osc("saw", f * vib * 2 ** (d / 1200), n, rng.random()) for d in (-11, -5, 0, 6, 12))
    y = peak_eq(lowpass(y, 1400 + 3200 * v), 900, 3, 0.8)
    return y * adsr(n, 0.09 + 0.22 * (1 - v), 0.1, 0.9, 0.35)


def brass(m, n, v, rng):
    f = hz(m)
    y = osc("saw", f, n, rng.random()) + 0.7 * osc("saw", f * 1.003, n, rng.random())
    y = sweep(y, 500 + 500 * v, 1400 + 3500 * v, "lp")
    return y * adsr(n, 0.05, 0.12, 0.8, 0.18)


def choir(m, n, v, rng):
    f, t = hz(m), times(n)
    vib = 1 + 0.005 * np.sin(2 * np.pi * 4.8 * t)
    y = sum(osc("saw", f * vib * 2 ** (d / 1200), n, rng.random()) for d in (-7, 0, 8))
    y = sum(bandpass(y, c, 3) * a for c, a in ((680, 1.0), (1150, 0.6), (2650, 0.25)))
    return y * adsr(n, 0.35, 0.2, 0.9, 0.5)


def flute(m, n, v, rng):
    f, t = hz(m), times(n)
    vib = 1 + 0.005 * np.sin(2 * np.pi * 5 * t) * np.minimum(1, t / 0.3)
    y = osc("sine", f * vib, n) + 0.22 * osc("sine", 2 * f * vib, n) + 0.05 * osc("sine", 3 * f * vib, n)
    breath = bandpass(rng.standard_normal(n), min(f * 2, 9000), 1) * 0.12
    return (y + breath) * adsr(n, 0.05, 0.05, 0.9, 0.09)


def bell(m, n, v, rng):
    return _partials(hz(m), times(n), ((1, 1, 1.6), (2.76, 0.5, 2.8), (5.4, 0.28, 4.5), (8.93, 0.12, 7)), rng) \
        * adsr(n, 0.001, 0, 1, 0.2)


def marimba(m, n, v, rng):
    return _partials(hz(m), times(n), ((1, 1, 7), (3.93, 0.35, 16), (9.2, 0.1, 32)), rng) * adsr(n, 0.001, 0, 1, 0.06)


def kalimba(m, n, v, rng):
    return _partials(hz(m), times(n), ((1, 1, 3.6), (5.95, 0.22, 20), (2.0, 0.08, 7)), rng) * adsr(n, 0.001, 0, 1, 0.1)


def pluck(m, n, v, rng):
    t, f = times(n), hz(m)
    y = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(4 * np.pi * f * t) + 0.12 * np.sin(6 * np.pi * f * t)
    return y * np.exp(-t * 5.5) * adsr(n, 0.004, 0, 1, 0.05)


def guitar(m, n, v, rng):
    y = _karplus(hz(m), n, rng, 1.4, 0.48, 3000 + 4500 * v)
    return peak_eq(y, 240, 3, 1) * adsr(n, 0.001, 0, 1, 0.08)


def nylon(m, n, v, rng):
    return lowpass(_karplus(hz(m), n, rng, 1.0, 0.5, 1800 + 1600 * v), 3600) * adsr(n, 0.002, 0, 1, 0.08)


def harp(m, n, v, rng):
    return _karplus(hz(m), n, rng, 2.2, 0.42, 3500 + 3500 * v) * adsr(n, 0.002, 0, 1, 0.2)


def synth_pluck(m, n, v, rng):
    f, t = hz(m), times(n)
    y = osc("saw", f, n, rng.random()) + 0.6 * osc("square", f * 1.004, n, rng.random())
    return sweep(y, 5200, 500, "lp") * np.exp(-t * 6) * adsr(n, 0.002, 0, 1, 0.05)


def lead_saw(m, n, v, rng):
    f, t = hz(m), times(n)
    vib = 1 + 0.004 * np.sin(2 * np.pi * 5.5 * t) * np.minimum(1, t / 0.4)
    y = osc("saw", f * vib, n, rng.random()) + osc("saw", f * vib * 1.005, n, rng.random())
    return lowpass(y, 2200 + 3500 * v) * adsr(n, 0.012, 0.1, 0.8, 0.1)


def lead_square(m, n, v, rng):
    return lowpass(osc("square", hz(m), n, rng.random()), 3800) * adsr(n, 0.006, 0.06, 0.7, 0.06)


def lead_soft(m, n, v, rng):
    f = hz(m)
    return (osc("tri", f, n) + 0.3 * osc("sine", 2 * f, n)) * adsr(n, 0.025, 0.08, 0.85, 0.14)


def bass_sub(m, n, v, rng):
    f, t = hz(m), times(n)
    return (np.sin(2 * np.pi * f * t) + 0.2 * np.sin(4 * np.pi * f * t)) * adsr(n, 0.01, 0, 1, 0.12)


def bass_electric(m, n, v, rng):
    y = _karplus(hz(m), n, rng, 1.2, 0.5, 900 + 900 * v)
    return lowpass(y, 1500) * adsr(n, 0.002, 0, 1, 0.06)


def bass_upright(m, n, v, rng):
    t = times(n)
    return lowpass(_karplus(hz(m), n, rng, 0.75, 0.5, 700), 1000) * np.exp(-t * 1.2) * adsr(n, 0.004, 0, 1, 0.06)


def bass_synth(m, n, v, rng):
    f = hz(m)
    y = osc("saw", f, n, rng.random()) + 0.7 * osc("square", f / 2 * 2, n, rng.random())
    return lowpass(y, 420 + 700 * v) * adsr(n, 0.004, 0.12, 0.75, 0.06)


def bass_808(m, n, v, rng):
    f, t = hz(m), times(n)
    freq = f * (1 + 0.8 * np.exp(-t * 55))
    y = np.sin(2 * np.pi * np.cumsum(freq) / SR)
    return np.tanh(1.5 * y) * np.exp(-t * 1.1) * adsr(n, 0.002, 0, 1, 0.08)


def bass_acid(m, n, v, rng):
    y = osc("square", hz(m), n, rng.random())
    return sweep(y, 1800 + 1500 * v, 240, "lp") * adsr(n, 0.002, 0.05, 0.85, 0.04)


INSTRUMENTS = {   # name: (function, release seconds)
    "piano": (piano, 0.25), "epiano": (epiano, 0.3), "organ": (organ, 0.08),
    "warm_pad": (warm_pad, 0.8), "glass_pad": (glass_pad, 1.0), "strings": (strings, 0.4),
    "brass": (brass, 0.2), "choir": (choir, 0.6), "flute": (flute, 0.1), "bell": (bell, 1.4),
    "marimba": (marimba, 0.35), "kalimba": (kalimba, 0.6), "pluck": (pluck, 0.25), "guitar": (guitar, 0.5),
    "nylon": (nylon, 0.45), "harp": (harp, 0.9), "synth_pluck": (synth_pluck, 0.2), "lead_saw": (lead_saw, 0.12),
    "lead_square": (lead_square, 0.08), "lead_soft": (lead_soft, 0.16), "bass_sub": (bass_sub, 0.14),
    "bass_electric": (bass_electric, 0.12), "bass_upright": (bass_upright, 0.12), "bass_synth": (bass_synth, 0.08),
    "bass_808": (bass_808, 0.5), "bass_acid": (bass_acid, 0.06),
}


def note(instrument: str, midi: float, seconds: float, velocity: float, seed: int = 0) -> np.ndarray:
    fn, release = INSTRUMENTS[instrument]
    n = max(64, int((seconds + release) * SR))
    y = fn(midi, n, float(np.clip(velocity, 0.05, 1)), np.random.default_rng([seed, int(midi * 10), n]))
    return norm(y - y.mean())


# ---------------------------------------------------------------- drum kits

KITS = {  # kick (hz, sweep, decay, click) · snare (tone, noise cutoff, decay) · hat (cutoff, decay) · drive
    "pop": ((50, 95, 9, 0.25), (195, 1700, 18), (7200, 60), 0.2),
    "acoustic": ((62, 70, 11, 0.35), (185, 1300, 15), (6200, 48), 0.05),
    "electronic": ((47, 120, 7.5, 0.3), (210, 2400, 22), (8800, 75), 0.35),
    "lofi": ((55, 60, 12, 0.08), (175, 1000, 20), (4800, 70), 0.3),
    "trap": ((40, 130, 3.8, 0.3), (205, 2600, 24), (9500, 95), 0.3),
    "cinematic": ((38, 62, 3.8, 0.12), (130, 700, 8), (5200, 25), 0.15),
    "brush": ((58, 45, 14, 0.04), (160, 2600, 11), (5200, 34), 0.0),
}
DRUMS = ("kick", "snare", "clap", "rim", "hat", "open_hat", "shaker", "tom", "tom_low", "crash", "ride", "taiko")


def drum(kind: str, kit: str, rng) -> np.ndarray:
    (kf, ks, kd, kc), (st, sc, sd), (hc, hd), drive = KITS[kit]
    def noise(n):
        return rng.standard_normal(n)
    if kind in ("kick", "taiko"):
        f0, sw, dec = (kf, ks, kd) if kind == "kick" else (58, 48, 3.2)
        n = int(SR * min(1.4, 4.5 / dec))
        t = times(n)
        y = np.sin(2 * np.pi * np.cumsum(f0 + sw * np.exp(-t * 32)) / SR) * np.exp(-t * dec)
        y += kc * highpass(noise(n), 1800) * np.exp(-t * 320)
        if kind == "taiko":
            y += 0.28 * lowpass(noise(n), 700) * np.exp(-t * 16)
    elif kind == "snare":
        n = int(SR * 0.3)
        t = times(n)
        y = 0.65 * highpass(noise(n), sc) * np.exp(-t * sd) + 0.55 * np.sin(2 * np.pi * st * t) * np.exp(-t * 26)
    elif kind == "clap":
        n = int(SR * 0.28)
        t = times(n)
        burst = sum(np.exp(-np.maximum(t - d, 0) * 150) * (t >= d) for d in (0, 0.011, 0.023)) \
            + 0.8 * np.exp(-np.maximum(t - 0.032, 0) * 22) * (t >= 0.032)
        y = bandpass(noise(n), 1500, 0.8) * burst
    elif kind == "rim":
        n = int(SR * 0.06)
        t = times(n)
        y = (np.sin(2 * np.pi * 1700 * t) + 0.6 * np.sin(2 * np.pi * 470 * t)) * np.exp(-t * 120)
    elif kind == "hat":
        n = int(SR * 0.07)
        y = highpass(noise(n), hc) * np.exp(-times(n) * hd)
    elif kind == "open_hat":
        n = int(SR * 0.42)
        y = highpass(noise(n), hc * 0.9) * np.exp(-times(n) * 9) * adsr(n, 0.001, 0, 1, 0.05)
    elif kind == "shaker":
        n = int(SR * 0.09)
        y = bandpass(noise(n), 6000, 1.2) * adsr(n, 0.018, 0.02, 0.5, 0.04)
    elif kind in ("tom", "tom_low"):
        f = 165 if kind == "tom" else 96
        n = int(SR * 0.45)
        t = times(n)
        y = np.sin(2 * np.pi * np.cumsum(f * (1 + 0.5 * np.exp(-t * 30))) / SR) * np.exp(-t * 8) \
            + 0.12 * noise(n) * np.exp(-t * 80)
    elif kind == "crash":
        n = int(SR * 2.2)
        t = times(n)
        y = highpass(noise(n), 3200) * np.exp(-t * 2.3) * adsr(n, 0.001, 0, 1, 0.1) + \
            0.5 * bandpass(noise(n), 5200, 1) * np.exp(-t * 5)
    elif kind == "ride":
        n = int(SR * 0.6)
        t = times(n)
        y = 0.5 * highpass(noise(n), 6000) * np.exp(-t * 7) + sum(
            np.sin(2 * np.pi * f * t) for f in (3120, 4470, 5320)) * 0.12 * np.exp(-t * 6)
    else:
        raise ValueError(f"unknown drum {kind}")
    return norm(saturate(norm(y), drive))
