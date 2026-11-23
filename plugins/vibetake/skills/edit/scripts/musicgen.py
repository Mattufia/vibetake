# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""A composer in code: original music for the edit, no samples, no licenses, no GPU.

    uv run musicgen.py --mood <mood> --duration 45 [--energy "0:low,6.2:high,38:mid"] [--out ...]
    uv run musicgen.py --list                                   # moods, instruments, patterns, grooves…

Compose (all optional; what isn't said comes from the mood and the seed):
    --bpm --key --mode --progression "i VI III VII" or "Am F C G:2" --form "intro:4,verse:8,chorus:8,outro:2"
    --instrument "lead=flute,bass=bass_808" --pattern "bass=octave,keys=offbeat" --groove --kit --swing
    --hit "6.2,18.4"   an impact on those seconds; without --bpm the tempo adjusts so they land on bars
    --ending fade|ring|cut
Mix: --mute lead,drums --solo pad,bass --gain "pad=-3,lead=+2" --reverb --width --bright --warmth --lofi --sidechain
Variations and changes:
    --variations 3                    three takes (<out>_v1.wav…), each with its own seed
    --score <out>.score.json          reopen a piece: same music plus the flags given here
    --regen "chorus:lead,verse:chords"  rewrite only that part of that section (--seed for another idea)
    --stems                           one WAV per part in <out>.stems/

Every render writes <out>.score.json, the whole piece as editable data: tempo, key, parts, and per
section its bars, energy, chords, patterns and notes ([beat, length, note, velocity]; a note is a MIDI
number or a name like "C5"). Edit it and render it with --score to change anything, note by note.
<out>.beats.json has the beats, bars and sections for cutting on the music.
Then: uv run audio.py music --file <out>
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import wave
import zlib
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import _common  # noqa: E402,F401  (UTF-8 console output on Windows)
import _synth as sy  # noqa: E402
from _synth import SR  # noqa: E402

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
SCALES = {
    "major": (0, 2, 4, 5, 7, 9, 11), "minor": (0, 2, 3, 5, 7, 8, 10), "dorian": (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10), "lydian": (0, 2, 4, 6, 7, 9, 11), "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "harmonic_minor": (0, 2, 3, 5, 7, 8, 11),
}
LEVELS = {"low": 0.25, "mid": 0.55, "high": 0.9}
LEAD_FROM = 0.6      # a section gets a melody of its own from this energy up
PARTS = ("pad", "keys", "bass", "arp", "lead", "drums", "fx")
PART_MIX = {  # gain dB, pan, reverb send, echo send, high-pass Hz
    "pad": (-7, 0.0, 0.42, 0.0, 170), "keys": (-9, -0.14, 0.26, 0.0, 150), "bass": (-5, 0.0, 0.02, 0.0, 0),
    "arp": (-14, 0.24, 0.3, 0.22, 240), "lead": (-9, 0.06, 0.28, 0.16, 260), "drums": (-5, 0.0, 0.07, 0.0, 0),
    "fx": (-13, 0.0, 0.4, 0.0, 120),
}
# Pure low tones carry far more energy than their peak suggests: trimmed so they don't bury the rest.
INSTRUMENT_TRIM = {"bass_sub": 0.34, "bass_808": 0.42, "bass_synth": 0.6, "bass_acid": 0.6, "bass_electric": 0.8,
                   "bass_upright": 0.85}
GROOVES = {  # 16 steps per bar: X accent, x normal, o soft
    "none": {},
    "pop": {"kick": "X.....x.X.......", "snare": "....X.......X...", "hat": "x.o.x.o.x.o.x.o."},
    "rock": {"kick": "X.x.....X.x.....", "snare": "....X.......X...", "hat": "x.x.x.x.x.x.x.x."},
    "four": {"kick": "X...X...X...X...", "clap": "....x.......x...", "hat": "..x...x...x...x.", "shaker": "o.o.o.o.o.o.o.o."},
    "halftime": {"kick": "X.........x.....", "snare": "........X.......", "hat": "x.o.x.o.x.o.x.o."},
    "boom": {"kick": "X.....x...x.....", "snare": "....X.......X...", "hat": "x.x.x.x.x.x.x.xo"},
    "lofi": {"kick": "X......x..x.....", "snare": "....x.......x..o", "hat": "x.o.x.o.x.o.x.o.", "shaker": "..o...o...o...o."},
    "trap": {"kick": "X.....x...x..x..", "snare": "........X.......", "hat": "x.x.x.xxx.x.xxxx"},
    "funk": {"kick": "X..x..x...x.....", "snare": "....X..o.o..X..o", "hat": "xoxoxoxoxoxoxoxo"},
    "latin": {"kick": "X..x....X..x....", "rim": "..x..x....x..x..", "shaker": "xoxoxoxoxoxoxoxo", "tom": "......x.......x."},
    "shuffle": {"kick": "X.....x.X.....x.", "snare": "....X.......X...", "ride": "x..xx..xx..xx..x"},
    "brush": {"kick": "x.......o.......", "snare": "....o.......o...", "ride": "x...x.ox..x.x.ox"},
    "cinematic": {"taiko": "X.....x.X...x.x.", "tom_low": "....o.......o...", "shaker": "o.o.o.o.o.o.o.o."},
    "pulse": {"kick": "x...x...x...x...", "hat": "..o...o...o...o."},
}
PATTERNS = {
    "pad": ("hold", "swell", "stabs"),
    "keys": ("block", "offbeat", "sync", "stabs", "broken", "hold"),
    "bass": ("root", "whole", "pulse", "octave", "sync", "walk"),
    "arp": ("up", "down", "updown", "random", "up16", "updown16"),
}
ARP_ORDERS = {"up": (0, 1, 2, 3), "down": (3, 2, 1, 0), "updown": (0, 1, 2, 3, 2, 1)}


def style(bpm, progs, groove, kit, pad, keys, bass, arp, lead, swing=0.0, **kw) -> dict:
    return {"bpm": bpm, "progs": progs, "groove": groove, "kit": kit, "swing": swing,
            "inst": {"pad": pad, "keys": keys, "bass": bass, "arp": arp, "lead": lead},
            "pat": kw.get("pat", {}), "master": kw.get("master", {})}


MOODS = {
    "calm": style((70, 86), ["I V vi IV", "I IV vi V", "vi IV I V"], "pulse", "brush", "warm_pad", "piano", "bass_sub", "pluck", "lead_soft"),
    "warm": style((64, 80), ["I iii IV V", "IV I V vi", "I vi ii V"], "brush", "brush", "strings", "piano", "bass_electric", "nylon", "flute"),
    "lofi": style((74, 90), ["ii7 V7 Imaj7 vi7", "Imaj7 vi7 ii7 V7", "IVmaj7 iii7 ii7 Imaj7"], "lofi", "lofi", "warm_pad", "epiano", "bass_upright", "kalimba", "lead_soft", swing=0.18, pat={"keys": "sync", "bass": "sync"}, master={"lofi": 0.6, "bright": 0.65, "warmth": 0.35}),
    "upbeat": style((104, 122), ["I V vi IV", "vi IV I V", "I IV V IV"], "pop", "pop", "warm_pad", "piano", "bass_electric", "synth_pluck", "lead_square", pat={"keys": "offbeat", "bass": "octave"}),
    "energetic": style((122, 136), ["i VI III VII", "vi IV I V", "i VII VI VII"], "four", "electronic", "warm_pad", "synth_pluck", "bass_synth", "synth_pluck", "lead_saw", pat={"bass": "pulse", "keys": "stabs", "arp": "up16"}, master={"sidechain": 0.55}),
    "cinematic": style((60, 76), ["i VI III VII", "i iv VI V", "VI VII i i"], "cinematic", "cinematic", "strings", "piano", "bass_sub", "harp", "brass", pat={"bass": "whole", "keys": "broken"}, master={"reverb": 0.34, "width": 1.25}),
    "tense": style((84, 100), ["i i VI V", "i iv i V", "i II i VII"], "pulse", "cinematic", "glass_pad", "bell", "bass_sub", "pluck", "strings", pat={"bass": "pulse", "keys": "stabs"}),
    "corporate": style((96, 114), ["I V vi IV", "IV I V vi", "I iii vi IV"], "pop", "pop", "glass_pad", "piano", "bass_electric", "guitar", "lead_soft", pat={"keys": "offbeat", "arp": "up"}),
    "inspiring": style((88, 108), ["vi IV I V", "I V vi IV", "IV V vi I"], "pop", "pop", "strings", "piano", "bass_electric", "harp", "strings", pat={"keys": "broken", "bass": "octave"}, master={"width": 1.2}),
    "happy": style((112, 130), ["I IV V I", "I vi IV V", "IV V I vi"], "pop", "pop", "warm_pad", "marimba", "bass_electric", "kalimba", "flute", pat={"keys": "offbeat", "bass": "octave"}),
    "playful": style((100, 124), ["I vi ii V", "I IV I V", "ii V I vi"], "shuffle", "brush", "glass_pad", "marimba", "bass_upright", "pluck", "flute", swing=0.14, pat={"keys": "sync", "bass": "walk"}),
    "romantic": style((62, 78), ["I vi IV V", "IVmaj7 iii7 vi7 V", "Imaj7 vi7 ii7 V7"], "brush", "brush", "strings", "piano", "bass_upright", "harp", "flute", pat={"keys": "broken", "bass": "whole"}),
    "acoustic": style((84, 112), ["I V vi IV", "I IV vi V", "vi IV I V"], "pop", "acoustic", "warm_pad", "guitar", "bass_electric", "nylon", "flute", pat={"keys": "broken", "arp": "updown"}),
    "piano": style((64, 92), ["I V vi IV", "vi IV I V", "I iii IV iv"], "none", "brush", "glass_pad", "piano", "bass_sub", "piano", "piano", pat={"keys": "broken", "bass": "whole"}),
    "ambient": style((56, 72), ["Imaj7 IVmaj7", "vi7 IVmaj7", "I V"], "none", "brush", "glass_pad", "bell", "bass_sub", "kalimba", "choir", pat={"bass": "whole", "keys": "hold"}, master={"reverb": 0.5, "width": 1.35}),
    "jazz": style((92, 132), ["ii7 V7 Imaj7 vi7", "Imaj7 vi7 ii7 V7", "iii7 vi7 ii7 V7"], "shuffle", "brush", "warm_pad", "epiano", "bass_upright", "pluck", "lead_soft", swing=0.22, pat={"keys": "sync", "bass": "walk"}),
    "funk": style((96, 116), ["i7 IV7", "i7 i7 IV7 V7", "ii7 V7"], "funk", "acoustic", "organ", "epiano", "bass_electric", "guitar", "brass", swing=0.08, pat={"keys": "sync", "bass": "sync"}),
    "house": style((120, 128), ["vi IV I V", "i VI III VII", "ii7 V7 Imaj7 vi7"], "four", "electronic", "warm_pad", "organ", "bass_synth", "synth_pluck", "lead_saw", pat={"keys": "offbeat", "bass": "pulse", "arp": "up16"}, master={"sidechain": 0.65}),
    "synthwave": style((84, 112), ["i VI III VII", "vi IV I V", "i VII VI V"], "pop", "electronic", "warm_pad", "synth_pluck", "bass_synth", "synth_pluck", "lead_saw", pat={"bass": "pulse", "arp": "up16"}, master={"reverb": 0.32, "sidechain": 0.4}),
    "trap": style((130, 160), ["i VI iv V", "i i VI VII", "i iv VI V"], "trap", "trap", "glass_pad", "bell", "bass_808", "pluck", "flute", pat={"bass": "sync", "keys": "stabs"}),
    "hiphop": style((80, 98), ["i VI III VII", "ii7 V7 Imaj7 vi7", "i iv VII III"], "boom", "lofi", "warm_pad", "epiano", "bass_sub", "pluck", "lead_soft", swing=0.12, pat={"keys": "stabs", "bass": "sync"}),
    "rock": style((104, 140), ["I V vi IV", "i VI III VII", "I bVII IV I"], "rock", "acoustic", "organ", "guitar", "bass_electric", "guitar", "lead_saw", pat={"keys": "block", "bass": "pulse"}, master={"warmth": 0.4}),
    "latin": style((92, 118), ["i iv V i", "vi IV I V", "i VII VI V"], "latin", "acoustic", "warm_pad", "nylon", "bass_electric", "marimba", "brass", pat={"keys": "sync", "bass": "sync"}),
    "epic": style((70, 96), ["i VI III VII", "i iv VI V", "VI VII i i"], "cinematic", "cinematic", "strings", "brass", "bass_sub", "harp", "choir", pat={"bass": "octave", "keys": "stabs", "arp": "up16"}, master={"reverb": 0.36, "width": 1.3, "warmth": 0.25}),
    "dark": style((70, 96), ["i bII i V", "i iv bII i", "i VI iv V"], "halftime", "cinematic", "glass_pad", "organ", "bass_sub", "bell", "choir", pat={"bass": "whole", "keys": "hold"}),
    "mystery": style((72, 92), ["i bVI i V", "i iv i bII", "i VII VI V"], "pulse", "brush", "glass_pad", "bell", "bass_upright", "kalimba", "flute", pat={"bass": "walk", "keys": "stabs"}),
    "sad": style((58, 76), ["vi IV I V", "i VI iv V", "i iv VII III"], "none", "brush", "strings", "piano", "bass_sub", "harp", "strings", pat={"keys": "broken", "bass": "whole"}),
}
ENERGY_BY_NAME = {"intro": 0.3, "verse": 0.55, "build": 0.68, "pre": 0.68, "chorus": 0.92, "drop": 1.0, "main": 0.78,
                  "bridge": 0.45, "break": 0.3, "solo": 0.8, "outro": 0.35}


def die(msg: str) -> None:
    raise SystemExit(f"ERROR: {msg}")


# ---------------------------------------------------------------- harmony

ROMAN = {"i": 0, "ii": 1, "iii": 2, "iv": 3, "v": 4, "vi": 5, "vii": 6}
QUALITY = {"": None, "maj": (0, 4, 7), "m": (0, 3, 7), "min": (0, 3, 7), "5": (0, 7, 12), "7": None, "maj7": (0, 4, 7, 11),
           "m7": (0, 3, 7, 10), "min7": (0, 3, 7, 10), "dim": (0, 3, 6), "dim7": (0, 3, 6, 9), "aug": (0, 4, 8),
           "sus2": (0, 2, 7), "sus4": (0, 5, 7), "add9": (0, 4, 7, 14), "madd9": (0, 3, 7, 14), "6": (0, 4, 7, 9),
           "m6": (0, 3, 7, 9), "9": (0, 4, 7, 10, 14), "m9": (0, 3, 7, 10, 14), "maj9": (0, 4, 7, 11, 14)}


def parse_chord(sym: str, key_pc: int, mode: str) -> dict:
    """A chord as a roman numeral in the key (vi, bVII, V7, Imaj7, ii/IV style bass with /E) or by name (Am, F#m7, C/E)."""
    body, _, slash = sym.strip().partition("/")
    bass = None
    m = re.fullmatch(r"([b#]?)(VII|VI|IV|V|III|II|I|vii|vi|iv|v|iii|ii|i)(.*)", body)
    if m and m.group(3) in QUALITY:
        acc, num, q = m.groups()
        degree = SCALES[mode][ROMAN[num.lower()]] + {"b": -1, "#": 1, "": 0}[acc]
        root = (key_pc + degree) % 12
        minor = num.islower()
        if q == "7":
            ints = (0, 3, 7, 10) if minor else (0, 4, 7, 10)
        elif q == "maj7":
            ints = (0, 4, 7, 11)
        elif q in ("", "maj"):
            ints = (0, 3, 7) if minor else (0, 4, 7)
        else:
            ints = QUALITY[q]
    else:
        n = re.fullmatch(r"([A-G][#b]?)(.*)", body)
        if not n or n.group(2) not in QUALITY:
            die(f"can't read the chord '{sym}' (roman like vi, bVII, V7, Imaj7, or a name like Am, F#m7, Csus4, C/E)")
        root = sy.pitch_class(n.group(1))
        q = n.group(2)
        ints = (0, 4, 7) if q == "" else ((0, 4, 7, 10) if q == "7" else QUALITY[q])
    if slash:
        try:
            bass = sy.pitch_class(slash)
        except ValueError:
            die(f"can't read the bass note in '{sym}'")
    return {"root": root, "intervals": list(ints), "bass": root if bass is None else bass}


def expand_chords(chords: list[str], bars: int) -> list[str]:
    """['Am:2', 'F'] over n bars, cycling: a :N holds the chord N bars."""
    cycle = []
    for c in chords:
        name, _, hold = c.partition(":")
        cycle += [name] * max(1, int(hold or 1))
    return [cycle[i % len(cycle)] for i in range(bars)]


def voiced(chord: dict, center: float, count: int | None = None) -> list[int]:
    """The chord's notes in the octave nearest the center: smooth motion from chord to chord."""
    out = []
    for iv in chord["intervals"][:count]:
        pc = chord["root"] + iv
        k = round((center - pc) / 12)
        out.append(int(pc + 12 * k))
    return sorted(set(out))


def bass_note(chord: dict, low: int = 36) -> int:
    n = chord["bass"]
    while n < low:
        n += 12
    return n


# ---------------------------------------------------------------- the piece as data

def parse_kv(spec: str | None, flag: str) -> dict[str, str]:
    out = {}
    for item in (spec or "").split(","):
        if not item.strip():
            continue
        k, sep, v = item.partition("=")
        if not sep:
            die(f"{flag}: '{item}' — use part=value")
        k = k.strip()
        if k not in PARTS:
            die(f"{flag}: no part '{k}' (parts: {', '.join(PARTS)})")
        out[k] = v.strip()
    return out


def parse_energy(spec: str | None) -> list[tuple[float, float]] | None:
    """'0:low,6.2:0.8' → [(0.0, .25), (6.2, .8)]."""
    if not spec:
        return None
    out = []
    for part in spec.split(","):
        t, _, lvl = part.strip().partition(":")
        try:
            value = LEVELS[lvl] if lvl in LEVELS else float(lvl)
            at = float(t)
        except ValueError:
            die(f"--energy: '{part}' — use <seconds>:low|mid|high or 0–1")
        if not 0 <= value <= 1:
            die(f"--energy: '{part}' — a number must be 0–1")
        out.append((at, value))
    return sorted(out)


def level_at(energy: list[tuple[float, float]], t: float) -> float:
    lvl = energy[0][1]
    for at, v in energy:
        if at <= t:
            lvl = v
    return lvl


def fit_tempo(bpm: float, duration: float, hits: list[float], low: float, high: float) -> float:
    """The nearest tempo where the hits land on bar lines and the piece ends on one."""
    best, best_cost = bpm, float("inf")
    for cand in np.arange(max(low, bpm * 0.95), min(high, bpm * 1.05) + 0.01, 0.05):
        bar = 240 / cand
        def miss(t):
            r = t % bar
            return min(r, bar - r)
        cost = sum(miss(t) ** 2 for t in hits) * 40 + miss(duration) ** 2 * 2 + ((cand - bpm) / bpm) ** 2
        if cost < best_cost - 1e-12:
            best, best_cost = float(cand), cost
    return round(best, 2)


def plan_sections(duration: float, bar: float, energy, form: str | None) -> list[dict]:
    n = max(1, int(np.ceil(duration / bar - 1e-6)))
    if form:
        out = []
        for item in form.split(","):
            name, _, count = item.strip().partition(":")
            if not count.isdigit() or int(count) < 1:
                die(f"--form: '{item}' — use name:bars, e.g. intro:4,verse:8,chorus:8")
            base = re.sub(r"\d+$", "", name)
            out.append({"name": name, "bars": int(count), "energy": ENERGY_BY_NAME.get(base, 0.6)})
    elif energy:
        levels = [level_at(energy, (b + 0.5) * bar) for b in range(n)]
        out = []
        for lv in levels:
            if out and out[-1]["energy"] == lv:
                out[-1]["bars"] += 1
            else:
                word = "low" if lv < 0.4 else ("mid" if lv < 0.72 else "high")
                out.append({"name": f"{word}{len(out) + 1}", "bars": 1, "energy": lv})
    elif n <= 3:
        out = [{"name": "main", "bars": n, "energy": 0.78}]
    elif n <= 8:
        out = [{"name": "intro", "bars": 1 + (n > 5), "energy": 0.4}]
        out.append({"name": "main", "bars": n - out[0]["bars"], "energy": 0.85})
    else:
        intro, outro = max(2, round(n * 0.12)), max(1, round(n * 0.08))
        body = n - intro - outro
        unit = max(2, body // (4 if body >= 16 else 2))
        names, left, out = ["verse", "chorus", "verse2", "chorus2", "bridge", "chorus3"], body, []
        out.append({"name": "intro", "bars": intro, "energy": 0.3})
        for i, nm in enumerate(names):
            if left <= 0:
                break
            take = left if i == len(names) - 1 or left < unit + 2 else unit
            out.append({"name": nm, "bars": take, "energy": ENERGY_BY_NAME[re.sub(r"\d", "", nm)]})
            left -= take
        out.append({"name": "outro", "bars": outro, "energy": 0.35})
    return out


def make_motif(rng) -> list[tuple[float, float, int]]:
    """Two bars of rhythm and contour: (beat, length, scale step). It returns and varies through the piece."""
    cells = [[(0, 1), (1, 0.5), (1.5, 0.5), (2, 2)], [(0, 1.5), (1.5, 0.5), (2, 1), (3, 1)],
             [(0.5, 0.5), (1, 1), (2, 0.5), (2.5, 1.5)], [(0, 0.75), (0.75, 0.75), (1.5, 0.5), (2, 2)],
             [(0, 2), (2, 1), (3, 0.5), (3.5, 0.5)], [(0, 1), (1, 1), (2, 1), (3, 1)]]
    a, b = cells[rng.integers(len(cells))], cells[rng.integers(len(cells))]
    notes, step = [], int(rng.integers(0, 5))
    for bar, cell in enumerate((a, b)):
        for beat, ln in cell:
            step = int(np.clip(step + rng.choice([-2, -1, -1, 1, 1, 2, 3]), -2, 9))
            notes.append((bar * 4 + beat, ln, step))
    return notes


def melody(section: dict, chords: list[dict], scale: list[int], key_pc: int, motif, seed: int) -> list[list]:
    rng = np.random.default_rng([seed, zlib.crc32(section["name"].encode())])
    pitches = sorted({12 * o + key_pc + s for o in range(5, 8) for s in scale if 66 <= 12 * o + key_pc + s <= 88})
    base = 0 if not pitches else len(pitches) // 3
    out = []
    for rep in range(int(np.ceil(section["bars"] / 2))):
        shift = 0 if rep % 2 == 0 else int(rng.choice([-2, 2, 3]))
        for beat, ln, step in motif:
            at = rep * 8 + beat
            bar = int(at // 4)
            if bar >= section["bars"]:
                continue
            if rep % 2 and rng.random() < 0.16:                   # the answer leaves some notes out
                continue
            p = pitches[int(np.clip(base + step + shift, 0, len(pitches) - 1))]
            if beat % 2 == 0:                                     # strong beats land on a chord tone
                tones = [t for t in pitches if t % 12 in {(chords[bar]["root"] + i) % 12 for i in chords[bar]["intervals"]}]
                if tones:
                    p = min(tones, key=lambda t: abs(t - p))
            vel = round(float(np.clip((0.82 if beat % 2 == 0 else 0.66) + rng.normal(0, 0.05), 0.35, 1)), 2)
            out.append([round(at, 3), ln, int(p), vel])
    return out


def build_score(a, seed: int) -> dict:
    st = MOODS[a.mood]
    rng = np.random.default_rng([seed, 7])
    low, high = st["bpm"]
    bpm = float(a.bpm) if a.bpm else float(rng.integers(low, high + 1))
    key = a.key or NOTES[rng.integers(12)]
    prog_text = st["progs"][rng.integers(len(st["progs"]))]
    progression = re.split(r"[\s,]+", a.progression.strip()) if a.progression else prog_text.split()
    mode = a.mode or ("minor" if any(re.match(r"[b#]?i(?![iv])", c) for c in progression) else "major")
    hits = parse_times(a.hit)
    if not a.bpm:
        bpm = fit_tempo(bpm, a.duration, hits, low, high)
    bar = 240 / bpm
    sections = plan_sections(a.duration, bar, parse_energy(a.energy), a.form)
    parts = {}
    for name in PARTS:
        g, p, rv, ec, hp = PART_MIX[name]
        parts[name] = {"gain": g, "pan": p, "reverb": rv, "echo": ec, "highpass": hp, "mute": False}
        if name in st["inst"]:
            parts[name]["instrument"] = st["inst"][name]
        if name in PATTERNS:
            parts[name]["pattern"] = st["pat"].get(name) or PATTERNS[name][0]
    for s in sections:
        s["chords"] = list(progression)
    master = {"reverb": 0.22, "width": 1.0, "bright": 1.0, "warmth": 0.15, "lofi": 0.0, "sidechain": 0.3,
              **st["master"]}
    score = {"version": 1, "mood": a.mood, "seed": seed, "duration": a.duration, "bpm": bpm, "key": key, "mode": mode,
             "swing": st["swing"], "groove": st["groove"], "kit": st["kit"], "progression": progression,
             "energy": a.energy or "arc", "ending": "fade", "hits": hits, "parts": parts, "master": master,
             "sections": sections}
    write_melodies(score)
    return score


def write_melodies(score: dict, only: tuple[str, ...] | None = None) -> None:
    """Writes the lead's notes into each section that has none, from one motif for the whole piece."""
    key_pc = sy.pitch_class(score["key"])
    scale = list(SCALES[score["mode"]])
    motif = make_motif(np.random.default_rng([score["seed"], 21]))
    for s in score["sections"]:
        if only is not None and s["name"] not in only:
            continue
        notes = s.setdefault("notes", {})
        wanted = "lead" in s["parts"] if s.get("parts") else float(s.get("energy", 0.6)) >= LEAD_FROM
        if "lead" not in notes and wanted:                        # quiet sections stay free for the voice
            chords = [parse_chord(c, key_pc, score["mode"]) for c in expand_chords(s["chords"], s["bars"])]
            notes["lead"] = melody(s, chords, scale, key_pc, motif, score["seed"])


def parse_times(spec: str | None) -> list[float]:
    if not spec:
        return []
    try:
        return sorted(float(x) for x in spec.split(",") if x.strip())
    except ValueError:
        die(f"--hit: '{spec}' — use seconds, e.g. 6.2,18.4")


def transpose(score: dict, semitones: int) -> None:
    """A new key on a reopened piece: written notes and chords by name move with it (roman numerals follow the key)."""
    def name(m: re.Match) -> str:
        return NOTES[(sy.pitch_class(m.group(0)) + semitones) % 12]

    def chord(c: str) -> str:
        if re.match(r"[b#]?(VII|VI|IV|V|III|II|I|vii|vi|iv|v|iii|ii|i)(?![a-z#])|[b#]?(vii|vi|iv|iii|ii)", c):
            return c
        head, _, hold = c.partition(":")
        body, slash, bass = head.partition("/")
        out = re.sub(r"^[A-G][#b]?", name, body) + (slash + re.sub(r"^[A-G][#b]?", name, bass) if slash else "")
        return out + (f":{hold}" if hold else "")
    score["progression"] = [chord(c) for c in score["progression"]]
    for s in score["sections"]:
        s["chords"] = [chord(c) for c in s["chords"]]
        for written in s.get("notes", {}).values():
            for item in written:
                if len(item) == 4:
                    try:
                        item[2] = sy.note_number(item[2]) + semitones
                    except ValueError:
                        pass                                       # render reports the bad note


def apply_changes(score: dict, a) -> None:
    """The flags change the piece the same way on a new score and on a reopened one."""
    if a.score and a.key and sy.pitch_class(a.key) != sy.pitch_class(score["key"]):
        transpose(score, (sy.pitch_class(a.key) - sy.pitch_class(score["key"]) + 6) % 12 - 6)
    if a.score and a.mode and a.mode != score["mode"]:             # another scale: the melody is written again
        for s in score["sections"]:
            s.get("notes", {}).pop("lead", None)
    for flag in ("key", "mode", "swing", "groove", "kit", "ending"):
        v = getattr(a, flag, None)
        if v is not None:
            score[flag] = v
    if a.score:
        if a.bpm:
            score["bpm"] = float(a.bpm)
        if a.duration:
            score["duration"] = a.duration
        if a.hit:
            score["hits"] = parse_times(a.hit)
        if a.progression:
            prog = re.split(r"[\s,]+", a.progression.strip())
            score["progression"] = prog
            for s in score["sections"]:
                s["chords"] = list(prog)
                s.get("notes", {}).pop("lead", None)
    for k, v in parse_kv(a.instrument, "--instrument").items():
        if v not in sy.INSTRUMENTS:
            die(f"--instrument: no '{v}' (see --list)")
        if k in ("drums", "fx"):
            die(f"--instrument: {k} has no instrument (use --kit for drums)")
        score["parts"][k]["instrument"] = v
    for k, v in parse_kv(a.pattern, "--pattern").items():
        if v not in PATTERNS.get(k, ()):
            die(f"--pattern: {k} has {', '.join(PATTERNS.get(k, ())) or 'no patterns'}")
        score["parts"][k]["pattern"] = v
    for k, v in parse_kv(a.gain, "--gain").items():
        try:
            score["parts"][k]["gain"] += float(v)
        except ValueError:
            die(f"--gain: '{k}={v}' — use dB, e.g. pad=-3")
    if a.mute:
        for k in a.mute.split(","):
            if k.strip() not in PARTS:
                die(f"--mute: no part '{k}'")
            score["parts"][k.strip()]["mute"] = True
    if a.solo:
        keep = {k.strip() for k in a.solo.split(",")}
        if keep - set(PARTS):
            die(f"--solo: no part {', '.join(keep - set(PARTS))}")
        for k, p in score["parts"].items():
            p["mute"] = k not in keep
    if a.no_melody:
        score["parts"]["lead"]["mute"] = True
    for k in ("reverb", "width", "bright", "warmth", "lofi", "sidechain"):
        v = getattr(a, k)
        if v is not None:
            score["master"][k] = v
    if a.regen:
        regen(score, a.regen, a.seed if a.seed is not None else score["seed"] + 1)
    fit_length(score)
    if score["groove"] not in GROOVES:
        die(f"no groove '{score['groove']}' (see --list)")
    if score["kit"] not in sy.KITS:
        die(f"no kit '{score['kit']}' (see --list)")
    write_melodies(score)


def regen(score: dict, spec: str, seed: int) -> None:
    """'chorus:lead,verse:chords,bridge:bass' rewrites only that part of that section."""
    rng = np.random.default_rng([seed, 99])
    names = {s["name"]: s for s in score["sections"]}
    for item in spec.split(","):
        name, _, part = item.strip().partition(":")
        if name not in names:
            die(f"--regen: no section '{name}' (sections: {', '.join(names)})")
        s = names[name]
        if part == "lead":
            s.setdefault("notes", {}).pop("lead", None)
            key_pc, scale = sy.pitch_class(score["key"]), list(SCALES[score["mode"]])
            chords = [parse_chord(c, key_pc, score["mode"]) for c in expand_chords(s["chords"], s["bars"])]
            s["notes"]["lead"] = melody(s, chords, scale, key_pc, make_motif(rng), seed)
        elif part == "chords":
            options = [p.split() for p in MOODS.get(score["mood"], {"progs": []})["progs"]]
            options = [p for p in options if p != s["chords"]] or [list(reversed(s["chords"]))]
            s["chords"] = options[rng.integers(len(options))]
            s.get("notes", {}).pop("lead", None)
        elif part in PATTERNS:
            current = s.get("patterns", {}).get(part) or score["parts"][part].get("pattern")
            options = [p for p in PATTERNS[part] if p != current]
            s.setdefault("patterns", {})[part] = options[rng.integers(len(options))]
        elif part == "drums":
            current = s.get("groove") or score["groove"]
            options = [g for g in GROOVES if g not in (current, "none")]
            s["groove"] = options[rng.integers(len(options))]
        else:
            die(f"--regen: '{item}' — use section:lead, chords, drums, {', '.join(PATTERNS)}")


def fit_length(score: dict) -> None:
    """A changed tempo or length: the last section grows so the music reaches the end."""
    need = int(np.ceil(score["duration"] / (240 / score["bpm"]) - 1e-6))
    have = sum(s["bars"] for s in score["sections"])
    if need > have:
        score["sections"][-1]["bars"] += need - have


# ---------------------------------------------------------------- performance

def part_notes(part: str, pattern: str, chord: dict, nxt: dict, center: int, rng) -> list[tuple]:
    """One bar of a part as (beat, length, midi, velocity)."""
    if part == "pad":
        tones = voiced(chord, center)
        if pattern == "stabs":
            return [(b, 0.9, m, 0.6) for b in (0, 2.5) for m in tones]
        return [(0, 4, m, 0.45 if pattern == "swell" else 0.55) for m in tones]
    if part == "keys":
        tones = voiced(chord, center)
        hits = {"block": [(0, 1.8, .8), (2, 1.8, .66)], "offbeat": [(b + .5, .4, .68) for b in range(4)],
                "sync": [(0, .7, .8), (1.5, .5, .62), (2.75, .6, .7)], "stabs": [(0, .4, .86), (2.5, .4, .7)],
                "hold": [(0, 3.9, .6)]}
        if pattern == "broken":
            order = (tones + [tones[0] + 12])[:4]
            return [(i / 2, .9, order[[0, 2, 1, 3, 2, 1, 3, 2][i] % len(order)], .62 + .14 * (i % 4 == 0)) for i in range(8)]
        return [(b, ln, m, v) for b, ln, v in hits[pattern] for m in tones]
    if part == "bass":
        r, fifth = bass_note(chord), bass_note(chord) + 7
        if pattern == "whole":
            return [(0, 3.9, r, .85)]
        if pattern == "pulse":
            return [(i / 2, .42, r, .86 if i % 4 == 0 else .68) for i in range(8)]
        if pattern == "octave":
            return [(i / 2, .4, r + 12 * (i % 2), .85 if i % 2 == 0 else .62) for i in range(8)]
        if pattern == "sync":
            return [(0, .7, r, .9), (.75, .4, r, .62), (1.5, .45, fifth, .7), (2.5, .45, r, .8), (3.25, .6, r + 12, .6)]
        if pattern == "walk":
            third = r + chord["intervals"][1]
            target = bass_note(nxt)
            return [(0, .92, r, .86), (1, .92, third, .7), (2, .92, fifth, .76), (3, .92, target + (1 if target < fifth else -1), .68)]
        return [(0, 1.9, r, .86), (2, 1.9, r, .76)]
    if part == "arp":
        tones = voiced(chord, center + 12)
        tones = (tones + [t + 12 for t in tones])[:4]
        fast = pattern.endswith("16")
        name = pattern[:-2] if fast else pattern
        steps = 16 if fast else 8
        size = 4 / steps
        if name == "random":
            pick = [tones[int(rng.integers(len(tones)))] for _ in range(steps)]
        else:
            order = ARP_ORDERS[name]
            pick = [tones[order[i % len(order)] % len(tones)] for i in range(steps)]
        return [(i * size, size * 1.5, m, .78 if i % (steps // 4) == 0 else .58) for i, m in enumerate(pick)]
    return []


def drum_hits(groove: str, energy: float) -> list[tuple[int, str, float]]:
    """The groove at this energy: little at first, everything near the top."""
    out = []
    for name, line in GROOVES[groove].items():
        if energy < 0.65 and name in ("snare", "clap", "tom", "tom_low", "rim") and groove != "cinematic":
            continue
        if energy < 0.65 and name == "shaker":
            continue
        scale = 0.72 if energy < 0.65 else 1.0
        for step, c in enumerate(line):
            if c == ".":
                continue
            if energy < 0.8 and c == "o" and name == "hat":
                continue
            out.append((step, name, {"X": 1.0, "x": 0.8, "o": 0.5}[c] * scale))
    if energy >= 0.86 and groove not in ("brush", "cinematic", "none"):
        out.append((14, "open_hat", 0.42))
    return out


def render(score: dict) -> tuple[np.ndarray, list[float], list[float], list[dict], dict[str, np.ndarray]]:
    duration, bpm = score["duration"], score["bpm"]
    beat = 60 / bpm
    total = int(round(SR * duration))
    size = total + SR * 4
    key_pc = sy.pitch_class(score["key"])
    mode = score["mode"]
    seed = score["seed"]
    rng = np.random.default_rng(seed)
    swing = float(score.get("swing", 0.0))
    buses = {p: np.zeros(size) for p in PARTS}
    cache: dict = {}
    kit = score["kit"]
    kit_cache: dict = {}
    kicks: list[int] = []
    beats: list[float] = []
    bars: list[float] = []
    info: list[dict] = []

    def when(b: float) -> float:
        frac = b % 1
        if abs(frac - 0.5) < 1e-6:
            return b + swing * 0.5
        if abs(frac - 0.25) < 1e-6 or abs(frac - 0.75) < 1e-6:
            return b + swing * 0.25
        return b

    def lay(bus: str, sig: np.ndarray, at: int, gain: float) -> None:
        if at >= size or at + len(sig) <= 0:
            return
        if at < 0:
            sig, at = sig[-at:], 0
        end = min(size, at + len(sig))
        buses[bus][at:end] += gain * sig[:end - at]

    def play(part: str, inst: str, start: float, b: float, ln: float, m: float, vel: float) -> None:
        secs = max(0.04, ln * beat)
        v = round(float(np.clip(vel + rng.normal(0, 0.035), 0.1, 1)), 1)
        k = (inst, round(m, 2), round(secs, 2), v)
        if k not in cache:
            cache[k] = sy.note(inst, m, secs, v, seed)
        loose = rng.normal(0, 0.004) if part in ("keys", "lead", "arp") else 0.0
        at = int((start + when(b) * beat + loose) * SR)
        lay(part, cache[k], at, vel * INSTRUMENT_TRIM.get(inst, 1.0))

    def hit(name: str, at: float, vel: float) -> None:
        round_robin = int(rng.integers(3))
        k = (name, round_robin)
        if k not in kit_cache:
            kit_cache[k] = sy.drum(name, kit, np.random.default_rng([seed, zlib.crc32(name.encode()), round_robin]))
        sample = int(at * SR)
        lay("drums", kit_cache[k], sample, vel * {"hat": .34, "shaker": .22, "open_hat": .3, "ride": .3, "crash": .5,
                                                  "rim": .5, "clap": .7, "snare": .78, "tom": .6, "tom_low": .7,
                                                  "taiko": .95}.get(name, 1.0))
        if name in ("kick", "taiko"):
            kicks.append(sample)

    sections = score["sections"]
    flat = [(si, b) for si, s in enumerate(sections) for b in range(s["bars"])]
    n_bars = min(len(flat), int(np.ceil(duration / (4 * beat) - 1e-6)))
    centers = {"pad": 60, "keys": 64, "arp": 72}
    bar_index = 0
    for si, s in enumerate(sections):
        if bar_index >= n_bars:
            break
        energy = float(s.get("energy", 0.6))
        chords_txt = expand_chords(s["chords"], s["bars"])
        chords = [parse_chord(c, key_pc, mode) for c in chords_txt]
        following = sections[si + 1] if si + 1 < len(sections) else None
        next_first = parse_chord(expand_chords(following["chords"], 1)[0], key_pc, mode) if following else chords[0]
        start_section = bar_index * 4 * beat
        info.append({"name": s["name"], "start": round(start_section, 3),
                     "end": round(min(duration, (bar_index + s["bars"]) * 4 * beat), 3), "energy": energy})
        active = set(s.get("parts") or [])
        if not active:
            active = {"pad", "bass"}
            if energy >= 0.35:
                active |= {"keys", "drums"}
            if energy >= 0.45:
                active.add("arp")
            active.add("fx")
        groove = s.get("groove") or score["groove"]
        pats = s.get("patterns", {})
        notes = s.get("notes", {})
        for b in range(s["bars"]):
            if bar_index >= n_bars:
                break
            t0 = bar_index * 4 * beat
            bars.append(round(t0, 3))
            beats += [round(t0 + q * beat, 3) for q in range(4)]
            chord = chords[b]
            nxt = chords[b + 1] if b + 1 < len(chords) else next_first
            last = bar_index == n_bars - 1 and score.get("ending") == "ring"
            if last:
                chord = parse_chord("i" if mode in ("minor", "dorian", "phrygian", "harmonic_minor") else "I", key_pc, mode)
            for part in ("pad", "keys", "bass", "arp"):
                p = score["parts"][part]
                if part not in active or p.get("mute") or part in notes or (last and part == "arp"):
                    continue
                pattern = "hold" if last and part != "bass" else ("whole" if last else pats.get(part) or p["pattern"])
                events = part_notes(part, pattern, chord, nxt, centers.get(part, 60), rng)
                vol = 1 / np.sqrt(max(1, len({e[0] for e in events if e[0] == 0}) and
                                      sum(1 for e in events if e[0] == events[0][0])))
                lift = 0.72 + 0.34 * energy
                for bt, ln, m, v in events:
                    play(part, p["instrument"], t0, bt, ln, m + p.get("octave", 0) * 12, v * vol * lift)
            if "drums" in active and not score["parts"]["drums"].get("mute"):
                if last:
                    hit("kick", t0, 0.9)
                    hit("crash", t0, 0.7)
                else:
                    for step, name, vel in drum_hits(groove, energy):
                        at = t0 + when(step / 4) * beat
                        hit(name, at + rng.normal(0, 0.0015), vel * float(rng.uniform(0.93, 1.0)))
                    rises = following and float(following.get("energy", 0)) > energy + 0.08
                    if rises and b == s["bars"] - 1 and groove != "none":     # a fill into every rise
                        for k in range(4):
                            hit("snare" if groove != "cinematic" else "tom", t0 + (3 + k / 4) * beat, 0.42 + 0.14 * k)
                    prev = sections[si - 1] if si else None
                    if b == 0 and energy >= 0.8 and (prev is None or float(prev.get("energy", 0)) < energy):
                        hit("crash", t0, 0.75)
            if "fx" in active and not score["parts"]["fx"].get("mute") and b == s["bars"] - 1 and following \
                    and float(following.get("energy", 0)) >= 0.75 > energy:
                n = int(4 * beat * SR)                                # air rising into the section
                air = sy.sweep(np.random.default_rng([seed, si]).standard_normal(n), 500, 7500, "bp", 0.9)
                lay("fx", sy.norm(air) * np.linspace(0, 1, n) ** 2.4, int(t0 * SR), 0.7)
            bar_index += 1
        lead = score["parts"]["lead"]
        for part, written in notes.items():                           # notes written in the score, per part
            if part not in score["parts"] or part == "drums" or score["parts"][part].get("mute"):
                continue
            if s.get("parts") and part not in s["parts"]:
                continue
            inst = score["parts"][part].get("instrument") or lead["instrument"]
            for item in written:
                try:
                    bt, ln, m, v = item
                    midi = sy.note_number(m)
                except (TypeError, ValueError) as e:
                    die(f"section {s['name']}, part {part}: bad note {item} ({e}) — [beat, length, note, velocity]")
                if bt / 4 < s["bars"]:
                    play(part, inst, start_section, float(bt), float(ln), midi, float(v) * (0.72 + 0.34 * energy))

    for at in score.get("hits", []):                                  # impacts on the edit's moments
        if not score["parts"]["fx"].get("mute") and at < duration:
            lay("fx", sy.norm(np.tanh(2 * sy.drum("taiko", "cinematic", np.random.default_rng([seed, 5])))), int(at * SR), 1.25)
            if not score["parts"]["drums"].get("mute"):
                hit("crash", at, 0.8)

    # Mix: each part filtered, placed and sent to the shared reverb and echo.
    dry = np.zeros((size, 2))
    send = np.zeros(size)
    echoes = np.zeros(size)
    stems: dict[str, np.ndarray] = {}
    pump = np.ones(size)
    depth = float(score["master"].get("sidechain", 0.0))
    if depth and kicks:
        shape = 1 - depth * np.exp(-np.arange(int(0.34 * SR)) / (0.09 * SR))
        for k in sorted({max(0, k) for k in kicks}):                  # a loose first hit can fall just before 0
            if k < size:
                end = min(size, k + len(shape))
                pump[k:end] = np.minimum(pump[k:end], shape[:end - k])
    for part in PARTS:
        x = buses[part]
        if not np.any(x):
            continue
        p = score["parts"][part]
        if p.get("highpass"):
            x = sy.highpass(x, p["highpass"])
        if part == "bass":
            x = sy.lowpass(x, 1600)
        if part in ("keys", "lead"):
            x = sy.peak_eq(x, 3000, -2.0, 0.7)                        # room for the voice
        if part == "pad":
            x = sy.peak_eq(x, 3000, -4.0, 0.6)
        x = x * 10 ** (p.get("gain", 0) / 20)
        if part not in ("drums", "fx"):
            x = x * pump
        y = sy.chorus(x) if part == "pad" else sy.pan(x, p.get("pan", 0))
        stems[part] = y
        dry += y
        send += x * p.get("reverb", 0)
        echoes += x * p.get("echo", 0)
    m = score["master"]
    if np.any(echoes):
        dry += sy.echo(echoes, beat * 0.75, 0.38) * 0.75
    if np.any(send):
        wet = sy.reverb(send, 1.2 + 2.4 * float(m.get("reverb", 0.22)), seed + 3)
        dry += wet * (0.6 + 2.0 * float(m.get("reverb", 0.22)))
    # Master: tone, width, glue, a safe ceiling.
    mix = sy.highpass(dry, 28, 2)
    bright = float(m.get("bright", 1.0))
    if abs(bright - 1) > 1e-3:
        mix = sy.shelf(mix, 3200, 12 * np.log2(max(bright, 0.05)))
    if abs(float(m.get("width", 1)) - 1) > 1e-3:
        mix = sy.width(mix, float(m["width"]))
    mix = sy.norm(mix, 0.95)
    mix = sy.saturate(mix, float(m.get("warmth", 0.0)))
    lofi = float(m.get("lofi", 0.0))
    if lofi > 0:
        t = sy.times(len(mix))
        flutter = 1 + 0.0015 * lofi * np.sin(2 * np.pi * 0.7 * t)
        idx = np.arange(len(mix)) * flutter
        mix = np.stack([np.interp(idx, np.arange(len(mix)), mix[:, c]) for c in (0, 1)], 1)
        mix = (1 - lofi * 0.5) * mix + lofi * 0.5 * sy.crush(mix, 11, 22000)
        mix = sy.lowpass(mix, 9000 - 5000 * lofi, 1)
        hiss = np.random.default_rng(seed + 8).standard_normal(len(mix)) * 0.0022 * lofi
        mix = mix + np.stack([hiss, np.roll(hiss, 91)], 1)
    mix = sy.compress(sy.norm(mix, 0.95), -13, 2.2)
    mix = mix[:total]
    ending = score.get("ending", "fade")
    fade = int(SR * ({"fade": min(2.0, duration / 4), "ring": min(1.2, duration / 4), "cut": 0.03}[ending]))
    if fade:
        mix[-fade:] *= np.linspace(1, 0, fade)[:, None] ** 1.5
    mix = sy.limiter(sy.norm(mix, 0.89), 0.89)
    scale = 0.89 / (np.abs(mix).max() + 1e-9)
    stems = {k: np.clip(v[:total] * scale * 0.7, -1, 1) for k, v in stems.items()}
    return mix * min(scale, 1.0), [b for b in beats if b < duration], [b for b in bars if b < duration], info, stems


# ---------------------------------------------------------------- files

def write_wav(path: Path, y: np.ndarray) -> None:
    pcm = (np.clip(y, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())


def output(score: dict, out: Path, want_stems: bool) -> None:
    audio, beats, bars, info, stems = render(score)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_wav(out, audio)
    meta = {"bpm": score["bpm"], "beats": beats, "bars": bars, "mood": score["mood"], "key": score["key"],
            "mode": score["mode"], "seed": score["seed"], "progression": score["progression"],
            "energy": score["energy"], "sections": info, "hits": score.get("hits", [])}
    out.with_suffix(".beats.json").write_text(json.dumps(meta), encoding="utf-8")
    out.with_suffix(".score.json").write_text(json.dumps(score, indent=1, ensure_ascii=False), encoding="utf-8")
    if want_stems:
        folder = out.with_suffix(".stems")
        folder.mkdir(exist_ok=True)
        for name, y in stems.items():
            write_wav(folder / f"{name}.wav", y)
    form = " ".join(f"{s['name']}@{s['start']:g}" for s in info)
    print(f"{out} ({score['duration']:.1f} s, {score['mood']}, {score['bpm']:g} bpm, {score['key']} {score['mode']}, "
          f"{'-'.join(score['progression'])}, seed {score['seed']}) · {len(beats)} beats · {form}"
          + (f" · stems → {out.with_suffix('.stems')}" if want_stems else ""))
    for t in score.get("hits", []):
        bar = 240 / score["bpm"]
        off = min(t % bar, bar - t % bar)
        if off > 0.06:
            print(f"note: the hit at {t:g} s is {off:.2f} s from a bar line (the impact is on it; the harmony isn't)")


def catalog() -> None:
    print("moods:       " + ", ".join(MOODS))
    print("instruments: " + ", ".join(sy.INSTRUMENTS))
    print("parts:       " + ", ".join(PARTS))
    for k, v in PATTERNS.items():
        print(f"pattern {k + ':':6}{', '.join(v)}")
    print("grooves:     " + ", ".join(GROOVES))
    print("kits:        " + ", ".join(sy.KITS))
    print("modes:       " + ", ".join(SCALES))
    print("chords:      roman (I vi bVII V7 Imaj7 ii7 Vsus4) or names (Am F#m7 C/E); :N holds N bars")
    print("sections:    any name; known energy: " + ", ".join(f"{k} {v}" for k, v in ENERGY_BY_NAME.items()))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="what's available")
    ap.add_argument("--mood", choices=tuple(MOODS), help="chosen from the video, never by habit")
    ap.add_argument("--duration", type=float, help="seconds (match the edit)")
    ap.add_argument("--score", help="reopen a piece (<out>.score.json) and apply the flags to it")
    ap.add_argument("--energy", help='"<s>:low|mid|high|0-1,..." following the edit (default: an arc)')
    ap.add_argument("--bpm", type=float, help="default: from the seed, within the mood's range")
    ap.add_argument("--key", help="C, F#, Bb… (default: from the seed)")
    ap.add_argument("--mode", choices=tuple(SCALES))
    ap.add_argument("--progression", help='chords: "i VI III VII" or "Am F C G:2"')
    ap.add_argument("--form", help='"intro:4,verse:8,chorus:8,outro:2" (name:bars)')
    ap.add_argument("--instrument", help='"lead=flute,bass=bass_808"')
    ap.add_argument("--pattern", help='"bass=octave,keys=offbeat,arp=up16"')
    ap.add_argument("--groove", help="drum groove (see --list)")
    ap.add_argument("--kit", help="drum kit (see --list)")
    ap.add_argument("--swing", type=float, help="0 straight … 0.3 heavy")
    ap.add_argument("--hit", help="impacts at these seconds of the edit: 6.2,18.4")
    ap.add_argument("--ending", choices=("fade", "ring", "cut"))
    ap.add_argument("--mute", help="parts off: lead,drums")
    ap.add_argument("--solo", help="only these parts: pad,bass")
    ap.add_argument("--gain", help='"pad=-3,lead=+2" (dB)')
    ap.add_argument("--no-melody", action="store_true", help="a bed with no lead")
    ap.add_argument("--reverb", type=float, help="room: 0 dry … 0.6 huge")
    ap.add_argument("--width", type=float, help="stereo: 0 mono, 1 normal, 1.4 wide")
    ap.add_argument("--bright", type=float, help="tone: 0.6 dark, 1 normal, 1.4 bright")
    ap.add_argument("--warmth", type=float, help="saturation 0–1")
    ap.add_argument("--lofi", type=float, help="worn tape 0–1")
    ap.add_argument("--sidechain", type=float, help="how much the kick pushes the rest, 0–1")
    ap.add_argument("--regen", help='rewrite one thing: "chorus:lead,verse:chords,bridge:drums,intro:bass"')
    ap.add_argument("--variations", type=int, default=1, help="how many takes, each with its own seed")
    ap.add_argument("--stems", action="store_true", help="one WAV per part")
    ap.add_argument("--seed", type=int, help="default: from the project folder and the length")
    ap.add_argument("--out")
    a = ap.parse_args()
    if a.list:
        catalog()
        return
    if a.key:
        try:
            sy.pitch_class(a.key)
        except ValueError:
            die(f"--key: '{a.key}' isn't a note (C, F#, Bb…)")
    if a.variations < 1:
        die("--variations: at least 1")
    if a.score:
        src = Path(a.score)
        if not src.exists():
            die(f"{src} doesn't exist")
        base = json.loads(src.read_text(encoding="utf-8"))
        out = Path(a.out or src.with_name(src.name.replace(".score.json", ".wav")))
        if a.seed is not None and not a.regen:
            base["seed"] = a.seed
        if a.variations > 1:
            die("--variations makes new pieces: use it without --score (or --regen with --seed on a score)")
        apply_changes(base, a)
        output(base, out, a.stems)
        return
    if not a.mood or not a.duration:
        die("needs --mood and --duration (or --score, or --list)")
    if a.duration <= 0:
        die("--duration must be positive")
    if a.regen:
        die("--regen changes an existing piece: use it with --score")
    seed = a.seed if a.seed is not None else zlib.crc32(f"{Path.cwd().name}|{a.duration:.2f}".encode())
    out = Path(a.out or f"02-work/music/{a.mood}.wav")
    for i in range(a.variations):
        take = seed + i
        path = out if a.variations == 1 else out.with_name(f"{out.stem}_v{i + 1}{out.suffix}")
        score = build_score(a, take)
        apply_changes(score, a)
        output(copy.deepcopy(score), path, a.stems)


if __name__ == "__main__":
    sys.exit(main())
