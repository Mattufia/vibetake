"""The composer's theory: harmony, phrasing, voice leading, melody and rhythm, from a feel.

There are no styles here. A piece starts from seven numbers read off the video (FEEL) and every
musical decision follows from them and from the rules of tonal writing:

- chords move by function (tonic → predominant → dominant → tonic) and phrases end on cadences;
- phrases come in periods: a question that ends open, an answer that closes on the tonic;
- voices move as little as they can, without parallel fifths or octaves;
- a melody is a motif that returns: chord tones on strong beats and long notes, anything else
  passes by step and resolves, leaps are small and recover in the opposite direction.
"""
from __future__ import annotations

import itertools
import re
import zlib

import numpy as np

import _synth as sy

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MAJOR = (0, 2, 4, 5, 7, 9, 11)
SCALES = {
    "major": MAJOR, "minor": (0, 2, 3, 5, 7, 8, 10), "dorian": (0, 2, 3, 5, 7, 9, 10),
    "phrygian": (0, 1, 3, 5, 7, 8, 10), "lydian": (0, 2, 4, 6, 7, 9, 11), "mixolydian": (0, 2, 4, 5, 7, 9, 10),
    "harmonic_minor": (0, 2, 3, 5, 7, 8, 11),
}
MINOR_MODES = ("minor", "dorian", "phrygian", "harmonic_minor")
FEEL = {  # what the video asks for, each 0–1
    "light": "dark, heavy → luminous, hopeful (mode and chord color)",
    "drive": "still → pushing forward (tempo, rhythm, drums)",
    "tension": "settled → unresolved (dominants, dissonance held longer)",
    "organic": "electronic → played by people (instruments, kit)",
    "size": "intimate → huge (register, room, width)",
    "color": "plain triads → rich sevenths and ninths",
    "sway": "straight → loose, swung",
}
MODE_LIGHT = {"phrygian": 0.05, "minor": 0.3, "dorian": 0.47, "mixolydian": 0.64, "major": 0.8, "lydian": 0.97}
# Where each degree tends to go (0 = tonic): the functional pull of tonal harmony.
GRAPH = {0: {3: 3, 4: 2, 5: 3, 1: 2, 2: 1}, 1: {4: 5, 6: 1, 3: 1}, 2: {5: 4, 3: 3}, 3: {4: 4, 0: 2, 1: 2},
         4: {0: 5, 5: 3, 3: 0.5}, 5: {1: 3, 3: 4, 4: 1, 2: 0.5}, 6: {0: 4, 2: 2}}
# The chord that closes onto the tonic, and the one a phrase can stop on, per mode.
CADENCE = {"major": 4, "minor": 4, "harmonic_minor": 4, "dorian": 3, "mixolydian": 6, "phrygian": 1, "lydian": 1}
HALF = {"major": 4, "minor": 4, "harmonic_minor": 4, "dorian": 6, "mixolydian": 3, "phrygian": 6, "lydian": 4}
NUMERALS = ("I", "II", "III", "IV", "V", "VI", "VII")
ROMAN = {"i": 0, "ii": 1, "iii": 2, "iv": 3, "v": 4, "vi": 5, "vii": 6}
QUALITY = {"": None, "maj": (0, 4, 7), "m": (0, 3, 7), "min": (0, 3, 7), "5": (0, 7, 12), "7": None, "maj7": (0, 4, 7, 11),
           "m7": (0, 3, 7, 10), "min7": (0, 3, 7, 10), "m7b5": (0, 3, 6, 10), "dim": (0, 3, 6), "dim7": (0, 3, 6, 9),
           "aug": (0, 4, 8), "sus2": (0, 2, 7), "sus4": (0, 5, 7), "add9": (0, 4, 7, 14), "madd9": (0, 3, 7, 14),
           "6": (0, 4, 7, 9), "m6": (0, 3, 7, 9), "9": (0, 4, 7, 10, 14), "m9": (0, 3, 7, 10, 14),
           "maj9": (0, 4, 7, 11, 14)}
