# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Render an edit decision list from the original files.

    uv run render.py [02-work/edl.json] [--out <file>] [--overwrite] [--preview] [--no-grade]

The master goes to 03-delivery/<name>_v<N>.mp4 — a new version on every render, so
each round of changes stays (<name>: edl.json's "name" or the project folder).
--overwrite replaces the latest version instead (a fix before it was delivered).
--variant hookB renders another edit (its own edl) as <name>_v<N>_hookB.mp4, next to
the main video of the same version (an alternative hook, a 4:5 cut…).

Per segment: seek into the original, color (HDR pre-filter + the file's LUT from
02-work/color/grade.json), frame to the output size, constant frame rate, and a
12 ms audio fade at each edge so cuts never click. Segments are joined in one pass
(over 2 min, in chunks of about a minute). Every segment is cut to a whole number of frames for
both picture and sound, so sync never drifts.

Master: hardware encoder at constant quality with its best preset when it works,
x264 slow/CRF 17 otherwise. --preview: small and fast, for checking the edit; then
joins.py lays out every join (02-work/joins/).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _common import (DELIVERY, Log, deliverable_name, die, ffmpeg_progress, ffprobe, filter_path, run, tc,  # noqa: E402
                     tool, versions)
from _common import channel_pan, material_index, pre_filter, work_paths  # noqa: E402
from _timeline import (audio_mode, chapters, is_simple, jl_cuts, pieces, place_music, play_len,  # noqa: E402
                       resolve_broll, seg_time, starts, transition_dur,
                       signature as timeline_sig, total as timeline_total)

MATERIAL: dict = {}      # material.json by file name: HDR material without a grade still gets tone-mapped
from audio import BUS, TARGETS, master, music_graph  # noqa: E402
from sfx import resolve as resolve_sfx, sfx_graph  # noqa: E402

FADE_S = 0.012
CHUNK = 40          # most segments in one pass (one decoder each)
CHUNK_S = 60.0      # most seconds of edit in one pass: long takes split too, not only many cuts
ONE_PASS_S = 120.0  # an edit up to this long (and ≤ CHUNK segments) renders in a single pass


def chunks(segs: list[dict]) -> list[list[dict]]:
    """Consecutive segments in groups of ≤ CHUNK segments and ≤ CHUNK_S seconds (a single
    segment longer than that is a group of its own)."""
    if len(segs) <= CHUNK and sum(seg_time(s) for s in segs) <= ONE_PASS_S:
        return [segs]
    groups, cur, length = [], [], 0.0
    for k, s in enumerate(segs):
        d = seg_time(s)
        full = cur and (len(cur) >= CHUNK or length + d > CHUNK_S)
        # A transition or a J/L cut joins two segments in one graph: split elsewhere if possible,
        # but never let a pass hold more than twice CHUNK segments (decoders; a dissolve on every
        # cut, J/L everywhere): then that one cut becomes a straight cut. Long takes don't count.
        joined = s.get("_tr") or s.get("_delta")
        if full and joined and len(cur) >= 2 * CHUNK:
            prev = cur[-1]
            print(f"WARNING: a long run of transitions/J-L cuts: the cut into segment {k} is played "
                  "straight so the render can split there")
            prev["_atail"] -= prev["_post"] + s["_delta"]
            s["_alead"] -= s["_pre"] - s["_delta"]
            s.update(_tr=None, _pre=0.0, _delta=0.0)
            prev["_post"] = 0.0
            joined = False
        if full and not joined:
            groups.append(cur)
            cur, length = [], 0.0
        cur.append(s)
        length += d
    return groups + [cur]
MASTER = [
    ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "18", "-b:v", "0",
                    "-profile:v", "high"]),
    ("h264_videotoolbox", ["-c:v", "h264_videotoolbox", "-q:v", "70", "-profile:v", "high"]),
    ("h264_qsv", ["-c:v", "h264_qsv", "-global_quality", "18", "-profile:v", "high"]),
    ("h264_amf", ["-c:v", "h264_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "18", "-qp_p", "18"]),
    ("libx264", ["-c:v", "libx264", "-preset", "slow", "-crf", "17", "-profile:v", "high"]),
]
PREVIEW = [
    ("h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p1", "-cq", "28"]),
    ("h264_videotoolbox", ["-c:v", "h264_videotoolbox", "-q:v", "45"]),
    ("libx264", ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "28"]),
]
HWDEC = ["-hwaccel", "auto"]   # decode on the GPU when there is one; falls back to the CPU
HWDEC_MAX = 16   # ponytail: each 4K HEVC decoder holds ~165 MB of VRAM; past this, a chunk decodes on the CPU
INTERMEDIATE = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "8", "-pix_fmt", "yuv420p"]
TAGS = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]


def pick(candidates: list) -> tuple[str, list[str]]:
    """First encoder that really works on this machine (a GPU doesn't guarantee its encoder does)."""
    for name, a in candidates:
        p = run([tool("ffmpeg"), "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:d=0.5",
                 *a, "-pix_fmt", "yuv420p", "-f", "null", "-"], check=False)
        if p.returncode == 0:
            return name, a
    return candidates[-1]


def frames(t: float, fps: float) -> int:
    return max(1, round(t * fps))


def display_dims(probe: dict) -> tuple[int, int]:
    v = next(s for s in probe["streams"] if s.get("codec_type") == "video")
    rot = next((int(round(float(sd["rotation"]))) for sd in v.get("side_data_list", []) or [] if "rotation" in sd), 0)
    return (v["height"], v["width"]) if abs(rot) % 180 == 90 else (v["width"], v["height"])


_DYNAMIC_CROP: bool | None = None


def dynamic_crop_ok() -> bool:
    """Whether this ffmpeg accepts a crop whose size changes mid-stream (impact zoom and drift).
    Older builds refuse it; then the zoom stays at its base value."""
    global _DYNAMIC_CROP
    if _DYNAMIC_CROP is None:
        with tempfile.TemporaryDirectory() as tmp:
            cmd = Path(tmp) / "c.cmd"
            cmd.write_text("0.1 crop@t w 300, crop@t h 300;\n", encoding="utf-8")
            p = run([tool("ffmpeg"), "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=640x360:d=0.3",
                     "-vf", f"sendcmd=f='{filter_path(cmd)}',crop@t=w=320:h=320,scale=180:180", "-f", "null", "-"],
                    check=False)
            _DYNAMIC_CROP = p.returncode == 0
    return _DYNAMIC_CROP


BUMP_S = 0.15        # the impact zoom settles in this time


def zoom_at(t: float, dur: float, bump: float, drift: float) -> float:
    """Extra zoom over the base: a short impact on entry (eases out) times a slow push-in."""
    impact = bump * (1 - min(t / BUMP_S, 1)) ** 3 if bump else 0.0
    return (1 + impact) * (1 + drift * min(t / dur, 1) if dur > 0 else 1)


