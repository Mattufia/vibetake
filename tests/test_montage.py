# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Montage to music (montage.py): moments from the picture, cuts on the beat.

    uv run tests/test_montage.py

Two clips with action at known moments (a square that moves only then) and a drum track at
120 BPM, quiet for 12 s, then loud. Checks: moments split at the shot change and long shots at
calm points, each with its action peak; sheets; every cut on a beat, frame-exact; shorter shots
where the track is loud; each shot shows its action; the render is as long as planned with the
music on the beat; a block before a spoken edit with its music following it.
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
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import _timeline as tl  # noqa: E402

FF = shutil.which("ffmpeg")
FPS = 30
BEAT = 0.5


def ff(cwd: Path, *a: str) -> None:
    subprocess.run([FF, "-hide_banner", "-v", "error", "-y", *a], cwd=cwd, check=True)


def uv(cwd: Path, *a: str) -> subprocess.CompletedProcess:
    return subprocess.run(["uv", "run", str(SCRIPTS / a[0]), *a[1:]], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def clip(tmp: Path, name: str, dur: float, scenes: list[tuple[float, float, str]], moves: list[tuple[float, float]]) -> None:
    """Backgrounds per scene, a white square that moves only during `moves`."""
    bg = "[0]" + ",".join(f"drawbox=c={c}:t=fill:enable='between(t,{a},{b})'" for a, b, c in scenes) + ","
    x = "+".join(f"clip((t-{a})/{b - a},0,1)*{240 // len(moves)}" for a, b in moves)
    ff(tmp, "-f", "lavfi", "-i", f"color=black:s=320x180:r={FPS}:d={dur}", "-f", "lavfi", "-i",
       f"color=white:s=30x30:r={FPS}:d={dur}", "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo",
       "-filter_complex", bg + f"null[bg];[bg][1]overlay=x='20+{x}':y=75,format=yuv420p[v]",
       "-map", "[v]", "-map", "2", "-t", str(dur), "-c:v", "libx264", "-preset", "ultrafast", "-crf", "16",
       "-c:a", "aac", f"00-raw/{name}")


def drums(path: Path, dur: float = 40.0, loud_from: float = 12.0) -> None:
    """Kick on every beat (accent on each bar's first), a hiss bed; quiet, then loud."""
    sr = 44100
    t = np.arange(int(dur * sr)) / sr
    x = 0.02 * np.random.default_rng(1).standard_normal(len(t))
    for k in range(int(dur / BEAT)):
        s = int(k * BEAT * sr)
        n = min(int(0.12 * sr), len(t) - s)
        hit = np.sin(2 * np.pi * 70 * np.arange(n) / sr) * np.exp(-np.arange(n) / (0.03 * sr))
        x[s:s + n] += (1.0 if k % 4 == 0 else 0.6) * hit
    x *= np.where(t < loud_from, 0.25, 1.0)
    x += np.where(t < loud_from, 0.0, 0.08) * np.random.default_rng(2).standard_normal(len(t))
    with wave.open(str(path), "wb") as f:
        f.setnchannels(1), f.setsampwidth(2), f.setframerate(sr)
        f.writeframes((np.clip(x * 0.7, -1, 1) * 32767).astype(np.int16).tobytes())


def main() -> None:
    checks = {}
    tmp = Path(tempfile.mkdtemp(prefix="vt-montage-"))
    for d in ("00-raw", "01-ingest", "02-work", "music"):
        (tmp / d).mkdir()
    clip(tmp, "a.mp4", 8, [(0, 4, "0x404040"), (4, 8, "0x206090")], [(2.0, 2.6), (6.0, 6.5)])
    clip(tmp, "b.mp4", 14, [(0, 14, "0x603020")], [(3.0, 3.5), (8.5, 9.0), (12.0, 12.4)])
    mat = [{"name": n, "file": f"00-raw/{n}", "kind": "video", "duration": d, "fps": FPS,
            "display_width": 320, "display_height": 180, "shots": {"shot_changes": sc}}
           for n, d, sc in (("a.mp4", 8.0, [4.0]), ("b.mp4", 14.0, []))]
    (tmp / "01-ingest" / "material.json").write_text(json.dumps({"files": mat}))
    drums(tmp / "music" / "drums.wav")

    r = uv(tmp, "montage.py", "moments", "--max", "6")
    if r.returncode:
        print(r.stdout, r.stderr)
    moms = json.loads((tmp / "02-work/montage/moments.json").read_text(encoding="utf-8"))["moments"]
    a = [m for m in moms if m["name"] == "a.mp4"]
    b = [m for m in moms if m["name"] == "b.mp4"]
    checks[f"moments: a.mp4 split at its shot change ({[(m['in'], m['out']) for m in a]})"] = (
        len(a) == 2 and abs(a[0]["out"] - 4.0) < 0.01)
    checks[f"moments: the 14 s shot in pieces of ≤6 s ({[(m['in'], m['out']) for m in b]})"] = (
        len(b) == 3 and all(m["out"] - m["in"] <= 6.0 + 1e-6 for m in b))
    checks[f"moments: cut at calm points, not through the action ({[m['out'] for m in b[:-1]]})"] = all(
        not (2.9 < m["out"] < 3.6 or 8.4 < m["out"] < 9.1) for m in b[:-1])
    truth = {("a.mp4", 0): 2.3, ("a.mp4", 1): 6.25}
    peaks = [m["peak"] for m in a]
    checks[f"moments: action peaks where the square moves ({peaks}, really 2.3 and 6.25)"] = all(
        abs(p - truth[("a.mp4", i)]) < 0.35 for i, p in enumerate(peaks))
    checks["moments: sheets and moments.md"] = (
        (tmp / "02-work/montage/sheet_01.jpg").exists() and "M1" in (tmp / "02-work/montage/moments.md").read_text(encoding="utf-8"))

    ids = [m["id"] for m in moms]
    order = ",".join(ids * 4)
    r = uv(tmp, "montage.py", "build", "--order", order, "--music", "music/drums.wav", "--in", "0.1")
    if r.returncode:
        print(r.stdout, r.stderr)
    edl = json.loads((tmp / "02-work/edl.json").read_text(encoding="utf-8"))
    segs = edl["segments"]
    beats = json.loads((tmp / "music/drums.beats.json").read_text())["beats"]
    m_in = edl["music"][0]["in"]
    t0 = tl.starts(segs, FPS) + [tl.total(segs, FPS)]
    cuts = t0[1:]
    off = [min(abs(c - (b - m_in)) for b in beats) for c in cuts]
    checks[f"build: every cut on a beat (worst {1000 * max(off):.0f} ms off, {len(cuts)} cuts)"] = max(off) <= 0.5 / FPS + 0.002
    checks[f"build: the music starts on a beat ({m_in:.3f} s)"] = min(abs(m_in - b) for b in beats) < 1e-3
    quiet = [s["out"] - s["in"] for s, t in zip(segs, t0) if t + m_in < 11]
    loud = [s["out"] - s["in"] for s, t in zip(segs, t0) if t + m_in > 13]
    checks[f"build: shorter shots where the track is loud ({np.mean(quiet):.2f} s quiet vs {np.mean(loud):.2f} s loud)"] = (
        bool(quiet) and bool(loud) and np.mean(loud) < 0.75 * np.mean(quiet))
    by_id = {m["id"]: m for m in moms}
    shows = [s["in"] - 1e-6 <= by_id[s["moment"]]["peak"] <= s["out"] + 1e-6 for s in segs]
    checks[f"build: each shot shows its moment's action peak ({sum(shows)}/{len(shows)})"] = all(shows)
    checks["build: shots muted (room tone, the music leads), music not a ducked bed"] = (
        all(s.get("audio") == "mute" for s in segs) and edl["music"][0]["block"] == "montage")

    r = uv(tmp, "render.py", "--out", "montage.mp4", "--no-grade")
    ok = r.returncode == 0
    if not ok:
        print(r.stdout[-2000:], r.stderr[-2000:])
    dur = float(json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
                                           str(tmp / "montage.mp4")], capture_output=True, text=True).stdout)
                ["format"]["duration"]) if ok else 0
    checks[f"render: {dur:.2f} s, as planned ({tl.total(segs, FPS):.2f} s)"] = ok and abs(dur - tl.total(segs, FPS)) < 0.05
    if ok:
        subprocess.run(["uv", "run", str(SCRIPTS / "audio.py"), "beats", "--file", str(tmp / "montage.mp4")],
                       cwd=tmp, capture_output=True)
        got = json.loads((tmp / "montage.beats.json").read_text())["beats"]
        want = [b - m_in for b in beats if m_in <= b <= m_in + dur - 0.3]
        err = np.median([min(abs(g - w) for g in got) for w in want])
        checks[f"render: the music on the beat in the export (median {1000 * err:.0f} ms)"] = err < 0.03

    # A block before a spoken edit: the music comes in with it and fades after it.
    spoken = {"version": 1, "output": {"width": 320, "height": 180, "fps": FPS},
              "segments": [{"source": "00-raw/b.mp4", "name": "b.mp4", "in": 0.0, "out": 3.0}]}
    (tmp / "02-work/edl.json").write_text(json.dumps(spoken))
    r = uv(tmp, "montage.py", "build", "--order", ",".join(ids[:3]), "--music", "music/drums.wav", "--where", "start")
    edl = json.loads((tmp / "02-work/edl.json").read_text(encoding="utf-8"))
    segs = edl["segments"]
    placed = tl.place_music(edl["music"], segs, FPS)[0]
    block_end = tl.starts(segs, FPS)[-1]
    checks[f"start block: shots first, the spoken edit after; music {placed['at']:.2f}–{placed['until']:.2f} s"] = (
        r.returncode == 0 and segs[-1].get("role") != "montage" and all(s.get("role") == "montage" for s in segs[:-1])
        and placed["at"] == 0 and abs(placed["until"] - (block_end + 1.5)) < 1e-6)
    r = uv(tmp, "montage.py", "clear")
    edl = json.loads((tmp / "02-work/edl.json").read_text(encoding="utf-8"))
    checks["clear: the block and its music out, the spoken edit stays"] = (
        len(edl["segments"]) == 1 and not edl.get("music") and "montage" not in edl)

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    n = sum(checks.values())
    print(f"{n}/{len(checks)} pass · {tmp}")
    if n == len(checks):
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if n == len(checks) else 1)


if __name__ == "__main__":
    main()