RANGES = {"pad": (52, 72), "keys": (55, 76), "arp": (64, 84)}
BASS_RANGE = (31, 55)
# Timbres as coordinates, not genres: (organic, brightness).
TIMBRE = {
    "pad": {"warm_pad": (.35, .4), "glass_pad": (.1, .8), "strings": (.95, .55), "choir": (.85, .45), "organ": (.6, .6)},
    "keys": {"piano": (1, .6), "epiano": (.6, .45), "guitar": (.95, .65), "nylon": (.95, .4), "marimba": (.85, .75),
             "synth_pluck": (.05, .75), "bell": (.3, .95), "organ": (.6, .5)},
    "bass": {"bass_upright": (1, .25), "bass_electric": (.8, .5), "bass_synth": (.15, .6), "bass_sub": (.1, .15),
             "bass_808": (.05, .3), "bass_acid": (0, .85)},
    "arp": {"harp": (.95, .7), "nylon": (.95, .4), "kalimba": (.8, .8), "marimba": (.85, .6), "pluck": (.4, .55),
            "synth_pluck": (.05, .8)},
    "lead": {"flute": (.95, .7), "strings": (.95, .5), "brass": (.85, .75), "piano": (1, .6), "lead_soft": (.35, .4),
             "lead_saw": (.05, .8), "lead_square": (0, .6), "choir": (.85, .4)},
}


def die(msg: str) -> None:
    raise SystemExit(f"ERROR: {msg}")


def base_name(name: str) -> str:
    return re.sub(r"\d+$", "", name)


# ---------------------------------------------------------------- the feel

def parse_feel(spec: str | None) -> dict[str, float]:
    """'light=.7,drive=.4' → all seven values; what isn't said sits in the middle."""
    out = dict.fromkeys(FEEL, 0.5)
    for item in (spec or "").split(","):
        if not item.strip():
            continue
        k, sep, v = item.partition("=")
        k = k.strip()
        if not sep or k not in FEEL:
            die(f"--feel: '{item}' — use name=0..1 with {', '.join(FEEL)}")
        try:
            value = float(v)
        except ValueError:
            die(f"--feel: '{item}' — the value is a number 0–1")
        if not 0 <= value <= 1:
            die(f"--feel: '{item}' — the value is a number 0–1")
        out[k] = value
    return out


def choose_mode(feel: dict, rng) -> str:
    target = feel["light"] - 0.08 * feel["tension"] + float(rng.normal(0, 0.035))
    return min(MODE_LIGHT, key=lambda m: abs(MODE_LIGHT[m] - target))


def choose_tempo(feel: dict, rng) -> float:
    return float(np.clip(round(60 + 76 * feel["drive"] + float(rng.normal(0, 2.5))), 52, 148))


def choose_timbre(role: str, feel: dict, rng) -> str:
    bright = 0.6 * feel["light"] + 0.4 * (1 - feel["size"] * 0.4)
    ranked = sorted(TIMBRE[role], key=lambda k: (TIMBRE[role][k][0] - feel["organic"]) ** 2 * 2
                    + (TIMBRE[role][k][1] - bright) ** 2)
    return ranked[0] if rng.random() < 0.7 else ranked[1]


def choose_kit(feel: dict) -> str:
    o, d, s = feel["organic"], feel["drive"], feel["size"]
    if s > 0.75 and d < 0.65:
        return "cinematic"
    if o >= 0.7:
        return "brush" if d < 0.42 else "acoustic"
    if o < 0.35:
        return "trap" if d > 0.72 and feel["light"] < 0.42 else "electronic"
    return "lofi" if feel["sway"] > 0.5 else "pop"


