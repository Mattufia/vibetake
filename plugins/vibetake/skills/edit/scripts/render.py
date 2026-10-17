# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Render an edit decision list from the original files.

    uv run render.py [02-work/edl.json] [--out 03-delivery/edit.mp4] [--preview] [--no-grade]

Per segment: seek into the original, color (HDR pre-filter + the file's LUT from
02-work/color/grade.json), frame to the output size, constant frame rate, and a
12 ms audio fade at each edge so cuts never click. Segments are joined in one pass
(in chunks for long edits). Every segment is cut to a whole number of frames for
both picture and sound, so sync never drifts.

Master: hardware encoder at constant quality with its best preset when it works,
x264 slow/CRF 17 otherwise. --preview: small and fast, for checking the edit.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _common import Log, die, ffmpeg_progress, ffprobe, filter_path, run, tc, tool  # noqa: E402
from _timeline import resolve_broll, total as timeline_total  # noqa: E402
from audio import BUS, TARGETS, master, music_graph  # noqa: E402
from sfx import resolve as resolve_sfx, sfx_graph  # noqa: E402

FADE_S = 0.012
CHUNK = 40
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


def framing(k: int, seg: dict, dur: float, fps: float, workdir: Path) -> str:
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
    for i in range(frames(dur, fps) + 1):
        t = i / fps
        z = zoom_at(t, dur, bump, drift) if sizing else 1.0
        cw, ch = max(2, round(bw / z / 2) * 2), max(2, round(bh / z / 2) * 2)
        cw, ch = min(cw, sw // 2 * 2), min(ch, sh // 2 * 2)
        cx = float(np.interp(t, kt, [k_[1] for k_ in keys]))
        cy = float(np.interp(t, kt, [k_[2] for k_ in keys]))
        x = int(min(max(cx * sw - cw / 2, 0), sw - cw))
        y = int(min(max(cy * sh - ch / 2, 0), sh - ch))
        if first is None:
            first = (cw, ch, x, y)
        size = f"crop@rf{k} w {cw}, crop@rf{k} h {ch}, " if sizing else ""
        lines.append(f"{t:.4f} {size}crop@rf{k} x {x}, crop@rf{k} y {y};")
    cmd = workdir / f"frame_{k:03d}.cmd"
    cmd.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cw, ch, x0, y0 = first
    return f"sendcmd=f='{filter_path(cmd)}',crop@rf{k}=w={cw}:h={ch}:x={x0}:y={y0}"


def segment_graph(k: int, seg: dict, out: dict, grade: dict, audio_in: str | None, preview: bool,
                  workdir: Path) -> tuple[str, str]:
    W, H, fps = out["width"], out["height"], out["fps"]
    dur = frames(seg["out"] - seg["in"], fps) / fps
    g = grade.get(seg.get("name", Path(seg["source"]).name)) or {}
    chain = []
    if g.get("pre"):
        chain.append(g["pre"])
    if g.get("lut"):
        chain.append(f"lut3d=file='{filter_path(g['lut'])}'")
    # Framing from reframe.py, or a plain center crop to the output aspect.
    chain.append(framing(k, seg, dur, fps, workdir) or f"crop='min(iw,ih*{W}/{H})':'min(ih,iw*{H}/{W})'")
    chain += [f"scale={W}:{H}:flags={'bilinear' if preview else 'lanczos'}", "setsar=1",
              f"fps={fps}", f"tpad=stop_mode=clone:stop_duration=0.2,trim=duration={dur:.6f}",
              "setpts=PTS-STARTPTS", "format=yuv420p"]
    v = f"[{k}:v:0]{','.join(chain)}[v{k}]"
    if audio_in:
        a = (f"[{audio_in}]aformat=sample_rates=48000:channel_layouts=stereo,"
             f"apad,atrim=duration={dur:.6f},asetpts=PTS-STARTPTS,"
             f"afade=t=in:d={FADE_S},afade=t=out:st={max(dur - FADE_S, 0):.6f}:d={FADE_S}[a{k}]")
    else:
        a = f"anullsrc=r=48000:cl=stereo,atrim=duration={dur:.6f}[a{k}]"
    return v + ";" + a, dur


def broll_graph(first_input: int, base: str, items: list[dict], out: dict, grade: dict) -> tuple[list, list, str]:
    """Inputs and filters that lay each b-roll over the edit at its timeline position:
    framed to the output, graded like its source, with a slow 4% push-in. The edit's
    sound keeps playing underneath."""
    W, H, fps = out["width"], out["height"], out["fps"]
    inputs, graphs, cur = [], [], base
    for j, b in enumerate(items):
        k = first_input + j
        inputs += ["-ss", f"{b['in']:.4f}", "-t", f"{b['dur'] + 0.3:.4f}", "-i", b["source"]]
        g = grade.get(b["clip"]) or {}
        chain = [x for x in (g.get("pre"), f"lut3d=file='{filter_path(g['lut'])}'" if g.get("lut") else None) if x]
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
    g_inputs = []
    if fin.get("graphics"):
        gpath = Path(fin["graphics"])
        # VP9 keeps its alpha only through the libvpx decoder; ProRes 4444 / PNG MOV need nothing.
        g_inputs = (["-c:v", "libvpx-vp9"] if gpath.suffix.lower() == ".webm" else []) + ["-i", str(gpath)]
        graphs.append(f"[{nxt_input}:v:0]format=yuva420p,scale={out['width']}:{out['height']},"
                      f"fps={out['fps']},setpts=PTS-STARTPTS[gfx]")
        graphs.append(f"[{v_last}][gfx]overlay=eof_action=pass:repeatlast=0:format=auto[vg]")
        v_last, nxt_input = "vg", nxt_input + 1
    if fin.get("subs"):
        fonts = f":fontsdir='{filter_path(fin['fontsdir'])}'" if fin.get("fontsdir") else ""
        graphs.append(f"[{v_last}]subtitles=filename='{filter_path(fin['subs'])}'{fonts}[vs]")
        v_last = "vs"
    graphs.append(f"[{v_last}]null[v]")
    graphs.append(f"[{a}]{BUS}[voice]")
    m_inputs, m_graphs, a_last = music_graph(nxt_input, "voice", fin["music"], fin["total"])
    graphs += m_graphs
    s_inputs, s_graphs, bus = sfx_graph(nxt_input + len(fin["music"]), fin.get("sfx", []), fin["total"])
    if bus:
        graphs += s_graphs + [f"[{a_last}][{bus}]amix=inputs=2:normalize=0:duration=first[withsfx]"]
        a_last = "withsfx"
    graphs.append(f"[{a_last}]anull[a]")
    return b_inputs + g_inputs + m_inputs + s_inputs, graphs


def render_chunk(segs: list[dict], out: dict, grade: dict, enc: list[str], dst: Path, log: Log,
                 preview: bool, label: str, clean: dict, fin: dict | None = None) -> float:
    """Segments joined in one pass. With `fin`, the finishing (b-roll, subtitles, voice bus,
    music) happens in the same pass; without it (chunks of a long edit) it comes later."""
    inputs, graphs, total = [], [], 0.0
    with tempfile.TemporaryDirectory(prefix="vibetake-render-") as tmp:
        workdir = Path(tmp)
        idx = 0
        for k, s in enumerate(segs):
            fps = out["fps"]
            dur = frames(s["out"] - s["in"], fps) / fps
            inputs += ["-ss", f"{s['in']:.4f}", "-t", f"{dur + 0.25:.4f}", "-i", s["source"]]
            vid, idx = idx, idx + 1
            c = clean.get(s.get("name", Path(s["source"]).name))
            if c:
                # The cleaned audio of this file (audio.py clean): same timing as the original.
                inputs += ["-ss", f"{s['in']:.4f}", "-t", f"{dur + 0.25:.4f}", "-i", c["file"]]
                audio_in, idx = f"{idx}:a:0", idx + 1
            elif any(st.get("codec_type") == "audio" for st in s["_probe"]["streams"]):
                audio_in = f"{vid}:a:{s.get('track', 0)}"
            else:
                audio_in = None
            gph, d = segment_graph(k, s, out, grade, audio_in, preview, workdir)
            graphs.append(gph.replace(f"[{k}:v:0]", f"[{vid}:v:0]", 1))
            total += d
        joined = "".join(f"[v{k}][a{k}]" for k in range(len(segs)))
        if fin:
            graphs.append(f"{joined}concat=n={len(segs)}:v=1:a=1[vc][ac]")
            f_inputs, f_graphs = finish_graph(idx, "vc", "ac", fin, out, grade)
            inputs += f_inputs
            graphs += f_graphs
        else:
            graphs.append(f"{joined}concat=n={len(segs)}:v=1:a=1[v][a]")
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
    ap.add_argument("edl", nargs="?", default="02-work/edl.json")
    ap.add_argument("--out")
    ap.add_argument("--grade", default="02-work/color/grade.json")
    ap.add_argument("--no-grade", action="store_true")
    ap.add_argument("--preview", action="store_true", help="small, fast render to check the edit")
    ap.add_argument("--loudness", choices=tuple(TARGETS), default="social")
    ap.add_argument("--clean", default="01-ingest/audio/clean.json", help="cleaned audio index (audio.py clean)")
    ap.add_argument("--raw-audio", action="store_true", help="use the original audio even if cleaned exists")
    ap.add_argument("--subs", default="02-work/subs.ass", help="burned-in subtitles (subtitles.py)")
    ap.add_argument("--no-subs", action="store_true")
    ap.add_argument("--fontsdir", help="folder with the subtitle font, if it isn't installed")
    ap.add_argument("--graphics", help="graphics layer with alpha, as long as the edit "
                                       "(default: 02-work/graphics.mov or .webm if present)")
    ap.add_argument("--no-graphics", action="store_true")
    ap.add_argument("--no-sfx", action="store_true")
    args = ap.parse_args()

    edl = json.loads(Path(args.edl).read_text(encoding="utf-8"))
    segs = edl["segments"]
    if not segs:
        die("the edit has no segments")
    out = dict(edl["output"])
    if args.preview:
        scale = min(1.0, 540 / min(out["width"], out["height"]))
        out["width"], out["height"] = (int(out["width"] * scale) // 2 * 2, int(out["height"] * scale) // 2 * 2)
    grade = {}
    if not args.no_grade and Path(args.grade).exists():
        grade = json.loads(Path(args.grade).read_text(encoding="utf-8"))
    probes = {}
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
    dst = Path(args.out or ("02-work/preview.mp4" if args.preview else "03-delivery/edit.mp4"))
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
    graphics = None
    if not args.no_graphics:
        graphics = Path(args.graphics) if args.graphics else next(
            (p for p in (Path("02-work/graphics.mov"), Path("02-work/graphics.webm")) if p.exists()), None)
        if graphics and not graphics.exists():
            die(f"{graphics} doesn't exist")
    sfx, sfx_warn = ([], []) if args.no_sfx else resolve_sfx(segs, out["fps"], edl.get("sfx", []))
    for w in sfx_warn:
        log(f"WARNING: {w}")
    fin = {"overlays": overlays, "music": edl.get("music", []), "total": timeline_total(segs, out["fps"]),
           "subs": subs if subs and subs.exists() else None, "fontsdir": args.fontsdir,
           "graphics": graphics, "sfx": sfx}
    log(f"audio: {'cleaned' if clean else 'original'} · music: {len(fin['music'])} · sfx: {len(sfx)} · "
        f"graphics: {graphics.name if graphics else 'none'} · "
        f"subtitles: {'burned-in' if fin['subs'] else 'none'} · loudness: {args.loudness}")

    pre = dst.with_name(dst.stem + ".premaster" + dst.suffix)
    if len(segs) <= CHUNK:
        total = render_chunk(segs, out, grade, enc, pre, log, args.preview, "render", clean, fin)
    else:
        work = Path("02-work") / "render"
        work.mkdir(parents=True, exist_ok=True)
        parts, total = [], 0.0
        n = math.ceil(len(segs) / CHUNK)
        for i in range(n):
            part = work / f"part_{i:03d}.mkv"
            total += render_chunk(segs[i * CHUNK:(i + 1) * CHUNK], out, grade, INTERMEDIATE, part, log,
                                  args.preview, f"chunk {i + 1}/{n}", clean)
            parts.append(part)
        lst = work / "parts.txt"
        lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
        f_inputs, f_graphs = finish_graph(1, "0:v", "0:a", fin, out, grade)
        ffmpeg_progress(["-f", "concat", "-safe", "0", "-i", str(lst), *f_inputs,
                         "-filter_complex", ";".join(f_graphs), "-map", "[v]", "-map", "[a]", *enc,
                         "-pix_fmt", "yuv420p", *TAGS, "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart",
                         str(pre)], total, "final encode", log)
        for p in parts:
            p.unlink(missing_ok=True)
    master(pre, dst, args.loudness, log)
    pre.unlink(missing_ok=True)

    got = ffprobe(dst)
    vd = float(next(s for s in got["streams"] if s["codec_type"] == "video").get("duration") or 0)
    ad = float(next((s for s in got["streams"] if s["codec_type"] == "audio"), {}).get("duration") or 0)
    log(f"done: {tc(total)} expected · video {tc(vd)} · audio {tc(ad)} · "
        f"{dst.stat().st_size / 1e6:.1f} MB · encoded with {enc_name}")
    if abs(vd - ad) > 1.5 / out["fps"]:
        log(f"WARNING: picture and sound differ by {abs(vd - ad) * 1000:.0f} ms")


if __name__ == "__main__":
    main()
