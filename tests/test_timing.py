# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Time effects in render.py: speed, ramps, freezes, reversal, boomerang, J/L cuts, transitions.

    uv run tests/test_timing.py

A source whose brightness is its frame number (Y = 16 + N) and a beep at every k + 0.5 s. Each
rendered frame says which source frame it shows, so every segment's time map is checked frame by
frame against _timeline's; the beeps check that kept sound stays in sync (tempo changed) and that
a J cut moves the sound's cut and not the picture's.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
FIX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(SCRIPTS))
import _timeline as tl  # noqa: E402
import render  # noqa: E402

FF = shutil.which("ffmpeg")
FPS = 30


def ff(cwd: Path, *a: str) -> str:
    return subprocess.run([FF, "-hide_banner", "-y", *a], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True).stderr


def render_edl(tmp: Path, segs: list[dict], out: str) -> str:
    (tmp / "02-work").mkdir(exist_ok=True)
    (tmp / "02-work" / "edl.json").write_text(json.dumps(
        {"version": 1, "output": {"width": 320, "height": 180, "fps": FPS},
         "segments": [{"source": str(tmp / "src.mp4"), "name": "src.mp4", **s} for s in segs]}))
    return subprocess.run(["uv", "run", str(SCRIPTS / "render.py"), "--out", out, "--no-grade"], cwd=tmp,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", check=True).stdout


def lum(tmp: Path, f: str) -> list[float]:
    log = ff(tmp, "-i", f, "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-")
    return [float(v) for v in re.findall(r"YAVG=([\d.]+)", log)]


def beeps(tmp: Path, f: str) -> list[float]:
    raw = subprocess.run([FF, "-v", "error", "-i", str(tmp / f), "-ac", "1", "-ar", "48000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    env = np.abs(x[:len(x) // 48 * 48]).reshape(-1, 48).mean(1)            # 1 ms
    return [i / 1000 for i in range(1, len(env)) if env[i] > 0.03 and env[i - 1] <= 0.03]


def durations(tmp: Path, f: str) -> tuple[float, float]:
    st = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(tmp / f)],
                                   capture_output=True, text=True, check=True).stdout)["streams"]
    return tuple(float(s["duration"]) for s in st)


