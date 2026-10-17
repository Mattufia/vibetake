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
from _timeline import resolve_broll  # noqa: E402

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


def framing(k: int, seg: dict, dur: float, fps: float, workdir: Path) -> str:
    """Crop for the segment: the operator path from reframe.py, sent frame by frame to the
    crop filter; or a plain center crop to the output aspect when there's no framing."""
    fr = seg.get("frame")
    if not fr:
        return ""
    sw, sh = display_dims(seg["_probe"])
    cw, ch = round(fr["crop"][0] * sw / 2) * 2, round(fr["crop"][1] * sh / 2) * 2
    keys = fr["keys"] or [[0, 0.5, 0.5]]
    kt = [k_[0] for k_ in keys]
    lines, x0, y0 = [], None, None
    for i in range(frames(dur, fps) + 1):
        t = i / fps
        cx = float(np.interp(t, kt, [k_[1] for k_ in keys]))
        cy = float(np.interp(t, kt, [k_[2] for k_ in keys]))
        x = int(min(max(cx * sw - cw / 2, 0), sw - cw))
        y = int(min(max(cy * sh - ch / 2, 0), sh - ch))
        if x0 is None:
            x0, y0 = x, y
        lines.append(f"{t:.4f} crop@rf{k} x {x}, crop@rf{k} y {y};")
    cmd = workdir / f"frame_{k:03d}.cmd"
    cmd.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"sendcmd=f='{filter_path(cmd)}',crop@rf{k}=w={cw}:h={ch}:x={x0}:y={y0}"


def segment_graph(k: int, seg: dict, out: dict, grade: dict, has_audio: bool, preview: bool,
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
    if has_audio:
        a = (f"[{k}:a:{seg.get('track', 0)}]aformat=sample_rates=48000:channel_layouts=stereo,"
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


def render_chunk(segs: list[dict], out: dict, grade: dict, enc: list[str], dst: Path, log: Log,
                 preview: bool, label: str, overlays: list[dict] | None = None) -> float:
    inputs, graphs, total = [], [], 0.0
    with tempfile.TemporaryDirectory(prefix="vibetake-render-") as tmp:
        workdir = Path(tmp)
        for k, s in enumerate(segs):
            fps = out["fps"]
            dur = frames(s["out"] - s["in"], fps) / fps
            inputs += ["-ss", f"{s['in']:.4f}", "-t", f"{dur + 0.25:.4f}", "-i", s["source"]]
            has_audio = any(st.get("codec_type") == "audio" for st in s["_probe"]["streams"])
            gph, d = segment_graph(k, s, out, grade, has_audio, preview, workdir)
            graphs.append(gph)
            total += d
        joined = "".join(f"[v{k}][a{k}]" for k in range(len(segs)))
        graphs.append(f"{joined}concat=n={len(segs)}:v=1:a=1[vc][a]")
        b_inputs, b_graphs, last = broll_graph(len(segs), "vc", overlays or [], out, grade)
        inputs += b_inputs
        graphs += b_graphs + [f"[{last}]null[v]"]
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

    overlays, warnings = resolve_broll(segs, out["fps"], edl.get("broll", []))
    for w in warnings:
        log(f"WARNING: {w}")
    if overlays:
        log(f"b-roll: {len(overlays)} clip(s) over {sum(b['dur'] for b in overlays):.1f} s")

    if len(segs) <= CHUNK:
        total = render_chunk(segs, out, grade, enc, dst, log, args.preview, "render", overlays)
    else:
        work = Path("02-work") / "render"
        work.mkdir(parents=True, exist_ok=True)
        parts, total = [], 0.0
        n = math.ceil(len(segs) / CHUNK)
        for i in range(n):
            part = work / f"part_{i:03d}.mkv"
            total += render_chunk(segs[i * CHUNK:(i + 1) * CHUNK], out, grade, INTERMEDIATE, part, log,
                                  args.preview, f"chunk {i + 1}/{n}")
            parts.append(part)
        lst = work / "parts.txt"
        lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts), encoding="utf-8")
        b_inputs, b_graphs, last = broll_graph(1, "0:v", overlays, out, grade)
        overlay_args = (["-filter_complex", ";".join(b_graphs + [f"[{last}]null[v]"]), "-map", "[v]", "-map", "0:a"]
                        if overlays else [])
        ffmpeg_progress(["-f", "concat", "-safe", "0", "-i", str(lst), *b_inputs, *overlay_args, *enc,
                         "-pix_fmt", "yuv420p", *TAGS, "-c:a", "aac", "-b:a", "320k", "-movflags", "+faststart",
                         str(dst)], total, "final encode", log)
        for p in parts:
            p.unlink(missing_ok=True)

    got = ffprobe(dst)
    vd = float(next(s for s in got["streams"] if s["codec_type"] == "video").get("duration") or 0)
    ad = float(next((s for s in got["streams"] if s["codec_type"] == "audio"), {}).get("duration") or 0)
    log(f"done: {tc(total)} expected · video {tc(vd)} · audio {tc(ad)} · "
        f"{dst.stat().st_size / 1e6:.1f} MB · encoded with {enc_name}")
    if abs(vd - ad) > 1.5 / out["fps"]:
        log(f"WARNING: picture and sound differ by {abs(vd - ad) * 1000:.0f} ms")


if __name__ == "__main__":
    main()
