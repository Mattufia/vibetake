# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""AI upscaling (Real-ESRGAN) for the segments whose crop is smaller than the output.

    uv run upscale.py list                       # segments that are upscaled, and by how much
    uv run upscale.py run --seg 3 --test 2       # 2 s trial: time per frame, estimate, comparison still
    uv run upscale.py run --seg 3                # the whole segment
    uv run upscale.py clear [--seg 3]

A 1080p horizontal shot cropped to vertical keeps ~608 px of width, stretched ~1.8×.
This renders the segment already graded and framed at its crop size, upscales every
frame ×4 with realesrgan-ncnn-vulkan (`realesrgan-x4plus`, the model for real footage;
the `anime` ones are for drawings; it runs on any GPU through Vulkan), brings it down
to the output size with lanczos, and stores it as the segment's picture: render.py
uses it instead of grading and framing the original (the sound still comes from the
original). Only for segments that need it and only once they're final — it's slow and
the frames take a lot of disk. If the edit, the framing or the grade of the segment
change, render.py ignores the stale file and says so.

Check the comparison still (02-work/upscale/seg_<n>_compare.png, plain stretch on the
left, AI on the right): if skin looks plastic or detail looks invented, don't upscale
that segment (`clear --seg n`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import HOME, die, ffmpeg_progress, ffprobe, filter_path, Log, run, tool  # noqa: E402

MODEL = "realesrgan-x4plus"
WORK = Path("02-work/upscale")


def esrgan() -> str:
    found = shutil.which("realesrgan-ncnn-vulkan")
    if found:
        return found
    for p in (HOME / "tools" / "realesrgan").glob("realesrgan-ncnn-vulkan*"):
        if p.suffix.lower() in ("", ".exe"):
            return str(p)
    die("realesrgan-ncnn-vulkan not found: install it (github.com/xinntao/Real-ESRGAN, release "
        "'realesrgan-ncnn-vulkan' for this OS) on the PATH or in ~/.vibetake/tools/realesrgan/")


def signature(seg: dict, grade: dict, out: dict) -> str:
    """What the upscaled picture depends on; a change makes it stale."""
    key = {k: seg.get(k) for k in ("source", "in", "out", "frame")}
    key["grade"] = grade.get(seg.get("name", Path(seg["source"]).name))
    key["out"] = [out["width"], out["height"], out["fps"]]
    return hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]


def crop_px(seg: dict, probe: dict) -> tuple[int, int]:
    from render import display_dims
    sw, sh = display_dims(probe)
    fr = seg.get("frame") or {}
    c = fr.get("crop") or [1.0, 1.0]
    return max(2, round(c[0] * sw / 2) * 2), max(2, round(c[1] * sh / 2) * 2)


def load_all(edl_path: Path, grade_path: Path) -> tuple[dict, dict]:
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    grade = json.loads(grade_path.read_text(encoding="utf-8")) if grade_path.exists() else {}
    return edl, grade


def cmd_list(args) -> None:
    edl, _ = load_all(Path(args.edl), Path(args.grade))
    W = edl["output"]["width"]
    probes = {}
    any_ = False
    for k, s in enumerate(edl["segments"]):
        probes.setdefault(s["source"], ffprobe(Path(s["source"])))
        cw, ch = crop_px(s, probes[s["source"]])
        factor = W / cw
        if factor > 1.05:
            any_ = True
            done = " · upscaled" if s.get("upscaled") else ""
            print(f"seg {k}: {s['out'] - s['in']:.1f} s, crop {cw}×{ch} → {W} px wide ({factor:.2f}×){done}")
    if not any_:
        print("no segment is stretched: nothing to upscale")