def framing(k: int, seg: dict, dur: float, fps: float, workdir: Path, lead: float = 0.0) -> str:
    """Crop for the segment from reframe.py — fixed or the operator path, with the impact
    zoom and drift — sent frame by frame to the crop filter; or a plain center crop to the
    output aspect when there's no framing."""
    fr = seg.get("frame")
    if not fr:
        return ""
    sw, sh = display_dims(seg["_probe"])
    bw, bh = fr["crop"][0] * sw, fr["crop"][1] * sh
    keys = fr["keys"] or [[0, 0.5, 0.5]]
    kt = [k_[0] for k_ in keys]
    bump, drift = fr.get("bump", 0.0), fr.get("drift", 0.0)
    sizing = bool(bump or drift) and dynamic_crop_ok()
    lines, first = [], None
    span = seg["out"] - seg["in"]          # keys, bump and drift are in source time from the cut
    for i in range(frames(dur, fps) + 1):
        t = i / fps - lead                  # the input starts `lead` seconds before the cut (a handle)
        z = zoom_at(max(t, 0), span, bump, drift) if sizing else 1.0
        cw, ch = max(2, round(bw / z / 2) * 2), max(2, round(bh / z / 2) * 2)
        cw, ch = min(cw, sw // 2 * 2), min(ch, sh // 2 * 2)
        cx = float(np.interp(t, kt, [k_[1] for k_ in keys]))
        cy = float(np.interp(t, kt, [k_[2] for k_ in keys]))
        x = int(min(max(cx * sw - cw / 2, 0), sw - cw))
        y = int(min(max(cy * sh - ch / 2, 0), sh - ch))
        if first is None:
            first = (cw, ch, x, y)
        size = f"crop@rf{k} w {cw}, crop@rf{k} h {ch}, " if sizing else ""
        lines.append(f"{i / fps:.4f} {size}crop@rf{k} x {x}, crop@rf{k} y {y};")
    cmd = workdir / f"frame_{k:03d}.cmd"
    cmd.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cw, ch, x0, y0 = first
    return f"sendcmd=f='{filter_path(cmd)}',crop@rf{k}=w={cw}:h={ch}:x={x0}:y={y0}"


def remap(seg: dict) -> str:
    """setpts expression: the played material's time T → the segment's time, through the speed
    curve (exact integral of a linear ramp) plus the freezes before it."""
    terms = []
    for s0, s1, v0, v1, t0 in pieces(seg):
        if abs(v1 - v0) < 1e-9 or s1 == math.inf:
            f = f"{t0:.6f}+(T-{s0:.6f})/{v0:.6f}"
        else:
            k = (v1 - v0) / (s1 - s0)
            f = f"{t0:.6f}+log(({v0:.6f}+{k:.6f}*(T-{s0:.6f}))/{v0:.6f})/{k:.6f}"
        terms.append((s1, f))
    expr = terms[-1][1]
    for s1, f in reversed(terms[:-1]):
        expr = f"if(lt(T,{s1:.6f}),{f},{expr})"
    holds = "".join(f"+{d:.6f}*gte(T,{a:.6f})" for a, d in seg.get("hold", []))
    return f"({expr}{holds})/TB"


def atempo(v: float) -> str:
    """Tempo change without pitch change: atempo steps of at most ×2 (or ÷2) each."""
    out = []
    while v > 2:
        out.append("atempo=2")
        v /= 2
    while v < 0.5:
        out.append("atempo=0.5")
        v /= 0.5
    return ",".join(out + [f"atempo={v:.6f}"])


def pitch(semitones: float) -> str:
    r = 2 ** (semitones / 12)
    return f"asetrate={48000 * r:.1f},aresample=48000,{atempo(1 / r)}"


VOICE = {        # "voice" of a segment; anything else is taken as an ffmpeg audio filter chain
    "phone": "highpass=f=300,lowpass=f=3400,acrusher=bits=10:mix=0.3,volume=1.5",
    "radio": "highpass=f=200,lowpass=f=5000,acompressor=threshold=0.1:ratio=6:makeup=2",
    "megaphone": "highpass=f=500,lowpass=f=4000,acrusher=bits=6:mix=0.4,volume=1.4",
    "echo": "aecho=0.8:0.7:180|360:0.4|0.2",
    "hall": "aecho=0.8:0.85:40|70|110|160:0.35|0.3|0.25|0.2",
    "robot": "afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)':win_size=512:overlap=0.75",
    "deep": pitch(-4),
    "high": pitch(5),
    "whisper": "afftfilt=real='hypot(re,im)*cos((random(0)*2-1)*2*3.14)':imag='hypot(re,im)*sin((random(1)*2-1)*2*3.14)':win_size=128:overlap=0.8",
}


_FILTERS: set | None = None


def has_filter(name: str) -> bool:
    global _FILTERS
    if _FILTERS is None:
        txt = run([tool("ffmpeg"), "-hide_banner", "-filters"], check=False).stdout
        _FILTERS = {ln.split()[1] for ln in txt.splitlines() if len(ln.split()) > 2}
    return name in _FILTERS


def redact(k: int, seg: dict, lead: float, fps: float, workdir: Path) -> list[str]:
    """Blurred or pixelated boxes on the source picture (redact.py tracks them: keys
    [t source, x, y, w, h] normalized), before stabilizing and framing so they move with it."""
    sw, sh = display_dims(seg["_probe"])
    out = []
    for j, r in enumerate(seg.get("redact", [])):
        keys = r.get("keys") or [[r.get("from", seg["in"]), *r["box"]], [r.get("to", seg["out"]), *r["box"]]]
        pad = 1 + float(r.get("pad", 0.2))
        bw = min(sw // 2 * 2, max(8, round(max(q[3] for q in keys) * sw * pad / 2) * 2))
        bh = min(sh // 2 * 2, max(8, round(max(q[4] for q in keys) * sh * pad / 2) * 2))
        t = [q[0] - seg["in"] + lead for q in keys]         # the input starts `lead` before the cut
        n = f"{k}_{j}"

        def at(tt: float) -> tuple[int, int]:
            cx = float(np.interp(tt, t, [q[1] + q[3] / 2 for q in keys])) * sw
            cy = float(np.interp(tt, t, [q[2] + q[4] / 2 for q in keys])) * sh
            return int(min(max(cx - bw / 2, 0), sw - bw)), int(min(max(cy - bh / 2, 0), sh - bh))
        x0, y0 = at(t[0])
        send = ""
        if len({(q[1], q[2]) for q in keys}) > 1:
            lines = [f"{i / fps:.4f} crop@rd{n} x {x}, crop@rd{n} y {y}, overlay@rd{n} x {x}, overlay@rd{n} y {y};"
                     for i in range(max(0, math.floor(t[0] * fps)), math.ceil(t[-1] * fps) + 1)
                     for x, y in [at(i / fps)]]
            cmd = workdir / f"redact_{n}.cmd"
            cmd.write_text("\n".join(lines) + "\n", encoding="utf-8")
            send = f"sendcmd=f='{filter_path(cmd)}',"
        m = min(bw, bh)
        fx = (f"scale={max(1, bw * 10 // m)}:{max(1, bh * 10 // m)}:flags=area,scale={bw}:{bh}:flags=neighbor"
              if r.get("mode") == "pixelate" else f"boxblur={max(2, m // 6)}:3")
        # A tracked box covers the whole segment (past its keys it stays at the edge ones: a re-cut
        # that lengthens the segment must never uncover what it hides); a from/to box only its span.
        when = "" if r.get("keys") else f":enable='between(t,{t[0]:.4f},{t[-1]:.4f})'"
        out.append(f"{send}split[rda{n}][rdb{n}];[rdb{n}]crop@rd{n}=w={bw}:h={bh}:x={x0}:y={y0},{fx}[rdc{n}];"
                   f"[rda{n}][rdc{n}]overlay@rd{n}=x={x0}:y={y0}{when}")
    return out


def fit(k: int, how: str, W: int, H: int) -> str:
    """The whole picture inside the output: over a blurred, darker copy of itself ("blur") or bars of a color."""
    inside = f"scale={W}:{H}:force_original_aspect_ratio=decrease:force_divisible_by=2"
    if how == "blur":
        return (f"split[fta{k}][ftb{k}];[fta{k}]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                f"boxblur={max(2, min(W, H) // 25)}:2,eq=brightness=-0.08[ftc{k}];[ftb{k}]{inside}[ftd{k}];"
                f"[ftc{k}][ftd{k}]overlay=(W-w)/2:(H-h)/2")
    return f"{inside},pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color={how}"


def chroma(k: int, key: dict, W: int, H: int, fps: float) -> str:
    """Green/blue screen out, over a picture, a video (looped) or a color."""
    c = key.get("color", "green")
    col = {"green": "0x00ff00", "blue": "0x0000ff"}.get(c, c)
    bg = str(key.get("bg", "black"))
    p = Path(bg)
    if p.exists():
        src = f"movie=filename='{filter_path(p.resolve())}'"
        src += (f",loop=loop=-1:size=1,setpts=N/{fps}/TB" if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".bmp")
                else ":loop=0,setpts=N/FRAME_RATE/TB")
    else:
        src = f"color=c={bg}:s={W}x{H}:r={fps}"
    spill = f",despill=type={c}" if c in ("green", "blue") else ""
    return (f"chromakey={col}:{float(key.get('similarity', 0.15))}:{float(key.get('blend', 0.05))}{spill}[kf{k}];"
            f"{src},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={fps}[kb{k}];"
            f"[kb{k}][kf{k}]overlay=shortest=1")


def look(lk: dict) -> str:
    """The whole edit's look: black and white, vignette, film grain (0–1 each)."""
    f = []
    if lk.get("bw"):
        f.append("hue=s=0")
    if lk.get("vignette"):
        f.append(f"vignette=angle={0.2 + 0.5 * float(lk['vignette']):.3f}")
    if lk.get("grain"):
        f.append(f"noise=c0s={round(4 + 26 * float(lk['grain']))}:c0f=t+u")
    return ",".join(f)


def segment_graph(k: int, seg: dict, out: dict, grade: dict, audio_in: str | None, preview: bool,
                  workdir: Path, lead: float = 0.0, lack: float = 0.0) -> tuple[str, float]:
    """Picture and sound of one segment. The picture runs `_pre` before the cut and `_post` after
    it (handles for a transition); `lead` = how much of the input comes before the cut (source
    seconds), `lack` = the part of the front handle the source doesn't have (first frame held).
    The sound starts `_alead` before the cut and ends `_atail` after the segment (J/L cuts and
    crossfades), so picture and sound each join on their own."""
    W, H, fps = out["width"], out["height"], out["fps"]
    dur = frames(seg_time(seg), fps) / fps
    pre, post = seg.get("_pre", 0.0), seg.get("_post", 0.0)
    span = seg["out"] - seg["in"]
    simple = is_simple(seg)
    v = float(seg.get("speed", 1)) if simple else 1.0
    g = grade.get(seg.get("name", Path(seg["source"]).name)) or {}
    rot = float(seg.get("rotate", 0)) % 360
    chain = []
    if not seg.get("_up"):          # an upscaled segment comes graded and framed already
        pre_f = pre_filter(MATERIAL.get(seg.get("name", Path(seg["source"]).name)), g)
        if pre_f:
            chain.append(pre_f)
        if g.get("lut"):
            chain.append(f"lut3d=file='{filter_path(g['lut'])}'")
        chain += redact(k, seg, lead, fps, workdir)
        if seg.get("stabilize"):
            strength = 1.0 if seg["stabilize"] is True else float(seg["stabilize"])
            chain.append(f"vidstabtransform=input='{filter_path(seg['_trf'])}':smoothing={round(10 + 30 * strength)}:"
                         "optzoom=1:optalgo=gauss:interpol=bicubic" if seg.get("_trf") else "deshake")
        if rot in (90, 270):
            chain.append(f"transpose={1 if rot == 90 else 2}")
        # The whole picture fitted (fit), framing from reframe.py, or a plain center crop to the output aspect.
        win = lead + span + (post * v if simple else 0)
        how = seg.get("fit") or out.get("fit")
        chain.append(fit(k, how, W, H) if how else (rot not in (90, 270) and framing(k, seg, win, fps, workdir, lead))
                     or f"crop='min(iw,ih*{W}/{H})':'min(ih,iw*{H}/{W})'")
    chain += [f"scale={W}:{H}:flags={'bilinear' if preview else 'lanczos'}", "setsar=1"]
    flip = seg.get("flip")
    chain += ["hflip"] * (flip in ("h", "both")) + ["vflip"] * (flip in ("v", "both")) + ["hflip,vflip"] * (rot == 180)
    if rot % 90:
        # A tilt: rotated, then zoomed just enough that no corner shows.
        z = abs(math.cos(math.radians(rot))) + max(W / H, H / W) * abs(math.sin(math.radians(rot)))
        chain.append(f"rotate={math.radians(rot):.6f},scale={round(W * z / 2) * 2}:{round(H * z / 2) * 2},crop={W}:{H}")
    if seg.get("key"):
        chain.append(chroma(k, seg["key"], W, H, fps))
    head = f"[{k}:v:0]"
    if simple:
        if seg.get("smooth") and v > 1.5:
            chain.append(f"tmix=frames={min(round(v), 8)}")        # fast motion blends its frames: no strobing
        chain.append(f"setpts=(PTS-STARTPTS)/{v:.6f}")
    else:
        # The played material (the cut, reversed, looped, there and back), then the time map.
        n = max(1, int(seg.get("loop", 1)))
        order = [bool(seg.get("reverse")) ^ (i % 2 == 1)
                 for i in range(n * 2 if seg.get("boomerang") else n)]
        chain += [f"trim=duration={span:.6f}", "setpts=PTS-STARTPTS"]
        if len(order) > 1:
            # ponytail: reverse holds the whole segment in memory (≈3 MB a frame at 1080p): fine for
            # the few seconds a reversal or boomerang lasts, not for minutes.
            copies = "".join(f"[m{k}_{i}]" for i in range(len(order)))
            head += f"{','.join(chain)},split={len(order)}{copies};"
            head += "".join(f"[m{k}_{i}]{'reverse' if r else 'null'}[n{k}_{i}];" for i, r in enumerate(order))
            head += "".join(f"[n{k}_{i}]" for i in range(len(order))) + f"concat=n={len(order)}:v=1:a=0,"
            chain = []
        elif order[0]:
            chain.append("reverse")
        if seg.get("smooth") and any(max(p[2], p[3]) > 1.5 for p in pieces(seg)):
            chain.append("tmix=frames=3")
        chain.append(f"setpts='{remap(seg)}'")
    slow = min(min(p[2], p[3]) for p in pieces(seg)) if not simple else v
    if seg.get("smooth") and slow < 0.9:
        # In-between frames, before fps would fill the gaps with copies and leave nothing to interpolate.
        chain.append(f"minterpolate=fps={fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1")
    chain.append(f"fps={fps}" if simple else f"fps={fps}:start_time=0")
    end_hold = sum(d for a, d in seg.get("hold", []) if a >= play_len(seg) - 1 / fps)
    chain += [f"tpad=start_mode=clone:start_duration={(lack if simple else pre):.6f}:"
              f"stop_mode=clone:stop_duration={post + end_hold + 0.2:.6f}",
              f"trim=duration={pre + dur + post:.6f}", "setpts=PTS-STARTPTS"]
    for side in ("in", "out"):
        f = seg.get(f"fade_{side}")
        if f:
            d = max(min(float(f.get("dur", 0.5)), dur - 1 / fps), 1 / fps)
            st = pre if side == "in" else max(pre + dur - d - 1 / fps, 0)     # the last frame fully faded
            chain.append(f"fade=t={side}:st={st:.6f}:d={d:.6f}:c={f.get('color', 'black')}")
    chain.append("format=yuv420p")
    v_graph = head + f"{','.join(chain)}[v{k}]"

    a_len = seg.get("_alead", 0.0) + dur + seg.get("_atail", 0.0)
    # ponytail: a fade's sound fades over the sound's own first/last seconds, not the picture's.
    d_in = float((seg.get("fade_in") or {}).get("dur", FADE_S))
    d_out = float((seg.get("fade_out") or {}).get("dur", FADE_S))
    fades = f"afade=t=in:d={d_in:.6f},afade=t=out:st={max(a_len - d_out, 0):.6f}:d={d_out:.6f}"
    if audio_in:
        mode = audio_mode(seg)
        # Room tone standing in keeps its own speed and gets no voice effect.
        tempo = "" if abs(v - 1) < 1e-6 or seg.get("_rt") else atempo(v) + "," if mode == "keep" else \
            f"asetrate={48000 * v:.3f},aresample=48000,"
        if seg.get("voice") and not seg.get("_rt"):
            tempo += VOICE.get(seg["voice"], seg["voice"]) + ","
        delay = f"adelay=delays={round(seg['_alack'] * 1000)}:all=1," if seg.get("_alack") else ""
        if seg.get("gain_db"):          # a segment's own sound lower or higher (a montage keeps it as an accent)
            delay += f"volume={float(seg['gain_db']):.2f}dB,"
        a = (f"[{audio_in}]{seg['_pan'] + ',' if seg.get('_pan') else ''}aformat=sample_rates=48000:channel_layouts=stereo,"
             f"{tempo}{delay}apad,atrim=duration={a_len:.6f},asetpts=PTS-STARTPTS,{fades}[a{k}]")
    else:
        a = f"anullsrc=r=48000:cl=stereo,atrim=duration={a_len:.6f}[a{k}]"
    return v_graph + ";" + a, dur


def inset(k: int, inp: int, seg: dict, out: dict, grade: dict, length: float) -> str:
    """Another source on top of segment k's picture: picture-in-picture in a corner ("pip") or
    the screen split in two ("split", the other source on `side`). Its sound isn't used."""
    W, H, fps = out["width"], out["height"], out["fps"]
    p = seg.get("pip") or seg["split"]
    name = Path(p["source"]).name
    g = grade.get(name) or {}
    chain = [x for x in (pre_filter(MATERIAL.get(name), g),
                         f"lut3d=file='{filter_path(g['lut'])}'" if g.get("lut") else None) if x]
    tail = [f"fps={fps}", "setpts=PTS-STARTPTS", f"tpad=stop_mode=clone:stop_duration={length:.4f}",
            f"trim=duration={length:.4f}", "setsar=1"]
    if seg.get("split"):
        side = p.get("side", "right")
        across = side in ("left", "right")
        w, h = (W // 2, H) if across else (W, H // 2)
        ow, oh = (W - w, H) if across else (W, H - h)
        # ponytail: the speaker's half is a center crop of the framed picture, not re-framed on the face.
        chain += [f"crop='min(iw,ih*{ow}/{oh})':'min(ih,iw*{oh}/{ow})'", f"scale={ow}:{oh}", *tail]
        pair = f"[o{k}][vm{k}]" if side in ("left", "top") else f"[vm{k}][o{k}]"
        return (f"[{inp}:v:0]{','.join(chain)}[o{k}];[vm{k}]crop={w}:{h}[vm{k}c];" +
                pair.replace(f"[vm{k}]", f"[vm{k}c]") + f"{'hstack' if across else 'vstack'},format=yuv420p[v{k}]")
    corner = p.get("corner", "top-right")
    m = round(0.04 * min(W, H))
    pw = round(W * float(p.get("scale", 0.32)) / 2) * 2
    # In a vertical video the corners sit inside the safe band (platform buttons and captions around it).
    top, bottom = (f"{round(0.13 * H)}", f"{round(0.77 * H)}-h") if H > W else (f"{m}", f"H-h-{m}")
    x = f"{m}" if "left" in corner else f"W-w-{m}"
    y = top if "top" in corner else bottom
    chain += [f"scale={pw}:-2", *tail]
    return f"[{inp}:v:0]{','.join(chain)}[o{k}];[vm{k}][o{k}]overlay=x={x}:y={y}:eof_action=repeat,format=yuv420p[v{k}]"


def broll_graph(first_input: int, base: str, items: list[dict], out: dict, grade: dict) -> tuple[list, list, str]:
    """Inputs and filters that lay each b-roll over the edit at its timeline position:
    framed to the output, graded like its source, with a slow 4% push-in. The edit's
    sound keeps playing underneath."""
    W, H, fps = out["width"], out["height"], out["fps"]
    inputs, graphs, cur = [], [], base
    for j, b in enumerate(items):
        k = first_input + j
        inputs += [*HWDEC, "-ss", f"{b['in']:.4f}", "-t", f"{b['dur'] + 0.3:.4f}", "-i", b["source"]]
        g = grade.get(b["clip"]) or {}
        chain = [x for x in (pre_filter(MATERIAL.get(b["clip"]), g),
                             f"lut3d=file='{filter_path(g['lut'])}'" if g.get("lut") else None) if x]
        n = frames(b["dur"], fps)
        chain += [f"crop='min(iw,ih*{W}/{H})':'min(ih,iw*{H}/{W})'", f"scale={W}:{H}", "setsar=1", f"fps={fps}",
                  f"zoompan=z='1+0.04*on/{n}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={W}x{H}:fps={fps}",
                  f"trim=end_frame={n}", f"setpts=PTS-STARTPTS+{b['at']:.4f}/TB", "format=yuv420p"]
        graphs.append(f"[{k}:v:0]{','.join(chain)}[b{j}]")
        nxt = f"o{j}"
        graphs.append(f"[{cur}][b{j}]overlay=eof_action=pass:repeatlast=0[{nxt}]")
        cur = nxt
    return inputs, graphs, cur


def finish_graph(first_input: int, v: str, a: str, fin: dict, out: dict, grade: dict) -> tuple[list, list]:
    """What goes over the whole edit: b-roll, the graphics layer, then subtitles on top of
    everything; the voice bus (gentle compression across cuts), music ducked under it,
    then the sound effects."""
    b_inputs, graphs, v_last = broll_graph(first_input, v, fin["overlays"], out, grade)
    nxt_input = first_input + len(fin["overlays"])
    if look(out.get("look") or {}):
        graphs.append(f"[{v_last}]{look(out['look'])}[vlook]")
        v_last = "vlook"
    g_inputs = []
    if fin.get("graphics"):
        gpath = Path(fin["graphics"])
        # VP9 keeps its alpha only through the libvpx decoder; ProRes 4444 / PNG MOV need nothing.
        g_inputs = (["-c:v", "libvpx-vp9"] if gpath.suffix.lower() == ".webm" else []) + ["-i", str(gpath)]
        graphs.append(f"[{nxt_input}:v:0]format=yuva420p,scale={out['width']}:{out['height']},"
                      f"fps={out['fps']},setpts=PTS-STARTPTS[gfx]")
        graphs.append(f"[{v_last}][gfx]overlay=eof_action=pass:repeatlast=0:format=auto[vg]")
        v_last, nxt_input = "vg", nxt_input + 1
        # Segments marked "behind": the person (their picture, cut out by its mask) back over the graphics.
        for j, (at, pic, mask) in enumerate(fin.get("behind", [])):
            g_inputs += ["-i", pic, "-i", mask]
            put = f"setpts=PTS-STARTPTS+{at:.4f}/TB"
            # Never over b-roll: the person belongs to the main picture, which the b-roll covers.
            hide = "+".join(f"between(t,{b['at']:.3f},{b['at'] + b['dur']:.3f})" for b in fin["overlays"])
            graphs += [f"[{nxt_input}:v:0]{look(out.get('look') or {}) or 'null'},format=yuva420p,{put}[pp{j}]",
                       f"[{nxt_input + 1}:v:0]format=gray,scale={out['width']}:{out['height']},tmix=frames=3,{put}[pm{j}]",
                       f"[pp{j}][pm{j}]alphamerge[pa{j}]",
                       f"[{v_last}][pa{j}]overlay=eof_action=pass:repeatlast=0"
                       f"{f':enable=not({hide})' if hide else ''}[vb{j}]"]
            v_last, nxt_input = f"vb{j}", nxt_input + 2
    if fin.get("subs"):
        fonts = f":fontsdir='{filter_path(fin['fontsdir'])}'" if fin.get("fontsdir") else ""
        graphs.append(f"[{v_last}]subtitles=filename='{filter_path(fin['subs'])}'{fonts}[vs]")
        v_last = "vs"
    graphs.append(f"[{v_last}]null[v]")
    bl = fin.get("bleep")
    if bl:
        # Censored words: the voice silenced over them, a 1 kHz tone in its place (or nothing).
        e = "+".join(f"between(t,{x:.3f},{y:.3f})" for x, y in bl["spans"])
        graphs.append(f"[{a}]volume=0:enable='{e}'[bmute]")
        a = "bmute"
        if bl["sound"] == "beep":
            graphs += [f"aevalsrc='0.2*sin(2*PI*1000*t)*({e})':s=48000:c=stereo:d={fin['total']:.3f}[btone]",
                       "[bmute][btone]amix=inputs=2:normalize=0:duration=first[bleeped]"]
            a = "bleeped"
    graphs.append(f"[{a}]{BUS}[voice]")
    m_inputs, m_graphs, a_last = music_graph(nxt_input, "voice", fin["music"], fin["total"])
    graphs += m_graphs
    s_inputs, s_graphs, bus = sfx_graph(nxt_input + len(fin["music"]), fin.get("sfx", []), fin["total"])
    if bus:
        graphs += s_graphs + [f"[{a_last}][{bus}]amix=inputs=2:normalize=0:duration=first[withsfx]"]
        a_last = "withsfx"
    graphs.append(f"[{a_last}]anull[a]")
    return b_inputs + g_inputs + m_inputs + s_inputs, graphs


def bleep_spans(edl: dict, segs: list[dict]) -> dict | None:
    """Timeline spans of the words on edl.json's "bleep" list (subtitles show them starred)."""
    bl = edl.get("bleep")
    tr_p = Path("01-ingest/transcript.json")
    if not (bl and bl.get("words")):
        return None
    if not tr_p.exists():
        print("WARNING: bleep: no 01-ingest/transcript.json — nothing censored")
        return None
    from subtitles import timeline_words
    words = timeline_words({**edl, "segments": segs}, json.loads(tr_p.read_text(encoding="utf-8")))
    spans = [(max(w["t"] - 0.04, 0), w["end"] + 0.04) for w in words if w.get("bleep")]
    return {"spans": spans, "sound": bl.get("sound", "beep")} if spans else None


XFADE = {"dissolve": "fade", "crossfade": "fade", "cross": "fade", "zoom": "zoomin",   # names people use → xfade
         "push": "slideleft", "push-left": "slideleft", "push-right": "slideright", "push-up": "slideup",
         "push-down": "slidedown"}
# A whip pan: the next shot slides in with motion blur along the move.
WHIP = {"whip": ("slideleft", "x"), "whip-left": ("slideleft", "x"), "whip-right": ("slideright", "x"),
        "whip-up": ("slideup", "y"), "whip-down": ("slidedown", "y")}


def edges(segs: list[dict], fps: float) -> list[str]:
    """Where picture and sound of each segment start and end around its cut: transition handles
    (half the transition on each side, so the timeline doesn't move) and J/L cuts (the sound's cut
    moves, the picture's stays). Returns warnings."""
    for s in segs:
        s.update(_pre=0.0, _post=0.0, _delta=0.0, _tr=None)
    cuts, warn = jl_cuts(segs, fps)
    for k in range(1, len(segs)):
        s, prev = segs[k], segs[k - 1]
        d = transition_dur(segs, k, fps)
        if d > 0:
            typ = s["transition"].get("type", "dissolve")
            typ, axis = WHIP.get(typ, (XFADE.get(typ, typ), None))
            s["_tr"] = (typ, d, axis)
            s["_pre"] = prev["_post"] = d / 2
        j, l_ = cuts[k]
        s["_delta"] = l_ - j
    for k, s in enumerate(segs):
        s["_alead"] = s["_pre"] - s["_delta"]
        s["_atail"] = s["_post"] + (segs[k + 1]["_delta"] if k + 1 < len(segs) else 0.0)
    return warn


def join(segs: list[dict], fps: float, v_out: str, a_out: str, width: int = 1920) -> list[str]:
    """Picture and sound joined, each on its own: straight cuts concatenate, a transition
    crossfades (xfade / acrossfade) over the handles both segments carry; a whip adds blur
    along the move (light over the transition, strong in its middle)."""
    groups = [[0]]
    for k in range(1, len(segs)):
        (groups.append([k]) if segs[k].get("_tr") else groups[-1].append(k))
    graphs = []
    for kind, out_label in (("v", v_out), ("a", a_out)):
        cur, length = None, 0.0
        for gi, g in enumerate(groups):
            lbl = f"{kind}{g[0]}"
            if len(g) == 1 and kind == "v" and len(groups) > 1:
                # xfade wants both inputs on one time base: a concat group is on AVTB, a lone segment on 1/fps.
                graphs.append(f"[{lbl}]settb=AVTB[{kind}t{gi}]")
                lbl = f"{kind}t{gi}"
            if len(g) > 1:
                lbl = f"{kind}g{gi}"
                graphs.append("".join(f"[{kind}{k}]" for k in g) +
                              f"concat=n={len(g)}:v={int(kind == 'v')}:a={int(kind == 'a')}[{lbl}]")
            g_len = sum((s["_pre"] + frames(seg_time(s), fps) / fps + s["_post"]) if kind == "v" else
                        (s["_alead"] + frames(seg_time(s), fps) / fps + s["_atail"]) for s in (segs[k] for k in g))
            if cur is None:
                cur, length = lbl, g_len
                continue
            typ, d, axis = segs[g[0]]["_tr"]
            nxt, at = f"{kind}x{gi}", length - d
            graphs.append(f"[{cur}][{lbl}]xfade=transition={typ}:duration={d:.6f}:offset={at:.6f}[{nxt}]"
                          if kind == "v" else f"[{cur}][{lbl}]acrossfade=d={d:.6f}:c1=tri:c2=tri[{nxt}]")
            if kind == "v" and axis:
                blur = [f"gblur=sigma={width / r if axis == 'x' else 0:.1f}:sigmaV={0 if axis == 'x' else width / r:.1f}:"
                        f"enable='between(t,{a:.4f},{b:.4f})'"
                        for r, a, b in ((80, at, at + d), (30, at + d / 4, at + 3 * d / 4))]
                graphs.append(f"[{nxt}]{','.join(blur)}[{nxt}w]")
                nxt += "w"
            cur, length = nxt, length - d + g_len
        graphs.append(f"[{cur}]{'null' if kind == 'v' else 'anull'}[{out_label}]")
    return graphs


def matte_paths(s: dict, out: dict, grade: dict) -> tuple[Path, Path]:
    """Where a segment's cut-out picture and mask live, named by what makes the picture."""
    import hashlib
    own = {a: b for a, b in s.items() if not a.startswith("_")}
    key = hashlib.sha1(json.dumps([own, out, grade.get(s.get("name", Path(s["source"]).name))],
                                  sort_keys=True, default=str).encode()).hexdigest()[:12]
    return Path("02-work/matte") / f"{key}.mkv", Path("02-work/matte") / f"{key}.mask.mkv"


def mattes(segs: list[dict], out: dict, grade: dict, clean: dict, log: Log, preview: bool) -> list[tuple]:
    """The person in each segment marked "behind" (graphics go between the background and them):
    the segment rendered on its own, then its mask (matte.py, rembg). Both kept in 02-work/matte/,
    named by what makes the picture, so a re-render reuses them. Returns (start, picture, mask)."""
    fps, res = out["fps"], []
    t0 = starts(segs, fps)
    for k, s in enumerate(segs):
        if not s.get("behind"):
            continue
        pic, mask = matte_paths(s, out, grade)
        if not mask.exists():
            pic.parent.mkdir(parents=True, exist_ok=True)
            one = {**s, "_pre": 0.0, "_post": 0.0, "_delta": 0.0, "_tr": None, "_alead": 0.0, "_atail": 0.0}
            render_chunk([one], out, grade, INTERMEDIATE, pic, log, preview, f"segment {k + 1} for its matte", clean)
            n = frames(seg_time(s), fps)
            log(f"cutting out the person in segment {k + 1} ({n} frames: ~{n * 0.1:.0f} s on an NVIDIA GPU, "
                f"~{n * 0.8:.0f} s on the CPU)")
            r = run([tool("uv"), "run", str(Path(__file__).with_name("matte.py")), "--in", str(pic), "--out", str(mask)],
                    check=False)
            if r.returncode or not mask.exists():
                log(f"WARNING: segment {k + 1}: no matte ({(r.stderr or r.stdout).strip().splitlines()[-1:]}) — "
                    "its graphics stay in front of the person")
                continue
            log((r.stdout.strip().splitlines() or [""])[0])
        res.append((t0[k], str(pic), str(mask)))
    return res


def render_chunk(segs: list[dict], out: dict, grade: dict, enc: list[str], dst: Path, log: Log,
                 preview: bool, label: str, clean: dict, fin: dict | None = None) -> float:
    """Segments joined in one pass. With `fin`, the finishing (b-roll, subtitles, voice bus,
    music) happens in the same pass; without it (chunks of a long edit) it comes later."""
    inputs, graphs, total = [], [], 0.0
    with tempfile.TemporaryDirectory(prefix="vibetake-render-") as tmp:
        workdir = Path(tmp)
        idx = 0
        fps = out["fps"]
        rt = next((x["roomtone"] for x in clean.values() if x.get("roomtone")), None)
        for k, s in enumerate(segs):
            dur = frames(seg_time(s), fps) / fps
            span = s["out"] - s["in"]
            simple = is_simple(s)
            v = float(s.get("speed", 1)) if simple else 1.0
            # The picture: the cut plus its transition handles, taken from the source when it has them.
            lead = min(s["_pre"] * v, s["in"]) if simple and not s.get("_up") else 0.0
            lack = s["_pre"] - lead / v if simple else 0.0
            length = lead + span + (s["_post"] * v if simple else 0) + 0.25
            if s.get("_up"):
                inputs += ["-t", f"{length:.4f}", "-i", s["_up"]]          # upscale.py's picture
            else:
                inputs += [*(HWDEC if k < HWDEC_MAX else []), "-ss", f"{s['in'] - lead:.4f}", "-t", f"{length:.4f}",
                           "-i", s["source"]]
            vid, idx = idx, idx + 1
            if s.get("stabilize") and not s.get("_up") and has_filter("vidstabdetect"):
                # vid.stab's first pass: the camera's shake over the same frames the picture uses.
                s["_trf"] = workdir / f"stab_{k:03d}.trf"
                log(f"stabilizing segment {k + 1}…")
                run([tool("ffmpeg"), "-hide_banner", "-v", "error", "-ss", f"{s['in'] - lead:.4f}", "-t", f"{length:.4f}",
                     "-i", s["source"], "-vf", f"vidstabdetect=shakiness=8:result='{filter_path(s['_trf'])}'",
                     "-f", "null", "-"])
            # The sound: its own window (J/L cuts and crossfades move it off the picture's).
            c = clean.get(s.get("name", Path(s["source"]).name))
            has_audio = any(st.get("codec_type") == "audio" for st in s["_probe"]["streams"])
            a_from = s["in"] - s["_alead"] * v
            s["_alack"] = max(-a_from, 0) / v
            a_win = ["-vn", "-ss", f"{max(a_from, 0):.4f}", "-t", f"{(s['_alead'] + dur + s['_atail']) * v + 0.25:.4f}"]
            if audio_mode(s) == "mute" or not (c or has_audio):
                # No sound of its own (none recorded, or a speed effect): the room's air, never dead silence.
                s["_rt"] = bool(rt)
                if rt:
                    inputs += ["-stream_loop", "-1", "-i", rt]
                    audio_in, idx = f"{idx}:a:0", idx + 1
                else:
                    audio_in = None
            elif c:
                # The cleaned audio of this file (audio.py clean): same timing as the original.
                inputs += [*a_win, "-i", c["file"]]
                audio_in, idx = f"{idx}:a:0", idx + 1
            else:
                inputs += [*a_win, "-i", s["source"]]
                audio_in, idx = f"{idx}:a:{s.get('track', 0)}", idx + 1
                # Raw audio (no cleanup): a mic on one channel still goes to the center.
                info = next((a for a in (MATERIAL.get(s.get("name", "")) or {}).get("audio", [])
                             if a["index"] == s.get("track", 0)), {})
                s["_pan"] = channel_pan(info.get("channels"))
            gph, d = segment_graph(k, s, out, grade, audio_in, preview, workdir, lead, lack)
            gph = gph.replace(f"[{k}:v:0]", f"[{vid}:v:0]", 1)
            other = s.get("pip") or s.get("split")
            if other:
                # ponytail: the inset goes over the segment after its fades, so a fade-out leaves it showing.
                length = s["_pre"] + d + s["_post"]
                inputs += ["-ss", f"{max(float(other.get('in', 0)) - s['_pre'], 0):.4f}", "-t", f"{length + 0.25:.4f}",
                           "-i", other["source"]]
                gph = gph.replace(f"[v{k}];", f"[vm{k}];", 1) + ";" + inset(k, idx, s, out, grade, length)
                idx += 1
            graphs.append(gph)
            total += d
        v_out, a_out = ("vc", "ac") if fin else ("v", "a")
        graphs += join(segs, fps, v_out, a_out, out["width"])
        if fin:
            f_inputs, f_graphs = finish_graph(idx, "vc", "ac", fin, out, grade)
            inputs += f_inputs
            graphs += f_graphs
        script = workdir / "graph.txt"
        # Long graphs overflow the command line, so the graph goes in a file.
        script.write_text(";".join(graphs), encoding="utf-8")
        audio = ["-c:a", "pcm_s16le"] if dst.suffix == ".mkv" else ["-c:a", "aac", "-b:a", "320k"]
        ffmpeg_progress([*inputs, "-/filter_complex", str(script), "-map", "[v]", "-map", "[a]",
                         *enc, "-pix_fmt", "yuv420p", *TAGS, *audio, "-ar", "48000",
                         *(["-movflags", "+faststart"] if dst.suffix == ".mp4" else []), str(dst)],
                        total, label, log)
    return total


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("edl", nargs="?", help="default: 02-work/edl.json (a variant: 02-work/edl_<variant>.json)")
    ap.add_argument("--out", help="output file (default: the next version in 03-delivery/)")
    ap.add_argument("--overwrite", action="store_true", help="replace the latest version instead of adding one")
    ap.add_argument("--variant", help="a variant of the current version (hookB, 4x5…): <name>_v<N>_<variant>.mp4, "
                                      "same N as the main video, replaced on each render")
    ap.add_argument("--grade", default="02-work/color/grade.json")
    ap.add_argument("--no-grade", action="store_true")
    ap.add_argument("--preview", action="store_true", help="small, fast render to check the edit")
    ap.add_argument("--loudness", choices=tuple(TARGETS), default="social")
    ap.add_argument("--clean", default="01-ingest/audio/clean.json", help="cleaned audio index (audio.py clean)")
    ap.add_argument("--raw-audio", action="store_true", help="use the original audio even if cleaned exists")
    ap.add_argument("--subs", help="burned-in subtitles (default: 02-work/subs.ass, or the variant's subs_<v>.ass)")
    ap.add_argument("--no-subs", action="store_true")
    ap.add_argument("--fontsdir", help="folder with the subtitle fonts (default: 02-work/fonts, from fonts.py)")
    ap.add_argument("--graphics", help="graphics layer with alpha, as long as the edit "
                                       "(default: 02-work/graphics.mov or .webm if present)")
    ap.add_argument("--no-graphics", action="store_true")
    ap.add_argument("--no-sfx", action="store_true")
    ap.add_argument("--allow-stale", action="store_true",
                    help="render the master even with subtitles/graphics made for another cut")
    ap.add_argument("--upload-height", type=int, help="scale the master up to this height for upload "
                                                      "(e.g. 1440 for YouTube: it gets better codecs)")
    args = ap.parse_args()

    paths = work_paths(args.variant)
    if not args.edl:
        args.edl = str(paths["edl"])
        if args.variant and not paths["edl"].exists():
            die(f"the variant has no cut of its own ({paths['edl']}): cutlist.py --variant {args.variant} "
                "(then reframe, subtitles and graphics with --variant too)")
    args.subs = args.subs or str(paths["subs"])
    edl = json.loads(Path(args.edl).read_text(encoding="utf-8"))
    MATERIAL.update(material_index())
    sig = timeline_sig(edl)                    # before anything below touches the segments
    segs = edl["segments"]
    if not segs:
        die("the edit has no segments")
    out = dict(edl["output"])
    if args.upload_height and not args.preview and args.upload_height > out["height"]:
        # Same framing, more pixels (lanczos): the platform's better codec pays off, no detail is invented.
        k = args.upload_height / out["height"]
        out["width"], out["height"] = round(out["width"] * k / 2) * 2, args.upload_height
    if args.preview:
        scale = min(1.0, 540 / min(out["width"], out["height"]))
        out["width"], out["height"] = (int(out["width"] * scale) // 2 * 2, int(out["height"] * scale) // 2 * 2)
    grade = {}
    if not args.no_grade and Path(args.grade).exists():
        grade = json.loads(Path(args.grade).read_text(encoding="utf-8"))
    probes = {}
    from upscale import signature
    for k, s in enumerate(segs):
        up = s.get("upscaled")
        if up:
            if Path(up["file"]).exists() and up.get("sig") == signature(s, grade, edl["output"]):
                s["_up"] = up["file"]
            else:
                print(f"WARNING: segment {k}'s upscaled picture is stale or missing (the edit, framing or "
                      f"grade changed): using the original. Re-run upscale.py run --seg {k} if it's needed.")
    for s in segs:
        s["_probe"] = probes.setdefault(s["source"], ffprobe(Path(s["source"])))
        # Cut on the source's frame grid, for picture and sound alike: a cut between two
        # frames would start the picture on the next frame but the sound right away.
        v = next((st for st in s["_probe"]["streams"] if st.get("codec_type") == "video"), {})
        n, d = (v.get("avg_frame_rate") or "0/0").split("/")
        src_fps = float(n) / float(d) if float(d) else 0
        if src_fps:
            s["in"] = round(s["in"] * src_fps) / src_fps
            s["out"] = round(s["out"] * src_fps) / src_fps
    for w in edges(segs, out["fps"]):
        print(f"WARNING: {w}")
    if args.out:
        dst = Path(args.out)
    elif args.preview:
        dst = Path("02-work/preview.mp4")
    else:
        name = deliverable_name(edl)
        have = versions(name)
        if args.variant:
            # A variant belongs to the latest main version: it doesn't open a new one.
            n = have[-1][0] if have else 1
            dst = DELIVERY / f"{name}_v{n}_{args.variant}.mp4"
        else:
            n = (have[-1][0] if args.overwrite else have[-1][0] + 1) if have else 1
            dst = DELIVERY / f"{name}_v{n}.mp4"
    dst.parent.mkdir(parents=True, exist_ok=True)
    log = Log(Path("02-work") / "render.log")
    enc_name, enc = pick(PREVIEW if args.preview else MASTER)
    graded = sum(1 for s in segs if grade.get(s.get("name", Path(s["source"]).name), {}).get("lut"))
    log(f"rendering {len(segs)} segments → {dst} ({out['width']}×{out['height']} @ {out['fps']} fps, "
        f"{enc_name}, color on {graded}/{len(segs)} segments)")

    if any((s.get("frame") or {}).get("bump") or (s.get("frame") or {}).get("drift") for s in segs) \
            and not dynamic_crop_ok():
        log("WARNING: this ffmpeg can't change the crop size mid-stream: impact zoom and drift skipped "
            "(punch-ins still alternate). A recent ffmpeg build enables them.")
    overlays, warnings = resolve_broll(segs, out["fps"], edl.get("broll", []))
    for w in warnings:
        log(f"WARNING: {w}")
    if overlays:
        log(f"b-roll: {len(overlays)} clip(s) over {sum(b['dur'] for b in overlays):.1f} s")
    clean = {}
    if not args.raw_audio and Path(args.clean).exists():
        clean = json.loads(Path(args.clean).read_text(encoding="utf-8"))
    subs = None if args.no_subs else Path(args.subs)
    in_layer = not args.no_subs and paths["captions"].exists()     # subtitles.py --engine remotion
    if args.variant and subs and not subs.exists() and not in_layer:
        log(f"WARNING: this variant has no captions of its own: subtitles.py --variant {args.variant}")
    if not args.fontsdir and Path("02-work/fonts").is_dir():
        args.fontsdir = "02-work/fonts"
    graphics = None
    if not args.no_graphics:
        graphics = Path(args.graphics) if args.graphics else next(
            (p for p in (paths["layer"], paths["layer"].with_suffix(".webm")) if p.exists()), None)
        if graphics and not graphics.exists():
            die(f"{graphics} doesn't exist")
    sfx, sfx_warn = ([], []) if args.no_sfx else resolve_sfx(segs, out["fps"], edl.get("sfx", []))
    for w in sfx_warn:
        log(f"WARNING: {w}")
    if in_layer and not graphics:
        log("WARNING: the captions are drawn by the graphics layer and there is none: graphics.py render")
    fin = {"overlays": overlays, "music": place_music(edl.get("music", []), segs, out["fps"]), "total": timeline_total(segs, out["fps"]),
           "subs": subs if subs and subs.exists() else None, "fontsdir": args.fontsdir,
           "graphics": graphics, "sfx": sfx, "bleep": bleep_spans(edl, segs)}
    if graphics and any(s.get("behind") for s in segs):
        fin["behind"] = mattes(segs, out, grade, clean, log, args.preview)
    elif any(s.get("behind") for s in segs):
        log("WARNING: segments marked \"behind\" but no graphics layer: nothing to put behind the person")
    # Layers made for another version of the cut land at the wrong times: catch it here.
    stale = []
    if fin["subs"]:
        mark = re.search(r"vibetake-timeline: (\w+)", fin["subs"].read_text(encoding="utf-8", errors="replace")[:400])
        if mark and mark.group(1) != sig:
            stale.append(f"subtitles ({fin['subs']}) were made for another cut: run subtitles.py again "
                         "(a variant: subtitles.py --edl <its edl> --ass <its own .ass>, then --subs)")
    meta = graphics.with_suffix(".meta.json") if graphics else None
    if meta and meta.exists():
        gm = json.loads(meta.read_text(encoding="utf-8"))
        if gm.get("timeline") and gm["timeline"] != sig:
            stale.append("the graphics layer was made for another cut: graphics.py cues, then graphics.py render "
                         "(or --no-graphics)")
        elif abs(gm["width"] / gm["height"] - out["width"] / out["height"]) > 0.01:
            stale.append(f"the graphics layer is {gm['width']}×{gm['height']}, another shape than this output "
                         "(it would be stretched)")
    unframed = [i for i, s in enumerate(segs) if not (s.get("frame") or s.get("_up") or s.get("fit") or out.get("fit"))]
    if unframed:
        log(f"WARNING: {len(unframed)} segment(s) without framing (plain center crop) — run reframe.py after "
            "every re-cut")
    for w in stale:
        log(f"WARNING: {w}")
    if stale and not args.preview and not args.allow_stale:
        die("not rendering the master with layers from another cut (fix the above, or --allow-stale)")
    log(f"audio: {'cleaned' if clean else 'original'} · music: {len(fin['music'])} · sfx: {len(sfx)} · "
        f"graphics: {graphics.name if graphics else 'none'} · "
        f"subtitles: {'burned-in' if fin['subs'] else 'in the graphics layer' if in_layer and graphics else 'none'} · loudness: {args.loudness}")

    pre = dst.with_name(dst.stem + ".premaster" + dst.suffix)
    cpu_name, cpu = (PREVIEW if args.preview else MASTER)[-1]

    def fallback(e: RuntimeError) -> list[str]:
        """A hardware encoder that passed the short test can still fail mid-render (session
        limits, drivers): finish on the CPU instead of stopping, and say so."""
        used_encoder[0] = cpu_name
        log(f"WARNING: {enc_name} failed mid-render ({str(e).splitlines()[0][:120]}) — finishing with {cpu_name}")
        return cpu
    used_encoder = [enc_name]
    groups = chunks(segs)
    if len(groups) == 1:
        try:
            total = render_chunk(segs, out, grade, enc, pre, log, args.preview, "render", clean, fin)
        except RuntimeError as e:
            if enc_name == cpu_name:
                raise
            enc = fallback(e)
            total = render_chunk(segs, out, grade, enc, pre, log, args.preview, "render", clean, fin)
    else:
        work = Path("02-work") / "render"
        work.mkdir(parents=True, exist_ok=True)
        parts, total = [], 0.0
        n = len(groups)
        for i, g in enumerate(groups):
            part = work / f"part_{i:03d}.mkv"
            total += render_chunk(g, out, grade, INTERMEDIATE, part, log, args.preview, f"chunk {i + 1}/{n}", clean)
            parts.append(part)
        lst = work / "parts.txt"
        lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
        f_inputs, f_graphs = finish_graph(1, "0:v", "0:a", fin, out, grade)

        def final(e_args: list[str]) -> None:
            ffmpeg_progress(["-f", "concat", "-safe", "0", "-i", str(lst), *f_inputs,
                             "-filter_complex", ";".join(f_graphs), "-map", "[v]", "-map", "[a]", *e_args,
                             "-pix_fmt", "yuv420p", *TAGS, "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart",
                             str(pre)], total, "final encode", log)
        try:
            final(enc)
        except RuntimeError as e:
            if enc_name == cpu_name:
                raise
            enc = fallback(e)
            final(enc)
        for p in parts:
            p.unlink(missing_ok=True)
    master(pre, dst, args.loudness, log)
    pre.unlink(missing_ok=True)
    chs = chapters({**edl, "segments": segs})
    if chs and dst.suffix == ".mp4":
        # Chapters in the file (players show them) and as text for the YouTube description.
        ends = [t for t, _ in chs[1:]] + [total]
        meta = dst.with_suffix(".ffmeta")
        esc = lambda s: re.sub(r"([=;#\\\n])", r"\\\1", s)  # noqa: E731  (ffmetadata escaping)
        meta.write_text(";FFMETADATA1\n" + "".join(
            f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={round(t * 1000)}\nEND={round(e * 1000)}\ntitle={esc(ti)}\n"
            for (t, ti), e in zip(chs, ends)), encoding="utf-8")
        tmp = dst.with_name(dst.stem + ".chapters" + dst.suffix)
        run([tool("ffmpeg"), "-hide_banner", "-v", "error", "-y", "-i", str(dst), "-i", str(meta), "-map", "0",
             "-map_chapters", "1", "-c", "copy", "-movflags", "+faststart", str(tmp)])
        tmp.replace(dst)
        meta.unlink()
        from deliver import youtube
        text, warn = youtube(chs, total)
        dst.with_suffix(".chapters.txt").write_text(text + "\n", encoding="utf-8")
        for w in warn:
            log(f"WARNING: chapters: {w}")
        log(f"chapters: {len(chs)} in the file and in {dst.with_suffix('.chapters.txt').name}")

    got = ffprobe(dst)
    vd = float(next(s for s in got["streams"] if s["codec_type"] == "video").get("duration") or 0)
    ad = float(next((s for s in got["streams"] if s["codec_type"] == "audio"), {}).get("duration") or 0)
    log(f"done: {tc(total)} expected · video {tc(vd)} · audio {tc(ad)} · "
        f"{dst.stat().st_size / 1e6:.1f} MB · encoded with {used_encoder[0]}")
    if abs(vd - ad) > 1.5 / out["fps"]:
        log(f"WARNING: picture and sound differ by {abs(vd - ad) * 1000:.0f} ms")
    if args.preview and len(segs) > 1:
        # Every join at a glance (sheets, a reel of ±1 s around each, clear mistakes flagged).
        import subprocess
        subprocess.run([tool("uv"), "run", str(Path(__file__).parent / "joins.py"), str(dst), "--edl", args.edl],
                       check=False)


if __name__ == "__main__":
    main()
