# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Edge cases of the time effects (from the review of the effects branch).

    uv run tests/test_effects_edges.py

A transition next to straight cuts, an L and a J eating one short segment from both ends, a fade
as long as its segment, a freeze at a segment's start, smooth slow motion that really
interpolates, J/L as played (first/last/transition), captions that follow the sound that is
heard, a pass that can't grow forever, ñ in bleeps, the last chapter's length.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import _timeline as tl  # noqa: E402
import render  # noqa: E402
from deliver import youtube  # noqa: E402
from subtitles import timeline_words  # noqa: E402

FF = shutil.which("ffmpeg")
FPS = 30


def ff(cwd: Path, *a: str) -> str:
    return subprocess.run([FF, "-hide_banner", "-y", *a], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True).stderr


def render_edl(tmp: Path, segs: list[dict], out: str, src: str = "src.mp4") -> subprocess.CompletedProcess:
    (tmp / "02-work").mkdir(exist_ok=True)
    (tmp / "02-work" / "edl.json").write_text(json.dumps(
        {"version": 1, "output": {"width": 320, "height": 180, "fps": FPS},
         "segments": [{"source": str(tmp / src), "name": src, **s} for s in segs]}))
    return subprocess.run(["uv", "run", str(SCRIPTS / "render.py"), "--out", out, "--no-grade"], cwd=tmp,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def lum(tmp: Path, f: str) -> list[float]:
    log = ff(tmp, "-i", f, "-vf", "signalstats,metadata=print:key=lavfi.signalstats.YAVG", "-f", "null", "-")
    return [float(v) for v in re.findall(r"YAVG=([\d.]+)", log)]


def durations(tmp: Path, f: str) -> tuple[float, float]:
    st = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(tmp / f)],
                                   capture_output=True, text=True, check=True).stdout)["streams"]
    return tuple(float(s["duration"]) for s in st)


