# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Sound effects: synthesized (no licenses) or the user's, anchored to the edit.

    uv run sfx.py make                                     # whoosh pop click hit riser ding → 02-work/sfx/
    uv run sfx.py place --kind whoosh --cut 3              # on the cut before segment 3
    uv run sfx.py place --kind pop --line L5 --word ventas # when that word is said
    uv run sfx.py place --file mine.wav --line L7          # the user's own sound
    uv run sfx.py check | clear

Every sound is placed by its *event point*: the start for pop, click, hit and ding;
the peak for whoosh (its middle) and riser (its end) — so the sound lands on the
frame of the cut, the word or the graphic. Anchors are moments of the source (like
b-roll), so re-cutting doesn't break them. Each sound is leveled at placement to
~10 dB under the cleaned voice (--gain adjusts); render.py mixes them over the music.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _common import die, tc, tool  # noqa: E402
from _timeline import to_timeline, total  # noqa: E402

SR = 48000
TARGET_RMS_DB = -30.0      # cleaned voice sits at about -20: SFX ~10 dB under it
KINDS = ("whoosh", "pop", "click", "hit", "riser", "ding")


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


def synth(kind: str, seed: int = 7) -> tuple[np.ndarray, float]:
    """(stereo samples, event point in seconds)."""
    rng = np.random.default_rng(seed)
    if kind == "whoosh":
        n = int(0.7 * SR)
        t = np.linspace(-1, 1, n)
        body = _bandpass_sweep(rng.normal(0, 1, n), 300, 4000) * np.exp(-(t / 0.45) ** 2)
        pan = 0.5 + 0.4 * t                                  # passes left to right
        y, ev = np.stack([body * (1 - pan), body * pan], 1), 0.35
    elif kind == "pop":
        n = int(0.09 * SR)
        t = np.arange(n) / SR
        f = 900 * np.exp(-t * 35) + 250
        m = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 55)
        y, ev = np.stack([m, m], 1), 0.0
    elif kind == "click":
        n = int(0.025 * SR)
        t = np.arange(n) / SR
        m = (np.sin(2 * np.pi * 2800 * t) + 0.5 * rng.normal(0, 1, n)) * np.exp(-t * 300)
        y, ev = np.stack([m, m], 1), 0.0
    elif kind == "hit":
        n = int(0.6 * SR)
        t = np.arange(n) / SR
        f = 55 + 90 * np.exp(-t * 30)
        thump = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 7)
        snap = rng.normal(0, 1, n) * np.exp(-t * 60) * 0.35
        m = np.tanh(1.6 * (thump + snap))
        y, ev = np.stack([m, m], 1), 0.0
    elif kind == "riser":
        n = int(1.8 * SR)
        t = np.arange(n) / SR
        k = t / t[-1]
        tone = np.sin(2 * np.pi * np.cumsum(200 * 2 ** (2.5 * k)) / SR) * 0.35
        air = _bandpass_sweep(rng.normal(0, 1, n), 400, 7000) * 0.8
        m = (tone + air) * k ** 2 * _env(n, 0.01, 0.03)
        y, ev = np.stack([m, m * 0.95], 1), t[-1]
    elif kind == "ding":
        n = int(1.4 * SR)
        t = np.arange(n) / SR
        m = sum(a * np.sin(2 * np.pi * f * t) * np.exp(-t * d)
                for f, a, d in ((1318.5, 1.0, 3.2), (2637, 0.35, 5), (3955, 0.15, 8), (1976, 0.25, 4)))
        m *= _env(n, 0.002, 0.05)
        y, ev = np.stack([m, m], 1), 0.0
    else:
        die(f"unknown kind {kind} (one of {', '.join(KINDS)})")
    y = y / (np.abs(y).max() + 1e-9) * 0.9
    return y.astype(np.float32), float(ev)


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


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def cmd_make(args) -> None:
    out = Path(args.dir)
    out.mkdir(parents=True, exist_ok=True)
    index = {}
    for k in KINDS:
        y, ev = synth(k)
        write_wav(out / f"{k}.wav", y)
        index[k] = {"file": str(out / f"{k}.wav"), "event": ev, "dur": round(len(y) / SR, 3)}
        print(f"- {k}: {len(y) / SR:.2f} s, event at {ev:.2f} s")
    (out / "index.json").write_text(json.dumps(index, indent=1), encoding="utf-8")
    print(f"sounds → {out}")


def anchor_from(args, edl: dict) -> dict:
    if args.cut is not None:
        segs = edl["segments"]
        if not 1 <= args.cut < len(segs):
            die(f"--cut must be between 1 and {len(segs) - 1} (the cut before that segment, 0-based)")
        return {"name": segs[args.cut]["name"], "time": segs[args.cut]["in"]}
    tr = load(Path(args.ingest) / "transcript.json")
    from broll import norm
    line = int(args.line.lstrip("Ll"))
    sent = next((s for s in tr["sentences"] if s["line"] == line), None) or die(f"no line L{line}")
    words = [w for seg in tr["files"][sent["file"]]["segments"] for w in seg["words"]
             if sent["start"] - 1e-3 <= w["start"] and w["end"] <= sent["end"] + 1e-3]
    w = words[0] if not args.word else next((w for w in words if norm(w["word"]) == norm(args.word)), None)
    if w is None:
        die(f"'{args.word}' isn't in L{line}: {''.join(x['word'] for x in words).strip()}")
    return {"name": sent["file"], "time": w["start"]}


def cmd_place(args) -> None:
    edl_path = Path(args.edl)
    edl = load(edl_path)
    if args.file:
        src = Path(args.file).resolve()
        if not src.exists():
            die(f"{src} doesn't exist")
        event, label = args.event or 0.0, src.name
    else:
        idx_path = Path(args.dir) / "index.json"
        if not idx_path.exists():
            cmd_make(args)
        info = load(idx_path)[args.kind]
        src, event, label = Path(info["file"]).resolve(), info["event"], args.kind
    gain = level_db(read_audio(src)) + args.gain
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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ingest", default="01-ingest")
    ap.add_argument("--edl", default="02-work/edl.json")
    ap.add_argument("--dir", default="02-work/sfx", help="where synthesized sounds live")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("make")
    p = sub.add_parser("place")
    p.add_argument("--kind", choices=KINDS)
    p.add_argument("--file", help="the user's own sound instead of a synthesized one")
    p.add_argument("--event", type=float, help="with --file: second of the sound that lands on the anchor")
    p.add_argument("--line", help="transcript line, e.g. L5")
    p.add_argument("--word", help="word of that line (default: its first word)")
    p.add_argument("--cut", type=int, help="the cut before this segment (0-based index in edl.json)")
    p.add_argument("--offset", type=float, default=0.0, help="seconds relative to the anchor")
    p.add_argument("--gain", type=float, default=0.0, help="dB over the default level (~10 dB under the voice)")
    p.add_argument("--note")
    sub.add_parser("check")
    sub.add_parser("clear")
    args = ap.parse_args()
    if args.cmd == "place":
        if not (args.kind or args.file):
            die("place needs --kind or --file")
        if not (args.line or args.cut is not None):
            die("place needs --line (and optionally --word) or --cut")
    {"make": cmd_make, "place": cmd_place, "check": cmd_check, "clear": cmd_clear}[args.cmd](args)


if __name__ == "__main__":
    main()