def main() -> None:
    checks = {}
    # The time map itself.
    ramp = {"in": 0, "out": 2, "speed": [[0, 1], [2, 2]]}
    hold = {"in": 0, "out": 1, "hold": [[0.5, 1.0], [1.0, 0.5]]}
    boom = {"in": 0, "out": 1, "boomerang": True, "speed": 2}
    rev = {"in": 2, "out": 3, "reverse": True}
    checks["time map: speed 2 halves, ramp 1→2 = 2·ln2, freezes add, boomerang doubles"] = (
        abs(tl.seg_time({"in": 0, "out": 2, "speed": 2}) - 1) < 1e-9 and abs(tl.seg_time(ramp) - 2 * np.log(2)) < 1e-9
        and abs(tl.seg_time(hold) - 2.5) < 1e-9 and abs(tl.seg_time(boom) - 1) < 1e-9)
    checks["time map: source → segment → source round-trips (ramp, freeze, reversal)"] = all(
        abs(tl.seg_to_src(s, tl.src_to_seg(s, t) + 1e-6) - t) < 1e-3
        for s, ts in ((ramp, (0.3, 1.1, 1.9)), (hold, (0.2, 0.7)), (rev, (2.2, 2.8))) for t in ts)
    checks["audio: a straight segment keeps its sound up to ×2.5, effects go quiet"] = (
        tl.audio_mode({"speed": 2}) == "keep" and tl.audio_mode({"speed": 4}) == "mute"
        and tl.audio_mode(rev) == "mute" and tl.audio_mode({"speed": 4, "audio": "pitch"}) == "pitch")
    a, b = {"in": 0, "out": 70}, {"in": 0, "out": 70, "_tr": ("fade", 0.4)}
    checks["chunks: never split across a transition"] = len(render.chunks([a, b])) == 1 and len(render.chunks([a, a])) == 2

    tmp = Path(tempfile.mkdtemp(prefix="vt-timing-"))
    ff(tmp, "-f", "lavfi", "-i", f"color=black:s=320x180:r={FPS}:d=7,format=yuv420p,geq=lum='16+N':cb=128:cr=128",
       "-f", "lavfi", "-i", "aevalsrc='0.5*sin(2*PI*1000*t)*lt(mod(t-0.5+10,1),0.066)':s=48000:d=7",
       "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "src.mp4")

    # 1. Picture: every effect in one edit, and a dissolve into a far part of the source.
    segs = [{"in": 0.0, "out": 2.0, "speed": 2},
            {"in": 2.0, "out": 3.0, "reverse": True},
            {"in": 3.0, "out": 4.0, "hold": [[0.5, 1.0]]},
            {"in": 4.0, "out": 6.0, "speed": [[0, 1], [2, 2]]},
            {"in": 0.2, "out": 1.2, "transition": {"type": "dissolve", "dur": 0.4}},
            {"in": 5.0, "out": 5.5, "boomerang": True}]
    render_edl(tmp, segs, "fx.mp4")
    ys = lum(tmp, "fx.mp4")
    t0 = tl.starts(segs, FPS)
    errs = {}
    for k, s in enumerate(segs):
        n = tl.seg_frames(s, FPS)
        lo, hi = (8 if k in (4,) else 2), n - (8 if k == 3 else 2)      # skip the dissolve and the edges
        for i in range(lo, hi):
            want = tl.seg_to_src(s, (i + 0.5) / FPS) * FPS
            errs[k] = max(errs.get(k, 0), abs((ys[round(t0[k] * FPS) + i] - 16) - want))
    names = ["×2", "reverse", "freeze", "ramp", "after a dissolve", "boomerang"]
    for k, e in errs.items():
        checks[f"{names[k]}: each frame is the source frame the time map says (worst {e:.1f} frames off)"] = e <= 2.0
    cut = round(t0[4] * FPS)
    mid = ys[cut] - 16
    checks[f"dissolve: the cut frame is a blend of both sides ({ys[cut - 7] - 16:.0f} → {mid:.0f} → {ys[cut + 7] - 16:.0f})"] = (
        ys[cut + 7] + 20 < ys[cut] < ys[cut - 7] - 20)
    freeze = ys[round((t0[2] + 0.5) * FPS) + 2: round((t0[2] + 1.5) * FPS) - 2]
    checks["freeze: one frame held for its second"] = max(freeze) - min(freeze) < 1
    vd, ad = durations(tmp, "fx.mp4")
    want = tl.total(segs, FPS)
    checks[f"length: picture {vd:.3f} = sound {ad:.3f} = time map {want:.3f} (the dissolve takes nothing)"] = (
        abs(vd - want) < 1.5 / FPS and abs(ad - want) < 0.03)
    got = beeps(tmp, "fx.mp4")
    wanted = [0.25, 0.75, t0[4] + 0.3]          # ×2 keeps its sound (faster); the effects are quiet
    checks[f"sound: ×2 and the straight segment beep where they should ({[round(g, 3) for g in got]})"] = (
        len(got) == len(wanted) and all(abs(g - w) < 1.5 / FPS for g, w in zip(got, wanted)))

    # 2. J cut: the next segment's sound comes in 0.6 s before its picture.
    segs = [{"in": 0.0, "out": 1.8}, {"in": 3.0, "out": 5.0, "j": 0.6, "fade_out": {"dur": 0.5}}]
    render_edl(tmp, segs, "j.mp4")
    got, ys = beeps(tmp, "j.mp4"), lum(tmp, "j.mp4")
    wanted = [0.5, 1.3, 2.3, 3.3]               # 2.5 s of the source heard at 1.3, under the first picture
    checks[f"J cut: sound early ({[round(g, 3) for g in got]}), picture cut stays at 1.8 s"] = (
        len(got) == 4 and all(abs(g - w) < 1.5 / FPS for g, w in zip(got, wanted))
        and ys[53] - 16 < 60 and ys[55] - 16 > 85)
    checks[f"fade out: to black on the last frame (Y {ys[-1]:.0f}), halfway at half ({ys[-8]:.0f})"] = (
        ys[-1] < 20 and 40 < ys[-8] < ys[-16] - 20)

    # 3. A re-cut keeps a segment's effects when it still covers the same piece of the material.
    proj = tmp / "proj"
    (proj / "01-ingest").mkdir(parents=True)
    shutil.copy(FIX / "retakes_es.transcript.json", proj / "01-ingest" / "transcript.json")
    (proj / "01-ingest" / "material.json").write_text(json.dumps({"files": [
        {"name": "retakes_es.wav", "file": str(FIX / "retakes_es.wav"), "kind": "audio"}]}))
    cut = lambda *a: subprocess.run(["uv", "run", str(SCRIPTS / "cutlist.py"), *a], cwd=proj, check=True,  # noqa: E731
                                    capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    cut("--keep", "auto")
    edl_p = proj / "02-work" / "edl.json"
    edl = json.loads(edl_p.read_text(encoding="utf-8"))
    edl["segments"][1].update(speed=0.5, transition={"type": "fadeblack", "dur": 0.3})
    edl_p.write_text(json.dumps(edl), encoding="utf-8")
    cut("--pace", "tight")
    again = json.loads(edl_p.read_text(encoding="utf-8"))["segments"]
    checks["re-cut: the segment keeps its speed and transition, the others stay plain"] = (
        again[1].get("speed") == 0.5 and again[1]["transition"]["type"] == "fadeblack"
        and not any("speed" in s for i, s in enumerate(again) if i != 1))

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
