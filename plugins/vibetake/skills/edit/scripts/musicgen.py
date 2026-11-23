# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""A composer in code: original music for the edit, no samples, no licenses, no GPU.

    uv run musicgen.py --feel "light=.7,drive=.4,tension=.2,organic=.8,size=.3,color=.5,sway=.2" --duration 45
                       [--energy "0:low,6.2:high,38:mid"] [--out ...]
    uv run musicgen.py --list                                   # the feel, instruments, patterns, kits…

There are no styles: the feel is read from the video and the harmony, melody, rhythm and sound are
composed from it with the rules of tonal writing (functions, cadences, periods, voice leading).

Compose (all optional; what isn't said comes from the feel and the seed):
    --bpm --key --mode --progression "i VI III VII" or "Am F C G:2" --form "intro:4,verse:8,chorus:8,outro:2"
    --instrument "lead=flute,bass=bass_808" --pattern "bass=octave,keys=offbeat" --kit --swing
    --groove "kick=X...X...X...X...,snare=....X.......X..." (16 steps; "none" for no drums)
    --hit "6.2,18.4"   an impact on those seconds; without --bpm the tempo adjusts so they land on bars
    --ending ring|fade|cut
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
import _compose as cp  # noqa: E402
from _compose import FEEL, NOTES, SCALES, die, expand_chords, parse_chord  # noqa: E402,F401
from _synth import SR  # noqa: E402

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
PATTERNS = {
    "pad": ("hold", "swell", "stabs"),
    "keys": ("block", "offbeat", "sync", "stabs", "broken", "hold"),
    "bass": ("root", "whole", "pulse", "octave", "sync", "walk"),
    "arp": ("up", "down", "updown", "random", "up16", "updown16"),
}
ARP_ORDERS = {"up": (0, 1, 2, 3), "down": (3, 2, 1, 0), "updown": (0, 1, 2, 3, 2, 1)}


ENERGY_BY_NAME = {"intro": 0.3, "verse": 0.55, "build": 0.68, "pre": 0.68, "chorus": 0.92, "drop": 1.0, "main": 0.78,
                  "bridge": 0.45, "break": 0.3, "solo": 0.8, "outro": 0.35}


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
        levels = [level_at(energy, (b - b % 2 + 0.5) * bar) for b in range(n)]   # changes fall on phrase lines
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
        intro, outro = 4 if n >= 32 else 2, 2 if n >= 12 else 1
        body = n - intro - outro
        unit = 8 if body >= 24 else 4
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


def build_score(a, seed: int) -> dict:
    feel = cp.parse_feel(a.feel)
    rng = np.random.default_rng([seed, 7])
    bpm = float(a.bpm) if a.bpm else cp.choose_tempo(feel, rng)
    progression = re.split(r"[\s,]+", a.progression.strip()) if a.progression else None
    named = cp.infer_key(progression) if progression else None
    key = a.key or (named[0] if named else NOTES[rng.integers(12)])
    if a.mode:
        mode = a.mode
    elif named:
        mode = named[1]
    elif progression:
        mode = "minor" if any(re.match(r"[b#]?i(?![iv])", c) for c in progression) else "major"
    else:
        mode = cp.choose_mode(feel, rng)
    hits = parse_times(a.hit)
    if not a.bpm:
        bpm = fit_tempo(bpm, a.duration, hits, bpm * 0.95, bpm * 1.05)
    sections = plan_sections(a.duration, 240 / bpm, parse_energy(a.energy), a.form)
    if progression:
        for s in sections:
            s["chords"] = list(progression)
    else:
        cp.compose_harmony(sections, mode, feel, rng)
        progression = max(sections, key=lambda s: s["bars"])["chords"]
    patterns = cp.choose_patterns(feel, rng)
    parts = {}
    for name in PARTS:
        g, p, rv, ec, hp = PART_MIX[name]
        parts[name] = {"gain": g, "pan": p, "reverb": rv, "echo": ec, "highpass": hp, "mute": False}
        if name in cp.TIMBRE:
            parts[name]["instrument"] = cp.choose_timbre(name, feel, rng)
        if name in PATTERNS:
            parts[name]["pattern"] = patterns[name]
    swing = round(feel["sway"] * 0.26, 3) if feel["sway"] > 0.35 else 0.0
    score = {"version": 2, "feel": feel, "seed": seed, "duration": a.duration, "bpm": bpm, "key": key, "mode": mode,
             "swing": swing, "groove": cp.make_groove(feel, rng), "kit": cp.choose_kit(feel),
             "progression": list(progression), "energy": a.energy or "arc",
             "ending": "fade" if a.progression else "ring", "hits": hits,
             "parts": parts, "master": cp.make_master(feel), "sections": sections}
    write_melodies(score)
    return score


def write_melodies(score: dict, only: tuple[str, ...] | None = None) -> None:
    """Writes the lead's notes into each section that has none, from one motif for the whole piece."""
    key_pc = sy.pitch_class(score["key"])
    motif = cp.make_motif(np.random.default_rng([score["seed"], 21]), score["feel"]["drive"])
    for s in score["sections"]:
        if only is not None and s["name"] not in only:
            continue
        notes = s.setdefault("notes", {})
        wanted = "lead" in s["parts"] if s.get("parts") else float(s.get("energy", 0.6)) >= LEAD_FROM
        if "lead" not in notes and wanted:                        # quiet sections stay free for the voice
            chords = [parse_chord(c, key_pc, score["mode"]) for c in expand_chords(s["chords"], s["bars"])]
            notes["lead"] = cp.melody(s, chords, key_pc, score["mode"], motif, score["seed"])


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
    for flag in ("key", "mode", "swing", "kit", "ending"):
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
    if a.groove:
        score["groove"] = parse_groove(a.groove)
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
    if score["kit"] not in sy.KITS:
        die(f"no kit '{score['kit']}' (see --list)")
    write_melodies(score)


def parse_groove(spec: str) -> dict[str, str]:
    """'kick=X...X...,snare=....X...' → the drum lines; 'none' is no drums."""
    if spec.strip() == "none":
        return {}
    out = {}
    for item in spec.split(","):
        name, _, line = item.strip().partition("=")
        if name not in sy.DRUMS or not re.fullmatch(r"[Xxo.]{16}", line):
            die(f"--groove: '{item}' — use drum=16 steps of X x o . (drums: {', '.join(sy.DRUMS)})")
        out[name] = line
    return out


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
            key_pc = sy.pitch_class(score["key"])
            chords = [parse_chord(c, key_pc, score["mode"]) for c in expand_chords(s["chords"], s["bars"])]
            s["notes"]["lead"] = cp.melody(s, chords, key_pc, score["mode"], cp.make_motif(rng, score["feel"]["drive"]), seed)
        elif part == "chords":
            closes = s is score["sections"][-1]
            feel = score["feel"]
            voicing = {d: cp.symbol(d, score["mode"], bool(rng.random() < feel["color"]), False) for d in range(7)}
            for _ in range(12):
                new = cp.section_chords(cp.base_name(name), s["bars"], closes, score["mode"], feel, voicing, rng)
                if new != s["chords"]:
                    break
            s["chords"] = new
            s.get("notes", {}).pop("lead", None)
        elif part in PATTERNS:
            current = s.get("patterns", {}).get(part) or score["parts"][part].get("pattern")
            options = [p for p in PATTERNS[part] if p != current]
            s.setdefault("patterns", {})[part] = options[rng.integers(len(options))]
        elif part == "drums":
            s["groove"] = cp.make_groove({**score["feel"], "drive": min(1.0, score["feel"]["drive"] + 0.08)}, rng)
        else:
            die(f"--regen: '{item}' — use section:lead, chords, drums, {', '.join(PATTERNS)}")


def fit_length(score: dict) -> None:
    """A changed tempo or length: the last section grows so the music reaches the end."""
    need = int(np.ceil(score["duration"] / (240 / score["bpm"]) - 1e-6))
    have = sum(s["bars"] for s in score["sections"])
    if need > have:
        score["sections"][-1]["bars"] += need - have


# ---------------------------------------------------------------- performance

def part_notes(part: str, pattern: str, chord: dict, tones: list[int], root: int, target: int, rng) -> list[tuple]:
    """One bar of a part as (beat, length, midi, velocity). tones is the led voicing, target the next bass note."""
    if part == "pad":
        if pattern == "stabs":
            return [(b, 0.9, m, 0.6) for b in (0, 2.5) for m in tones]
        return [(0, 4, m, 0.45 if pattern == "swell" else 0.55) for m in tones]
    if part == "keys":
        hits = {"block": [(0, 1.8, .8), (2, 1.8, .66)], "offbeat": [(b + .5, .4, .68) for b in range(4)],
                "sync": [(0, .7, .8), (1.5, .5, .62), (2.75, .6, .7)], "stabs": [(0, .4, .86), (2.5, .4, .7)],
                "hold": [(0, 3.9, .6)]}
        if pattern == "broken":
            order = (tones + [tones[0] + 12])[:4]
            return [(i / 2, .9, order[[0, 2, 1, 3, 2, 1, 3, 2][i] % len(order)], .62 + .14 * (i % 4 == 0)) for i in range(8)]
        return [(b, ln, m, v) for b, ln, v in hits[pattern] for m in tones]
    if part == "bass":
        r = root
        fifth = r + next((i for i in chord["intervals"] if i in (6, 7, 8)), 7)   # the chord's real fifth
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
            lead_in = target + (1 if target < fifth else -1)             # a half step into the next root
            return [(0, .92, r, .86), (1, .92, third, .7), (2, .92, fifth, .76), (3, .92, lead_in, .68)]
        return [(0, 1.9, r, .86), (2, 1.9, r, .76)]
    if part == "arp":
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


def drum_hits(groove: dict, energy: float) -> list[tuple[int, str, float]]:
    """The groove at this energy: little at first, everything near the top."""
    out = []
    for name, line in groove.items():
        if energy < 0.65 and name in ("snare", "clap", "tom", "rim", "shaker"):
            continue
        scale = 0.72 if energy < 0.65 else 1.0
        for step, c in enumerate(line):
            if c == ".":
                continue
            if energy < 0.8 and c == "o" and name == "hat":
                continue
            out.append((step, name, {"X": 1.0, "x": 0.8, "o": 0.5}[c] * scale))
    if energy >= 0.86 and "hat" in groove:
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
    led: dict[str, list[int]] = {}                                    # each part's last voicing, so the next moves least
    home = parse_chord("i" if mode in cp.MINOR_MODES else "I", key_pc, mode)
    ring = score.get("ending") == "ring"
    harmony = [parse_chord(c, key_pc, mode) for s in sections for c in expand_chords(s["chords"], s["bars"])][:n_bars]
    if ring and harmony:
        harmony[-1] = home                                            # a ringing ending always lands at home
    line = cp.bass_line(harmony)
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
        section_first = bar_index
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
        groove = s["groove"] if "groove" in s else score["groove"]
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
            last = bar_index == n_bars - 1 and ring
            if last:
                chord = home
            root = line[bar_index]
            target = line[bar_index + 1] if bar_index + 1 < len(line) else root
            for part in ("pad", "keys", "bass", "arp"):
                p = score["parts"][part]
                if part not in active or p.get("mute") or part in notes or (last and part == "arp"):
                    continue
                pattern = "hold" if last and part != "bass" else ("whole" if last else pats.get(part) or p["pattern"])
                tones = None
                if part in cp.RANGES:
                    tones = led[part] = cp.voice_lead(led.get(part), chord, *cp.RANGES[part])
                events = part_notes(part, pattern, chord, tones, root, target, rng)
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
                    if rises and b == s["bars"] - 1 and groove:               # a fill into every rise
                        for k in range(4):
                            hit("snare" if "snare" in groove else "tom", t0 + (3 + k / 4) * beat, 0.42 + 0.14 * k)
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
                if ring and section_first + int(bt // 4) == n_bars - 1 and int(round(midi)) % 12 not in cp.chord_tones(home):
                    midi = min((midi + d for d in range(-2, 3) if int(round(midi + d)) % 12 in cp.chord_tones(home)),
                               key=lambda n: abs(n - midi), default=midi)   # the last note agrees with the last chord
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
    meta = {"bpm": score["bpm"], "beats": beats, "bars": bars, "feel": score["feel"], "key": score["key"],
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
    print(f"{out} ({score['duration']:.1f} s, {score['bpm']:g} bpm, {score['key']} {score['mode']}, "
          f"{'-'.join(score['progression'])}, seed {score['seed']}) · {len(beats)} beats · {form}"
          + (f" · stems → {out.with_suffix('.stems')}" if want_stems else ""))
    for t in score.get("hits", []):
        bar = 240 / score["bpm"]
        off = min(t % bar, bar - t % bar)
        if off > 0.06:
            print(f"note: the hit at {t:g} s is {off:.2f} s from a bar line (the impact is on it; the harmony isn't)")


def catalog() -> None:
    print("feel (0–1):")
    for k, v in FEEL.items():
        print(f"  {k:9}{v}")
    print("instruments: " + ", ".join(sy.INSTRUMENTS))
    print("parts:       " + ", ".join(PARTS))
    for k, v in PATTERNS.items():
        print(f"pattern {k + ':':6}{', '.join(v)}")
    print('groove:      written for the feel, or your own: "kick=X...X...X...X...,snare=....X.......X..."')
    print("kits:        " + ", ".join(sy.KITS))
    print("modes:       " + ", ".join(SCALES))
    print("chords:      roman (I vi bVII V7 Imaj7 ii7 Vsus4) or names (Am F#m7 C/E); :N holds N bars")
    print("sections:    any name; known energy: " + ", ".join(f"{k} {v}" for k, v in ENERGY_BY_NAME.items()))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="what's available")
    ap.add_argument("--feel", help='read from the video: "light=.7,drive=.4,tension=.2,organic=.8,size=.3,color=.5,sway=.2"')
    ap.add_argument("--duration", type=float, help="seconds (match the edit)")
    ap.add_argument("--score", help="reopen a piece (<out>.score.json) and apply the flags to it")
    ap.add_argument("--energy", help='"<s>:low|mid|high|0-1,..." following the edit (default: an arc)')
    ap.add_argument("--bpm", type=float, help="default: from the drive")
    ap.add_argument("--key", help="C, F#, Bb… (default: from the seed)")
    ap.add_argument("--mode", choices=tuple(SCALES))
    ap.add_argument("--progression", help='chords: "i VI III VII" or "Am F C G:2"')
    ap.add_argument("--form", help='"intro:4,verse:8,chorus:8,outro:2" (name:bars)')
    ap.add_argument("--instrument", help='"lead=flute,bass=bass_808"')
    ap.add_argument("--pattern", help='"bass=octave,keys=offbeat,arp=up16"')
    ap.add_argument("--groove", help='your own drums: "kick=X...X...X...X...,snare=....X.......X..." or none')
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
        if base.get("version", 1) < 2:
            die(f"{src} was written by the old style-based composer: compose it again with --feel")
        out = Path(a.out or src.with_name(src.name.replace(".score.json", ".wav")))
        if a.seed is not None and not a.regen:
            base["seed"] = a.seed
        if a.variations > 1:
            die("--variations makes new pieces: use it without --score (or --regen with --seed on a score)")
        apply_changes(base, a)
        output(base, out, a.stems)
        return
    if not a.feel or not a.duration:
        die("needs --feel and --duration (or --score, or --list)")
    if a.duration <= 0:
        die("--duration must be positive")
    if a.regen:
        die("--regen changes an existing piece: use it with --score")
    seed = a.seed if a.seed is not None else zlib.crc32(f"{Path.cwd().name}|{a.duration:.2f}".encode())
    out = Path(a.out or "02-work/music/music.wav")
    for i in range(a.variations):
        take = seed + i
        path = out if a.variations == 1 else out.with_name(f"{out.stem}_v{i + 1}{out.suffix}")
        score = build_score(a, take)
        apply_changes(score, a)
        output(copy.deepcopy(score), path, a.stems)


if __name__ == "__main__":
    sys.exit(main())