def choose_patterns(feel: dict, rng) -> dict[str, str]:
    d, sway, color = feel["drive"], feel["sway"], feel["color"]
    keys = "hold" if d < 0.2 else "broken" if d < 0.45 else "block" if d < 0.6 else ("sync" if sway > 0.4 else "offbeat")
    if sway > 0.6 and color > 0.5 and d >= 0.3:
        bass = "walk"
    elif d < 0.3:
        bass = "whole"
    elif d < 0.5:
        bass = "root"
    else:
        bass = "sync" if sway > 0.4 else ("octave" if rng.random() < 0.5 else "pulse")
    arp = "updown" if d < 0.45 else "up" if d < 0.7 else "up16"
    return {"pad": "swell" if d < 0.35 else "hold", "keys": keys, "bass": bass, "arp": arp}


def make_master(feel: dict) -> dict:
    return {"reverb": round(0.14 + 0.36 * feel["size"] * (1 - 0.4 * feel["drive"]), 3),
            "width": round(0.95 + 0.4 * feel["size"], 3), "bright": round(0.78 + 0.42 * feel["light"], 3),
            "warmth": round(0.08 + 0.3 * feel["organic"], 3), "lofi": 0.0,
            "sidechain": round(0.15 + 0.5 * feel["drive"] * (1 - feel["organic"]), 3)}


def make_groove(feel: dict, rng) -> dict[str, str]:
    """A drum pattern written for this feel: 16 steps, X accent, x normal, o soft."""
    d, o, s, sway = feel["drive"], feel["organic"], feel["size"], feel["sway"]
    if d < 0.16:
        return {}
    def line(hits: dict[int, str]) -> str:
        return "".join(hits.get(i, ".") for i in range(16))
    half = d < 0.5 or (s > 0.7 and d < 0.62)
    four = d > 0.72 and o < 0.42 and sway < 0.35
    kick = {i: "X" for i in (0, 4, 8, 12)} if four else {0: "X"}
    if not four:
        options = [10, 6, 14, 3, 7, 11] if not half else [10, 6, 14]
        for i in rng.permutation(options)[:int(round(d * (3.4 if not half else 1.6)))]:
            kick[int(i)] = "x"
        if not half:
            kick[8] = "X"
    back = {8: "X"} if half else {4: "X", 12: "X"}
    big = s > 0.78 and d < 0.62
    out = {"taiko" if big else "kick": line(kick), "tom_low" if big else ("clap" if o < 0.35 else "snare"): line(back)}
    step = 1 if d > 0.8 else 2
    hats = {i: ("x" if i % 4 == 0 else "o") for i in range(0, 16, step)}
    if not big:
        out["ride" if o > 0.75 and sway > 0.45 else "hat"] = line(hats)
    if d > 0.5 and o > 0.5:
        out["shaker"] = line({i: "o" for i in range(2, 16, 4)})
    if sway > 0.55 and d > 0.4:
        out["rim"] = line({i: "o" for i in (3, 11)})
    return out


# ---------------------------------------------------------------- chords

def parse_chord(sym: str, key_pc: int, mode: str) -> dict:
    """A roman numeral in the key or a chord name. A plain numeral is that degree of the mode;
    a b or # is measured from the major scale, as written in harmony (bVI in C is always Ab)."""
    body, _, slash = sym.strip().partition("/")
    bass = None
    m = re.fullmatch(r"([b#]?)(VII|VI|IV|V|III|II|I|vii|vi|iv|v|iii|ii|i)(.*)", body)
    if m and m.group(3) in QUALITY:
        acc, num, q = m.groups()
        index = ROMAN[num.lower()]
        degree = MAJOR[index] + {"b": -1, "#": 1}[acc] if acc else SCALES[mode][index]
        root = (key_pc + degree) % 12
        minor = num.islower()
        if q == "7":
            ints = (0, 3, 7, 10) if minor else (0, 4, 7, 10)
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


def chord_tones(chord: dict) -> set[int]:
    return {(chord["root"] + i) % 12 for i in chord["intervals"]}