def cmd_run(args) -> None:
    import render
    edl_path = Path(args.edl)
    edl, grade = load_all(edl_path, Path(args.grade))
    segs, out = edl["segments"], edl["output"]
    if not 0 <= args.seg < len(segs):
        die(f"--seg must be between 0 and {len(segs) - 1}")
    s = segs[args.seg]
    s["_probe"] = ffprobe(Path(s["source"]))
    W, H, fps = out["width"], out["height"], out["fps"]
    cw, ch = crop_px(s, s["_probe"])
    if W / cw <= 1.05:
        die(f"segment {args.seg} isn't stretched ({cw} px crop for {W} px): nothing to gain")
    full = render.frames(s["out"] - s["in"], fps) / fps
    dur = min(full, args.test) if args.test else full
    exe = esrgan()
    WORK.mkdir(parents=True, exist_ok=True)
    log = Log(Path("02-work") / "upscale.log")
    with tempfile.TemporaryDirectory(prefix="vibetake-upscale-", dir=str(WORK)) as tmp:
        tmp = Path(tmp)
        src_dir, up_dir = tmp / "in", tmp / "out"
        src_dir.mkdir()
        up_dir.mkdir()
        g = grade.get(s.get("name", Path(s["source"]).name)) or {}
        chain = [x for x in (g.get("pre"), f"lut3d=file='{filter_path(g['lut'])}'" if g.get("lut") else None) if x]
        chain.append(render.framing(0, s, dur, fps, tmp) or f"crop='min(iw,ih*{W}/{H})':'min(ih,iw*{H}/{W})'")
        chain += [f"scale={cw}:{ch}:flags=lanczos", f"fps={fps}", f"trim=duration={dur:.6f}", "format=rgb24"]
        log(f"seg {args.seg}: {dur:.1f} s, crop {cw}×{ch} → ×4 → {W}×{H}")
        ffmpeg_progress(["-ss", f"{s['in']:.4f}", "-t", f"{dur + 0.25:.4f}", "-i", s["source"], "-vf",
                         ",".join(chain), "-an", str(src_dir / "%06d.png")], dur, "frames out", log)
        n = len(list(src_dir.glob("*.png")))
        t0 = time.time()
        log(f"upscaling {n} frames with {MODEL} (Vulkan)…")
        p = subprocess.run([exe, "-i", str(src_dir), "-o", str(up_dir), "-n", MODEL, "-f", "png"],
                           capture_output=True, text=True, errors="replace")
        if p.returncode != 0 or len(list(up_dir.glob("*.png"))) < n:
            die(f"realesrgan failed: {(p.stderr or p.stdout).strip()[-300:]}")
        per = (time.time() - t0) / max(n, 1)
        log(f"{per:.2f} s per frame" + (f" → the whole segment ({full:.1f} s) would take ~"
                                         f"{per * full * fps / 60:.1f} min" if args.test else ""))
        # Comparison still: plain stretch vs AI, middle frame, at output size.
        mid = sorted(src_dir.glob("*.png"))[n // 2].name
        cmp_path = WORK / f"seg_{args.seg}_compare.png"
        run([tool("ffmpeg"), "-v", "error", "-y", "-i", str(src_dir / mid), "-i", str(up_dir / mid),
             "-filter_complex", f"[0]scale={W}:{H}:flags=lanczos[a];[1]scale={W}:{H}:flags=lanczos[b];"
             "[a][b]hstack", "-frames:v", "1", str(cmp_path)])
        log(f"comparison (stretch | AI) → {cmp_path}: check skin, hair and edges")
        if args.test:
            log("trial only: nothing stored. Run without --test to upscale the segment.")
            return
        dst = WORK / f"seg_{args.seg}.mkv"
        ffmpeg_progress(["-framerate", str(fps), "-i", str(up_dir / "%06d.png"), "-vf",
                         f"scale={W}:{H}:flags=lanczos,format=yuv420p", "-c:v", "libx264", "-preset", "medium",
                         "-crf", "10", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
                         str(dst)], dur, "encode", log)
    s.pop("_probe", None)
    s["upscaled"] = {"file": str(dst.resolve()), "sig": signature(s, grade, out), "model": MODEL}
    edl_path.write_text(json.dumps(edl, indent=1, ensure_ascii=False), encoding="utf-8")
    log(f"seg {args.seg} upscaled → {dst}; render.py will use it")


def cmd_clear(args) -> None:
    edl_path = Path(args.edl)
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    n = 0
    for k, s in enumerate(edl["segments"]):
        if (args.seg is None or k == args.seg) and s.pop("upscaled", None):
            n += 1
            (WORK / f"seg_{k}.mkv").unlink(missing_ok=True)
    edl_path.write_text(json.dumps(edl, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"removed {n} upscaled segment(s)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edl", default="02-work/edl.json")
    ap.add_argument("--grade", default="02-work/color/grade.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    r = sub.add_parser("run")
    r.add_argument("--seg", type=int, required=True)
    r.add_argument("--test", type=float, help="seconds for a trial (time estimate and comparison only)")
    c = sub.add_parser("clear")
    c.add_argument("--seg", type=int)
    args = ap.parse_args()
    {"list": cmd_list, "run": cmd_run, "clear": cmd_clear}[args.cmd](args)


if __name__ == "__main__":
    main()
