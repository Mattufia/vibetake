# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""The composer follows the rules of music, for any feel: cadences, chord tones, resolved dissonance,
singable leaps, led voices, and a piece that depends on the video instead of on a style.

    uv run tests/test_composition.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import _compose as cp  # noqa: E402
import _synth as sy  # noqa: E402
import musicgen as mg  # noqa: E402


def args(**kw):
    names = ("list", "score", "energy", "bpm", "key", "mode", "progression", "form", "instrument", "pattern", "groove",
             "kit", "swing", "hit", "ending", "mute", "solo", "gain", "reverb", "width", "bright", "warmth", "lofi",
             "sidechain", "regen", "seed", "out")
    return type("Args", (), {**dict.fromkeys(names), "no_melody": False, "stems": False, "variations": 1, **kw})


def feels(n: int):
    rng = np.random.default_rng(2026)
    for _ in range(n):
        yield ",".join(f"{k}={rng.random():.2f}" for k in cp.FEEL)


def audit(score: dict, tally: dict) -> None:
    key, mode = sy.pitch_class(score["key"]), score["mode"]
    last = score["sections"][-1]
    final = cp.parse_chord(cp.expand_chords(last["chords"], last["bars"])[-1], key, mode)
    tally["pieces"] += 1
    tally["final not tonic"] += final["root"] != key
    for s in score["sections"]:
        chords = [cp.parse_chord(c, key, mode) for c in cp.expand_chords(s["chords"], s["bars"])]
        tally["chord repeated across a change"] += sum(
            a != b and a.partition(":")[0] == b.partition(":")[0] for a, b in zip(s["chords"], s["chords"][1:]))
        notes = s.get("notes", {}).get("lead", [])
        for i, (beat, ln, m, _) in enumerate(notes):
            bar = int(beat // 4)
            tones = cp.chord_tones(chords[bar])
            tally["notes"] += 1
            tally["outside the chord's scale"] += m % 12 not in cp.chord_scale(chords[bar], key, mode)
            if beat % 2 == 0 or ln >= 1:
                tally["firm notes"] += 1
                tally["firm note off the chord"] += m % 12 not in tones
            tally["crosses into the next chord"] += int((beat + ln - 1e-6) // 4) != bar
            if i + 1 < len(notes):
                step = notes[i + 1][2] - m
                tally["intervals"] += 1
                tally["awkward leap"] += abs(step) > 7 or abs(step) == 6
                if m % 12 not in tones:
                    tally["passing notes"] += 1
                    tally["dissonance unresolved"] += abs(step) > 2


def main() -> None:
    checks = {}
    tally = dict.fromkeys(("pieces", "final not tonic", "chord repeated across a change", "notes", "firm notes",
                           "firm note off the chord", "outside the chord's scale", "crosses into the next chord",
                           "intervals", "awkward leap", "passing notes", "dissonance unresolved"), 0)
    motion, parallels, changes, bass_leaps, bass_moves, bass_widest = [], 0, 0, 0, 0, 0
    modes, tempos = set(), []
    for i, feel in enumerate(feels(240)):
        score = mg.build_score(args(feel=feel, duration=float(20 + i % 50)), 1000 + i)
        audit(score, tally)
        modes.add(score["mode"])
        tempos.append(score["bpm"])
        key, mode = sy.pitch_class(score["key"]), score["mode"]
        prev = None
        flat = [cp.parse_chord(c, key, mode) for s in score["sections"] for c in cp.expand_chords(s["chords"], s["bars"])]
        line = cp.bass_line(flat)
        bass_moves += len(line) - 1
        bass_leaps += int((np.abs(np.diff(line)) > 7).sum())
        bass_widest = max(bass_widest, int(np.abs(np.diff(line)).max(initial=0)))
        for chord in flat:
            v = cp.voice_lead(prev, chord, *cp.RANGES["pad"])
            if prev and len(prev) == len(v) and prev != v:
                changes += 1
                motion.append(np.mean([abs(x - y) for x, y in zip(prev, v)]))
                moved = (prev[0] - v[0]) * (prev[-1] - v[-1]) > 0
                parallels += moved and (prev[-1] - prev[0]) % 12 == (v[-1] - v[0]) % 12 in (0, 7)
            prev = v

    checks[f"every piece ends on its tonic ({tally['pieces']} pieces)"] = tally["final not tonic"] == 0
    checks["a change of chord is always a different chord"] = tally["chord repeated across a change"] == 0
    checks[f"strong beats and long notes are chord tones ({tally['firm notes']} notes, "
           f"{tally['firm note off the chord']} off)"] = tally["firm note off the chord"] == 0
    checks[f"no note outside the chord's scale ({tally['outside the chord\'s scale']})"] = \
        tally["outside the chord's scale"] == 0
    checks[f"a melody note never rings over the next chord ({tally['crosses into the next chord']})"] = \
        tally["crosses into the next chord"] == 0
    checks[f"dissonance resolves by step ({tally['passing notes']} passing, "
           f"{tally['dissonance unresolved']} not)"] = tally["dissonance unresolved"] == 0
    rate = tally["awkward leap"] / max(1, tally["intervals"])
    checks[f"singable: no tritones or leaps over a fifth ({rate:.2%})"] = rate < 0.005
    checks[f"voices move little ({np.mean(motion):.2f} semitones per voice)"] = float(np.mean(motion)) < 2.6
    checks[f"outer voices avoid parallel fifths and octaves ({parallels}/{changes})"] = parallels / max(1, changes) < 0.02
    checks[f"the bass stays within a fifth ({bass_leaps}/{bass_moves} wider, widest {bass_widest} semitones)"] = \
        bass_leaps / max(1, bass_moves) < 0.01 and bass_widest <= 12

    # Theory itself.
    checks["bVI in C minor is Ab, bVII in C major is Bb"] = cp.parse_chord("bVI", 0, "minor")["root"] == 8 \
        and cp.parse_chord("bVII", 0, "major")["root"] == 10
    checks["over V in A minor the melody has G#, not G"] = \
        {8} <= cp.chord_scale(cp.parse_chord("V", 9, "minor"), 9, "minor") \
        and 7 not in cp.chord_scale(cp.parse_chord("V", 9, "minor"), 9, "minor")
    checks["a period: question stays open, answer closes"] = (lambda c: c[3].startswith("V") and c[7].startswith("I"))(
        cp.section_chords("chorus", 8, True, "major", cp.parse_feel("drive=.6,color=0"),
                          {d: cp.symbol(d, "major", False, False) for d in range(7)}, np.random.default_rng(4)))
    checks["chords by name set the key (Am F C G is A minor)"] = cp.infer_key(["Am", "F", "C", "G"]) == ("A", "minor")

    # No styles: the piece comes from the feel.
    checks["no style presets left"] = not hasattr(mg, "MOODS") and not hasattr(mg, "GROOVES")
    def mode_light(feel):
        got = [cp.MODE_LIGHT[mg.build_score(args(feel=feel, duration=20.0), s)["mode"]] for s in range(30)]
        return float(np.mean(got))
    checks["light brightens the mode"] = mode_light("light=.1") + 0.3 < mode_light("light=.9")
    def tempo(feel):
        return float(np.mean([mg.build_score(args(feel=feel, duration=20.0), s)["bpm"] for s in range(20)]))
    checks["drive raises the tempo"] = tempo("drive=.1") + 30 < tempo("drive=.9")
    def inst(feel, part):
        return {mg.build_score(args(feel=feel, duration=20.0), s)["parts"][part]["instrument"] for s in range(20)}
    checks["organic chooses played instruments, electronic synths"] = "bass_upright" in inst("organic=1", "bass") \
        and not inst("organic=0", "bass") & {"bass_upright", "bass_electric"}
    still = mg.build_score(args(feel="drive=.05", duration=20.0), 1)
    checks["a still video gets no drums"] = still["groove"] == {}
    checks[f"the same video space reaches {len(modes)} modes and {min(tempos):.0f}–{max(tempos):.0f} bpm"] = \
        len(modes) >= 5 and max(tempos) - min(tempos) > 50

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"{sum(checks.values())}/{len(checks)} pass")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