def chord_scale(chord: dict, key_pc: int, mode: str) -> set[int]:
    """The key's notes bent to agree with the chord: over V in minor, the leading tone is raised."""
    tones = chord_tones(chord)
    out = {(key_pc + s) % 12 for s in SCALES[mode]}
    for t in tones - out:
        out -= {(t - 1) % 12, (t + 1) % 12}
        out.add(t)
    return out | tones


def triad(mode: str, degree: int) -> tuple[int, int, int]:
    s = SCALES[mode]
    root = s[degree]
    return root, (s[(degree + 2) % 7] - root) % 12, (s[(degree + 4) % 7] - root) % 12


def usable_degrees(mode: str, tension: float) -> list[int]:
    out = []
    for d in range(7):
        _, third, fifth = triad(mode, d)
        if (third, fifth) in ((4, 7), (3, 7)) or (d == 4 and mode in ("minor", "harmonic_minor")) \
                or (tension > 0.65 and (third, fifth) == (3, 6)):
            out.append(d)
    return sorted(set(out) | {0, CADENCE[mode], HALF[mode]})


def symbol(degree: int, mode: str, extended: bool, rich: bool) -> str:
    """The degree's chord as a numeral, with the seventh or ninth the scale really gives it."""
    s = SCALES[mode]
    _, third, fifth = triad(mode, degree)
    seventh = (s[(degree + 6) % 7] - s[degree]) % 12
    if degree == 4 and mode in ("minor", "harmonic_minor"):              # the dominant is major in minor keys
        third, fifth, seventh = 4, 7, 10
    num = NUMERALS[degree]
    if third == 3 and fifth == 6:
        return num.lower() + ("m7b5" if extended and seventh == 10 else "dim")
    if third == 3:
        return num.lower() + (("m9" if rich and degree != 2 else "7") if extended else "")
    if not extended:
        return num
    if seventh == 11:
        return num + ("maj9" if rich else "maj7")
    return num + ("9" if rich and degree == 4 else "7")