def main() -> None:
    checks = {}
    tmp = Path(tempfile.mkdtemp(prefix="vt-edges-"))
    # Brightness = frame number (Y = 16 + N), a tone under it.
    ff(tmp, "-f", "lavfi", "-i", f"color=black:s=320x180:r={FPS}:d=7,format=yuv420p,geq=lum='16+N':cb=128:cr=128",
       "-f", "lavfi", "-i", "sine=f=440:sample_rate=48000:d=7",
       "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "src.mp4")

    # A dissolve into a lone segment after a run of straight cuts (two time bases meeting in xfade).
    segs = [{"in": 0.0, "out": 1.0}, {"in": 1.0, "out": 2.0},
            {"in": 3.0, "out": 4.0, "transition": {"type": "dissolve", "dur": 0.4}}, {"in": 4.0, "out": 5.0}]
    r = render_edl(tmp, segs, "tb.mp4")
    ok = r.returncode == 0
    vd, ad = durations(tmp, "tb.mp4") if ok else (0, 0)
    checks[f"transition next to straight cuts renders ({vd:.2f} s picture, {ad:.2f} s sound)"] = (
        ok and abs(vd - tl.total(segs, FPS)) < 1.5 / FPS and abs(ad - vd) < 0.03)

    # An L from the previous cut and a J from the next on a 1 s segment.
    segs = [{"in": 0.0, "out": 3.0, "l": 0.8}, {"in": 3.5, "out": 4.5}, {"in": 5.0, "out": 7.0, "j": 0.8}]
    cuts, _ = tl.jl_cuts(segs, FPS)
    checks[f"L + J on one short segment: it keeps ≥0.1 s of its sound ({cuts})"] = (
        cuts[1][1] + cuts[2][0] <= 1.0 - 0.1 + 1e-9)
    r = render_edl(tmp, segs, "lj.mp4")
    checks["L + J on one short segment renders"] = r.returncode == 0

    # A fade as long as its segment.
    r = render_edl(tmp, [{"in": 0.0, "out": 1.0}, {"in": 2.0, "out": 2.5, "fade_out": {"dur": 0.5}}], "fade.mp4")
    ys = lum(tmp, "fade.mp4") if r.returncode == 0 else []
    checks["fade out as long as the segment renders, ends black"] = r.returncode == 0 and ys and ys[-1] < 20

    # A freeze at the segment's start: the first frame held, then it plays.
    segs = [{"in": 2.0, "out": 3.0, "hold": [[0, 1.0]]}]
    r = render_edl(tmp, segs, "hold0.mp4")
    ys = lum(tmp, "hold0.mp4") if r.returncode == 0 else []
    first = 16 + 2.0 * FPS
    checks[f"freeze at 0: held at the start (frame 5 = {ys[5] - 16 if ys else '?':.0f}, wants {first - 16:.0f}), "
           f"plays after"] = bool(ys) and abs(ys[5] - first) < 3 and abs(ys[25] - first) < 3 and ys[50] > first + 10

    # Smooth slow motion: in-between frames (a square halfway between two positions), not copies.
    ff(tmp, "-f", "lavfi", "-i", f"color=black:s=320x180:r={FPS}:d=3", "-f", "lavfi", "-i",
       f"color=white:s=24x24:r={FPS}:d=3", "-filter_complex", "[0][1]overlay=x='20+t*90':y=78,format=yuv420p",
       "-c:v", "libx264", "-preset", "ultrafast", "-qp", "0", "move.mp4")
    r = render_edl(tmp, [{"in": 1.0, "out": 2.0, "speed": 0.5, "smooth": True}], "smooth.mp4", "move.mp4")
    md5 = subprocess.run([FF, "-v", "error", "-i", str(tmp / "smooth.mp4"), "-map", "0:v", "-f", "framemd5", "-"],
                         capture_output=True, text=True).stdout if r.returncode == 0 else ""
    sums = [ln.split(",")[-1].strip() for ln in md5.splitlines() if ln and not ln.startswith("#")]
    inner = sums[2:-2]
    unique = len(set(inner))
    checks[f"smooth ×0.5: {unique} different frames of {len(inner)} (copies would give half)"] = (
        bool(inner) and unique > 0.8 * len(inner))

    # J/L as played.
    segs = [{"in": 0, "out": 2, "j": 0.5}, {"in": 3, "out": 5, "transition": {"dur": 0.4}, "j": 0.3},
            {"in": 6, "out": 8, "l": 0.5}]
    cuts, warn = tl.jl_cuts(segs, FPS)
    checks["J/L: none on the first J, the last L, or a cut with a transition (and it says so)"] = (
        cuts == [(0.0, 0.0)] * 3 and len(warn) == 3)

    # Captions follow the sound: a J cut's words come in, the words it covers go.
    edl = {"output": {"fps": FPS}, "segments": [
        {"name": "a", "in": 10, "out": 20}, {"name": "b", "in": 50, "out": 60, "j": 1.0}]}
    tr = {"files": {"a": {"segments": [{"words": [{"word": " alfa", "start": 18.0, "end": 18.4},
                                                  {"word": " beta", "start": 19.5, "end": 19.8}]}]},
                    "b": {"segments": [{"words": [{"word": " gamma", "start": 49.2, "end": 49.6},
                                                  {"word": " delta", "start": 51.0, "end": 51.3}]}]}}}
    ws = timeline_words(edl, tr)
    checks[f"captions with a J cut: {[w['text'] for w in ws]} at {[w['t'] for w in ws]}"] = (
        [w["text"] for w in ws] == ["alfa", "gamma", "delta"] and ws == sorted(ws, key=lambda w: w["t"])
        and abs(ws[1]["t"] - 9.2) < 0.01)
    checks["anchors follow the same J: a word heard early lands under the previous picture"] = (
        abs(tl.to_timeline(edl["segments"], FPS, "b", 49.2) - 9.2) < 0.01)

    # One pass never grows past twice its limits, even with a transition on every cut.
    segs = [{"in": 0, "out": 1, "transition": {"dur": 0.2}} for _ in range(120)]
    render.edges(segs, FPS)
    groups = render.chunks(segs)
    checks[f"a dissolve on every cut: {len(groups)} passes of ≤{max(len(g) for g in groups)} segments"] = (
        len(groups) > 1 and max(len(g) for g in groups) <= 2 * render.CHUNK
        and all(abs(sum(s["_alead"] + s["_atail"] for s in g) - sum(s["_pre"] + s["_post"] for s in g)) < 1e-9
                for g in groups) and not any(g[0]["_tr"] for g in groups[1:]))

    # Bleeps: ñ is its own letter; a lone * bleeps nothing.
    checks["bleep: 'ano' doesn't catch 'año', 'ano*' doesn't catch 'años', '*' alone catches nothing"] = (
        not tl.bleeped("año", ["ano"]) and not tl.bleeped("Años", ["ano*"]) and tl.bleeped("anos", ["ano*"])
        and not tl.bleeped("hola", ["*"]) and tl.bleeped("Coño!", ["coño"]))

    # Chapters: the last one needs its 10 s too.
    _, warn = youtube([(0, "a"), (30, "b"), (60, "c")], total=65)
    checks["chapters: a last chapter of 5 s is flagged"] = any("'c' lasts" in w for w in warn)

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    n = sum(checks.values())
    print(f"{n}/{len(checks)} pass · {tmp}")
    if n == len(checks):
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if n == len(checks) else 1)


if __name__ == "__main__":
    main()
