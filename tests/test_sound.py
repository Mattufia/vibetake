# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""The sound generators: every mood, instrument and effect works; a piece can be changed part by part.

    uv run tests/test_sound.py
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
SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import _synth as sy  # noqa: E402
import musicgen as mg  # noqa: E402
import sfx  # noqa: E402


def gen(cwd: Path, *a: str, script: str = "musicgen.py") -> subprocess.CompletedProcess:
    return subprocess.run(["uv", "run", str(SCRIPTS / script), *a], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def read(path: Path) -> np.ndarray:
    with wave.open(str(path)) as w:
        return np.frombuffer(w.readframes(w.getnframes()), "<i2").reshape(-1, 2) / 32768


def rms(x: np.ndarray) -> float:
    return float(np.sqrt((x ** 2).mean()))


def pitch_of(y: np.ndarray) -> float:
    n = min(len(y), sy.SR)
    spec = np.abs(np.fft.rfft(y[:n] * np.hanning(n)))
    freqs = np.fft.rfftfreq(n, 1 / sy.SR)
    band = (freqs > 400) & (freqs < 480)                     # around the A4 asked for (a pluck's overtones can be louder)
    return float(freqs[band][np.argmax(spec[band])])


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-sound-"))
    checks = {}

    # Harmony.
    c = mg.parse_chord("vi", 0, "major")
    checks["chords: vi in C is A minor"] = c["root"] == 9 and c["intervals"] == [0, 3, 7]
    c = mg.parse_chord("bVII", 0, "major")
    checks["chords: bVII in C is Bb major"] = c["root"] == 10 and c["intervals"] == [0, 4, 7]
    c = mg.parse_chord("F#m7", 0, "major")
    checks["chords: names work (F#m7)"] = c["root"] == 6 and c["intervals"] == [0, 3, 7, 10]
    checks["chords: slash bass (C/E)"] = mg.parse_chord("C/E", 0, "major")["bass"] == 4
    checks["chords: :2 holds two bars"] = mg.expand_chords(["Am:2", "F"], 4) == ["Am", "Am", "F", "Am"]
    checks["notes: names and numbers"] = sy.note_number("C4") == 60 and sy.note_number("A4") == 69 and sy.note_number(61) == 61

    # Every instrument sounds, in tune where it has a clear pitch, and every drum sounds.
    bad = [k for k in sy.INSTRUMENTS if not 0.5 < np.abs(sy.note(k, 57, 0.6, 0.8)).max() <= 1.001]
    checks[f"all {len(sy.INSTRUMENTS)} instruments sound{(': ' + ', '.join(bad)) if bad else ''}"] = not bad
    off = {}
    for k in ("piano", "flute", "guitar", "harp", "marimba", "lead_soft"):
        got = pitch_of(sy.note(k, 69, 1.0, 0.7))
        off[k] = abs(1200 * np.log2(got / 440))
    checks[f"instruments in tune (worst {max(off.values()):.0f} cents)"] = max(off.values()) < 6
    bad = [f"{k}/{d}" for k in sy.KITS for d in sy.DRUMS
           if not np.isfinite(y := sy.drum(d, k, np.random.default_rng(1))).all() or np.abs(y).max() < 0.5]
    checks[f"all {len(sy.KITS)} kits × {len(sy.DRUMS)} drums sound"] = not bad

    # Every mood renders: exact length, no clipping, safe in mono, nothing silent.
    problems = []
    for mood in mg.MOODS:
        a = argparse_defaults(mood=mood, duration=6.0)
        score = mg.build_score(a, 5)
        mg.apply_changes(score, a)
        y, beats, bars, info, _ = mg.render(score)
        drop = 20 * np.log10(rms(y) / (rms(y.mean(1)) + 1e-9))
        if len(y) != 288000 or np.abs(y).max() > 0.9 or drop > 3 or rms(y) < 0.02 or not beats or not bars:
            problems.append(f"{mood} (len {len(y)}, peak {np.abs(y).max():.2f}, mono −{drop:.1f} dB)")
    checks[f"all {len(mg.MOODS)} moods render clean{(': ' + ', '.join(problems)) if problems else ''}"] = not problems

    # Direction: form, instruments, patterns, chords and hits.
    p = gen(tmp, "--mood", "upbeat", "--duration", "24", "--bpm", "120", "--seed", "2", "--form",
            "intro:2,verse:4,chorus:4,outro:2", "--instrument", "lead=flute,bass=bass_808", "--pattern", "bass=octave",
            "--progression", "Am F C G", "--out", "a.wav", "--stems")
    meta = json.loads((tmp / "a.beats.json").read_text()) if p.returncode == 0 else {}
    sc = json.loads((tmp / "a.score.json").read_text()) if p.returncode == 0 else {}
    checks[f"directed piece renders{'' if p.returncode == 0 else ': ' + p.stderr[-200:]}"] = p.returncode == 0
    checks["form: sections where they were asked"] = [(s["name"], s["start"]) for s in meta.get("sections", [])] == [
        ("intro", 0), ("verse", 4), ("chorus", 12), ("outro", 20)]
    checks["bars in beats.json (the montage reads them)"] = meta.get("bars", [])[:3] == [0, 2, 4]
    checks["instrument and pattern kept in the score"] = sc["parts"]["lead"]["instrument"] == "flute" \
        and sc["parts"]["bass"]["instrument"] == "bass_808" and sc["parts"]["bass"]["pattern"] == "octave"
    stems = {f.stem for f in (tmp / "a.stems").glob("*.wav")}
    checks[f"stems per part ({len(stems)})"] = {"pad", "bass", "drums", "lead"} <= stems
    base = read(tmp / "a.wav")
    gen(tmp, "--score", "a.score.json", "--out", "again.wav")
    checks["a score renders the same piece again"] = np.array_equal(base, read(tmp / "again.wav"))

    # Changing one thing leaves the rest alone.
    gen(tmp, "--score", "a.score.json", "--regen", "chorus:lead", "--out", "re.wav")
    before, after = json.loads((tmp / "a.score.json").read_text()), json.loads((tmp / "re.score.json").read_text())
    same = all(x["notes"].get("lead") == y["notes"].get("lead") for x, y in zip(before["sections"], after["sections"])
               if x["name"] != "chorus")
    changed = before["sections"][2]["notes"]["lead"] != after["sections"][2]["notes"]["lead"]
    checks["regen: rewrites that section's melody, only that one"] = same and changed
    gen(tmp, "--score", "a.score.json", "--mute", "drums", "--out", "nodrums.wav")
    checks["mute: the part is gone"] = not (tmp / "nodrums.stems").exists() and \
        json.loads((tmp / "nodrums.score.json").read_text())["parts"]["drums"]["mute"] and \
        not np.array_equal(base, read(tmp / "nodrums.wav"))
    original = json.loads((tmp / "a.score.json").read_text())
    new_key = mg.NOTES[(sy.pitch_class(original["key"]) + 2) % 12]
    gen(tmp, "--score", "a.score.json", "--key", new_key, "--out", "up.wav")
    up = json.loads((tmp / "up.score.json").read_text())
    was = [n[2] for n in original["sections"][2]["notes"]["lead"]]
    now = [n[2] for n in up["sections"][2]["notes"]["lead"]]
    checks["new key: the melody and named chords move with it"] = now == [m + 2 for m in was] and \
        up["sections"][2]["chords"][0] == "B" + "m" and original["sections"][2]["chords"][0] == "Am"
    edited = json.loads((tmp / "a.score.json").read_text())
    edited["sections"][1]["notes"]["lead"] = [[0, 2, "E5", 0.9], [2, 2, 79, 0.8]]
    (tmp / "edit.score.json").write_text(json.dumps(edited))
    p = gen(tmp, "--score", "edit.score.json", "--out", "edit.wav")
    checks["notes written by hand in the score play"] = p.returncode == 0 and not np.array_equal(base, read(tmp / "edit.wav"))
    edited["sections"][1]["notes"]["lead"] = [["x"]]
    (tmp / "bad.score.json").write_text(json.dumps(edited))
    p = gen(tmp, "--score", "bad.score.json", "--out", "bad.wav")
    checks["a broken note is reported, not a crash"] = p.returncode != 0 and "bad note" in p.stderr

    # Variations, hits on the edit.
    gen(tmp, "--mood", "calm", "--duration", "8", "--seed", "1", "--variations", "3", "--out", "v.wav")
    takes = [read(tmp / f"v_v{i}.wav") for i in (1, 2, 3)]
    checks["variations: three different takes"] = len(takes) == 3 and not np.array_equal(takes[0], takes[1]) \
        and not np.array_equal(takes[1], takes[2])
    p = gen(tmp, "--mood", "upbeat", "--duration", "20", "--seed", "9", "--hit", "8.1", "--out", "h.wav")
    bpm = json.loads((tmp / "h.beats.json").read_text())["bpm"]
    bar = 240 / bpm
    miss = min(8.1 % bar, bar - 8.1 % bar)
    checks[f"hit: the tempo adjusts so it lands on a bar ({miss * 1000:.0f} ms)"] = miss < 0.06

    # Clear errors.
    for args, name in ((["--mood", "calm", "--duration", "5", "--instrument", "lead=kazoo"], "unknown instrument"),
                       (["--mood", "calm", "--duration", "5", "--progression", "H Z"], "unreadable chord"),
                       (["--mood", "calm", "--duration", "5", "--energy", "0:loud"], "bad energy")):
        p = gen(tmp, *args, "--out", "err.wav")
        checks[f"{name} → a clear error"] = p.returncode != 0 and "ERROR" in p.stderr and "Traceback" not in p.stderr

    # Effects: all of them sound, can be shaped and stacked.
    bad = []
    for k in sfx.ALL_KINDS:
        y, ev = sfx.synth(k)
        if not np.isfinite(y).all() or np.abs(y).max() < 0.5 or not 0 <= ev <= len(y) / sfx.SR + 1e-3:
            bad.append(k)
    checks[f"all {len(sfx.ALL_KINDS)} effects sound, event inside{(': ' + ', '.join(bad)) if bad else ''}"] = not bad
    y, ev = sfx.synth("whoosh")
    low, ev2 = sfx.shape(y, ev, pitch=-12)
    checks["shape: an octave down lasts twice, event follows"] = abs(len(low) / len(y) - 2) < 0.01 and abs(ev2 - 2 * ev) < 0.01
    _, ev3 = sfx.shape(*sfx.synth("riser"), reverse=True)
    checks["shape: reversed riser has its event at the start"] = ev3 < 0.01
    wet, _ = sfx.shape(*sfx.synth("pop"), reverb=0.5)
    checks["shape: reverb leaves a tail"] = len(wet) > len(sfx.synth("pop")[0]) * 5
    long_rain, _ = sfx.synth("rain", dur=12)
    checks["ambience takes any length"] = abs(len(long_rain) / sfx.SR - 12) < 0.01
    stack, event = sfx.layered("riser,impact", 3, None)
    checks["layers line up on the event"] = abs(event - sfx.synth("riser", 3)[1]) < 0.01 and \
        np.abs(stack[int(event * sfx.SR):int((event + 0.05) * sfx.SR)]).max() > 0.2
    d = tmp / "fx"
    p = gen(tmp, "--dir", str(d), "design", "--kind", "whoosh", "--name", "slow", "--pitch", "-5", "--variants", "2",
            script="sfx.py")
    idx = json.loads((d / "index.json").read_text()) if p.returncode == 0 else {}
    checks["design: named variants saved"] = {"slow_1", "slow_2"} <= set(idx)
    gen(tmp, "--dir", str(d), "make", script="sfx.py")
    idx = json.loads((d / "index.json").read_text())
    checks["make keeps what was designed"] = "slow_1" in idx and "pop" in idx

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"{sum(checks.values())}/{len(checks)} pass")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


def argparse_defaults(**kw):
    """The flags musicgen would have with nothing else said."""
    names = ("list", "score", "energy", "bpm", "key", "mode", "progression", "form", "instrument", "pattern", "groove",
             "kit", "swing", "hit", "ending", "mute", "solo", "gain", "reverb", "width", "bright", "warmth", "lofi",
             "sidechain", "regen", "seed", "out")
    return type("Args", (), {**dict.fromkeys(names), "no_melody": False, "stems": False, "variations": 1, **kw})


if __name__ == "__main__":
    main()