def split_phrases(bars: int) -> list[int]:
    """Four-bar phrases; a loose bar stretches the last one instead of standing alone."""
    if bars <= 4:
        return [bars]
    out = [4] * (bars // 4)
    left = bars % 4
    if left == 1:
        out[-1] += 1
    elif left:
        out.append(left)
    return out


def walk(n: int, start: int, end: int, allowed: list[int], rng) -> list[int]:
    """n chords from start to end along the functional graph, never repeating one in place."""
    if n <= 1:
        return [end]
    def options(frm: int) -> tuple[list[int], np.ndarray]:
        keys = [k for k in GRAPH[frm] if k in allowed and k != frm] or [k for k in allowed if k != frm]
        w = np.array([GRAPH[frm].get(k, 0.4) for k in keys], float)
        return keys, w / w.sum()
    if n == 2:
        into = [d for d in allowed if d != end and end in GRAPH[d]] or [d for d in allowed if d != end]
        return [start if start in into else into[int(rng.integers(len(into)))], end]
    seq = [start]
    for _ in range(80):
        seq = [start]
        while len(seq) < n - 1:
            keys, w = options(seq[-1])
            seq.append(int(rng.choice(keys, p=w)))
        if seq[-1] != end and end in GRAPH[seq[-1]]:
            return seq + [end]
    if seq[-1] == end:
        seq[-1] = next(d for d in allowed if d != end and (len(seq) < 2 or d != seq[-2]))
    return seq + [end]


def section_chords(kind: str, bars: int, closes: bool, mode: str, feel: dict, voicing: dict, rng) -> list[str]:
    allowed = usable_degrees(mode, feel["tension"])
    hold = 2 if feel["drive"] < 0.3 and bars >= 4 else 1
    lift = [d for d in (3, 5, 0) if d in allowed]
    start = {"chorus": int(rng.choice(lift)), "drop": 0, "high": int(rng.choice(lift)),
             "bridge": lift[-2] if len(lift) > 1 else 0, "break": lift[-2] if len(lift) > 1 else 0}.get(kind, 0)
    phrases = split_phrases(bars)
    resolves = closes or kind in ("chorus", "drop", "main", "high", "outro")
    out, first = [], None
    for i, length in enumerate(phrases):
        slots = int(np.ceil(length / hold))
        last = i == len(phrases) - 1
        close = (last and resolves) or (not last and i % 2 == 1)
        end = 0 if close else HALF[mode]
        if first is not None and len(first) == slots and slots >= 3:         # the answer starts like the question
            seq = first[:-2] + ([CADENCE[mode], 0] if close else [first[-2], end])
            if seq[-2] == seq[-1]:
                seq[-2] = CADENCE[mode] if end != CADENCE[mode] else 1
        elif close and slots >= 2:
            seq = (walk(slots - 1, start, CADENCE[mode], allowed, rng) if slots > 2 else [CADENCE[mode]]) + [0]
        else:
            seq = walk(slots, start, end, allowed, rng)
        if first is None:
            first = seq
        lengths = [hold] * slots
        lengths[-1] -= slots * hold - length
        for degree, ln in zip(seq, lengths):
            sym = voicing[degree]
            out.append(f"{sym}:{ln}" if ln > 1 else sym)
    return out


def compose_harmony(sections: list[dict], mode: str, feel: dict, rng) -> None:
    """Writes every section's chords. Sections of the same kind share them, so the form is audible."""
    color = feel["color"]
    voicing = {d: symbol(d, mode, bool(rng.random() < color + 0.25 * feel["tension"] * (d == 4)),
                         bool(color > 0.7 and rng.random() < color - 0.3)) for d in range(7)}
    memo: dict = {}
    for i, s in enumerate(sections):
        closes = i == len(sections) - 1
        key = (base_name(s["name"]), s["bars"], closes)
        if key not in memo:
            memo[key] = section_chords(key[0], s["bars"], closes, mode, feel, voicing, rng)
        s["chords"] = list(memo[key])


def infer_key(progression: list[str]) -> tuple[str, str] | None:
    """Chords given by name say their own key: the first one is home."""
    m = re.fullmatch(r"([A-G][#b]?)(.*)", progression[0].partition(":")[0].partition("/")[0])
    if not m or m.group(2) not in QUALITY:
        return None
    minor = m.group(2).startswith("m") and not m.group(2).startswith("maj")
    return NOTES[sy.pitch_class(m.group(1))], "minor" if minor else "major"


# ---------------------------------------------------------------- voice leading

def voice_lead(prev: list[int] | None, chord: dict, low: int, high: int) -> list[int]:
    """The voicing of this chord that moves least from the last one, avoiding parallels and mud."""
    ints = list(chord["intervals"])
    if len(ints) > 4:
        ints.pop(2)                                                    # a ninth chord drops its fifth
    pcs = list(dict.fromkeys((chord["root"] + i) % 12 for i in ints))
    choices = [[n for n in range(low, high + 1) if n % 12 == pc] for pc in pcs]
    center = (low + high) / 2
    best, best_cost = None, float("inf")
    for combo in itertools.product(*choices):
        v = sorted(combo)
        if v[-1] - v[0] > 16:
            continue
        gaps = np.diff(v)
        cost = abs(float(np.mean(v)) - center) * 0.2
        cost += 5 * int((gaps <= 1).sum()) + 2.5 * int(v[0] < 57 and (gaps[:1] < 3).any())
        if prev:
            old = prev if len(prev) == len(v) else [min(prev, key=lambda p: abs(p - n)) for n in v]
            cost += sum(abs(a - b) for a, b in zip(old, v))
            for i, j in itertools.combinations(range(len(v)), 2):
                before, after = (old[j] - old[i]) % 12, (v[j] - v[i]) % 12
                if before == after and after in (0, 7) and old[i] != v[i] and (old[i] < v[i]) == (old[j] < v[j]):
                    cost += 4
        if cost < best_cost:
            best, best_cost = v, cost
    return best or sorted(c[0] for c in choices if c)


def bass_line(chords: list[dict]) -> list[int]:
    """The whole bass line at once: the octave of each root that keeps every move within a fifth,
    close to home. Looking ahead is what stops a rising line from running out of range."""
    low, high = BASS_RANGE
    home = 42
    layers = [[n for n in range(low, high + 1) if n % 12 == c["bass"]] for c in chords]
    if not layers:
        return []
    cost = [{n: abs(n - home) * 0.05 for n in layers[0]}]
    back: list[dict] = []
    for options in layers[1:]:
        now, links = {}, {}
        for n in options:
            def step(p: int) -> float:
                leap = abs(n - p)                                      # a line that must turn around does it by the least
                return cost[-1][p] + leap * 0.2 + (20 + 6 * (leap - 7) if leap > 7 else 0) + (200 if leap > 12 else 0)
            best = min(cost[-1], key=lambda p: (step(p), p))
            now[n], links[n] = step(best) + abs(n - home) * 0.05, best
        cost.append(now)
        back.append(links)
    n = min(cost[-1], key=lambda k: (cost[-1][k], k))
    line = [n]
    for links in reversed(back):
        n = links[n]
        line.append(n)
    return line[::-1]


# ---------------------------------------------------------------- melody

def make_motif(rng, drive: float = 0.5) -> list[tuple[float, float, int]]:
    """Two bars of rhythm and contour (beat, length, height): the idea that returns through the piece."""
    values = np.array([2, 1.5, 1, 0.5])
    slow, fast = np.array([.35, .25, .35, .05]), np.array([.04, .16, .4, .4])
    w = slow + (fast - slow) * drive
    notes, height = [], int(rng.integers(-1, 2))
    for bar in range(2):
        at = 0.5 if rng.random() < 0.2 else 0.0
        while at < 4 - 1e-6:
            ln = float(rng.choice(values, p=w / w.sum()))
            if at % 1 and ln > 1:                                      # off the beat, short: it leads to the next one
                ln = 0.5
            ln = min(ln, 4 - at)
            if not (at % 2 and rng.random() < 0.12):                   # a breath now and then, never on a strong beat
                height = int(np.clip(height + rng.choice([-2, -1, 1, 1, 2]), -3, 4))
                notes.append((bar * 4 + at, ln, height))
            at += ln
    return notes


def _pick(prev, prev_iv, prev_free, target, firm, tones, scale, pitches, leading, tonic, rng) -> int:
    pool = [p for p in pitches if p % 12 in (tones if firm else scale)] or list(pitches)
    if prev is None:
        return min(pool, key=lambda p: (abs(p - target), p))
    legal = [p for p in pool if abs(p - prev) <= 7 and abs(p - prev) != 6]
    if prev_free:                                                      # a passing note resolves by step onto the chord
        step = [p for p in pitches if p % 12 in tones and 0 < abs(p - prev) <= 2]
        if step:
            legal = step
    elif abs(prev_iv) >= 5:                                            # after a leap, step back the other way
        back = [p for p in legal if 0 < abs(p - prev) <= 2 and (p - prev) * prev_iv < 0]
        if back:
            legal = back
    if prev % 12 == leading and tonic in tones:                        # the leading tone rises to the tonic
        up = [p for p in legal if p == prev + 1]
        if up:
            return up[0]
    if not legal:
        legal = sorted(pool, key=lambda p: abs(p - prev))[:2]
    cost = np.array([abs(p - target) * 0.32 + {0: 1.5, 1: 0.3, 2: 0.0, 3: 0.5, 4: 0.7}.get(abs(p - prev), 1.5)
                     + (0 if p % 12 in tones else 0.35) for p in legal])
    w = np.exp(-cost * 2.2)
    return int(legal[int(rng.choice(len(legal), p=w / w.sum()))])


def melody(section: dict, chords: list[dict], key_pc: int, mode: str, motif, seed: int) -> list[list]:
    """A line in periods: the motif, its sequence, a climb to the high point, a cadence that breathes."""
    kind = base_name(section["name"])
    rng = np.random.default_rng([seed, zlib.crc32(kind.encode())])
    home = 60 + key_pc
    while home < 64:
        home += 12
    while home > 75:
        home -= 12
    low, high = max(60, home - 3), min(88, home + 16)
    leading = (key_pc + 11) % 12
    out: list[list] = []
    prev, prev_iv, prev_free = None, 0, False
    start = 0
    lift = 2 if kind in ("chorus", "drop", "high") else 0
    for length in split_phrases(section["bars"]):
        slots = [(n[0], n[1], n[2]) for n in motif]
        for b in range(length):
            bar = start + b
            chord = chords[bar]
            tones = chord_tones(chord)
            scale = chord_scale(chord, key_pc, mode)
            pitches = [p for p in range(low, high + 1) if p % 12 in scale]
            if b == length - 1:                                        # the phrase ends long, on a note that says where it is
                goal = {key_pc} if key_pc in tones else tones
                near = [p for p in pitches if p % 12 in goal] or pitches
                ref = prev if prev is not None else home
                p = min(near, key=lambda n: (abs(n - ref), n))
                if prev is not None and (abs(p - prev) == 6 or abs(p - prev) > 7):
                    p = min((n for n in pitches if n % 12 in tones), key=lambda n: abs(n - prev))
                out.append([round(bar * 4.0, 3), 3.0 if length > 1 else 2.0, int(p), 0.8])
                prev_iv = 0 if prev is None else p - prev
                prev, prev_free = p, False
                continue
            cell = b % 2
            shift = 2 if b >= 2 else 0                                 # the second time it answers a step higher
            for beat, ln, height in slots:
                if int(beat // 4) != cell:
                    continue
                at = beat - cell * 4
                x = (b + at / 4) / max(1, length)
                arch = np.sin(np.pi * min(1.0, x / 0.65) / 2) if x < 0.65 else np.cos(np.pi * (x - 0.65) / 0.7)
                target = home + lift + 5 * float(arch) + height + shift
                firm = at % 2 == 0 or ln >= 1
                p = _pick(prev, prev_iv, prev_free, target, firm, tones, scale, pitches, leading, key_pc, rng)
                vel = float(np.clip((0.84 if at % 2 == 0 else 0.68) + 0.06 * float(arch) + rng.normal(0, 0.03), .35, 1))
                out.append([round(bar * 4 + at, 3), ln, int(p), round(vel, 2)])
                prev_iv = 0 if prev is None else p - prev
                prev, prev_free = p, p % 12 not in tones
        start += length
    return resolve_dissonance(out, chords, low, high)


def resolve_dissonance(notes: list[list], chords: list[dict], low: int, high: int) -> list[list]:
    """A note off the chord that doesn't move by step to the next one becomes a chord tone."""
    def fits(p: int, other: int | None) -> bool:
        return other is None or (abs(p - other) <= 7 and abs(p - other) != 6)
    for i in range(len(notes) - 2, -1, -1):
        tones = chord_tones(chords[int(notes[i][0] // 4)])
        m, after = notes[i][2], notes[i + 1][2]
        if m % 12 in tones or abs(after - m) <= 2:
            continue
        before = notes[i - 1][2] if i else None
        options = [p for p in range(low, high + 1) if p % 12 in tones]
        legal = [p for p in options if fits(p, before) and fits(p, after)] or options
        notes[i][2] = min(legal, key=lambda p: (abs(p - m), p))
    return notes
