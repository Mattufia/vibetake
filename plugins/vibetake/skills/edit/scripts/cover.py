# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26", "pillow>=10", "opencv-python-headless>=4.9"]
# ///
"""Covers and thumbnails: three concepts, designed for this video, one file per platform.

    uv run cover.py candidates                  # frames worth a cover (face visible, big, sharp) → sheet
    uv run cover.py cutout --time 12.4          # the person cut out of that frame → 02-work/covers/
    uv run cover.py render                      # every concept × platform (Remotion stills) → 03-delivery/
    uv run cover.py preview                     # how they look at real size: profile grid / YouTube feed

The concepts are free canvases: React components written for the video in
02-work/remotion/src/covers/, listed in 02-work/covers.json (see references/covers.md).
`render` checks each one: a face in it, big enough, inside the area the platform's crop
keeps. Without Node/Remotion, covers are drawn as a canvas page like the graphics
(references/from-scratch.md), with the same design.

`candidates` ranks moments with a big, steady face and writes a numbered contact
sheet (02-work/covers/candidates.jpg) to pick from: look for the gesture that
matches the headline, eyes open, mouth not mid-syllable.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from _common import die, filter_path, material_index, pre_filter, run, tc, tool  # noqa: E402

PLATFORMS = {
    # size, core box (x0, y0, x1, y1 as 0-1: what must survive the platform's crop), avoid boxes.
    # Same boxes as KEEP in kit/remotion/src/kit/cover.tsx: change both together.
    "youtube": ((1280, 720), (0.04, 0.06, 0.96, 0.94), [(0.80, 0.82, 1.0, 1.0)]),
    "reels": ((1080, 1920), (0.06, 0.125, 0.94, 0.875), []),
    "tiktok": ((1080, 1920), (0.06, 0.125, 0.94, 0.875), []),
    "shorts": ((1080, 1920), (0.06, 0.125, 0.94, 0.875), []),
}
# ---------- frame and cutout ----------

def grab(video: Path, t: float, out: Path) -> Path:
    """Full-resolution frame at t, with the file's color grade if there is one."""
    gp = Path("02-work/color/grade.json")
    g = (json.loads(gp.read_text(encoding="utf-8")).get(video.name) or {}) if gp.exists() else {}
    # HDR without a grade is still tone-mapped: a cover is never washed out.
    vf = [x for x in (pre_filter(material_index().get(video.name), g),
                      f"lut3d=file='{filter_path(g['lut'])}'" if g.get("lut") else None) if x]
    run([tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
         *(["-vf", ",".join(vf)] if vf else []), str(out)])
    return out


def cut_out(frame: Path, out: Path) -> Path | None:
    """Person without background (rembg birefnet-portrait). None if it can't run here."""
    uvx = shutil.which("uvx")
    if not uvx:
        return None
    print("cutting out the person (rembg birefnet-portrait; the first run downloads the model, ~1 GB)…",
          flush=True)
    p = subprocess.run([uvx, "--python", "3.12", "--from", "rembg[cpu,cli]", "rembg", "i", "-m", "birefnet-portrait", str(frame),
                        str(out)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0 or not out.exists():
        print(f"WARNING: rembg failed ({(p.stderr or p.stdout).strip().splitlines()[-1:]}) — "
              "cover without cutout (or pass --cutout)")
        return None
    return out


# ---------- drawing helpers ----------

# ---------- compose ----------

def guides(img: Image.Image, core: tuple, avoid: list) -> Image.Image:
    g = img.copy()
    d = ImageDraw.Draw(g)
    d.rectangle(core, outline=(0, 255, 0), width=max(2, img.width // 400))
    for ax0, ay0, ax1, ay1 in avoid:
        d.rectangle((ax0 * img.width, ay0 * img.height, ax1 * img.width - 1, ay1 * img.height - 1),
                    outline=(255, 0, 0), width=max(2, img.width // 400))
    return g


# ---------- commands ----------

def cmd_candidates(args) -> None:
    """Moments where a face is visible, big, confidently detected, steady and sharp — inside
    the edit when there is one. Never a back, a far-away figure or a blurred gesture."""
    ing = Path(args.ingest)
    faces = json.loads((ing / "faces.json").read_text(encoding="utf-8"))["files"] if (ing / "faces.json").exists() \
        else {}
    material = {m["name"]: m for m in json.loads((ing / "material.json").read_text(encoding="utf-8"))["files"]}
    edl_p = Path(f"02-work/edl_{args.variant}.json" if args.variant else "02-work/edl.json")
    spans = {}
    if edl_p.exists():
        for sg in json.loads(edl_p.read_text(encoding="utf-8"))["segments"]:
            spans.setdefault(sg["name"], []).append((sg["in"], sg["out"]))
    scored = []
    for name, samples in faces.items():
        for i, smp in enumerate(samples):
            if not smp["faces"] or (spans and not any(a <= smp["t"] <= b for a, b in spans.get(name, []))):
                continue
            x, y, w, h, conf = (max(smp["faces"], key=lambda f: f[3]) + [1.0])[:5]
            if conf < 0.8 or h < 0.05:
                continue                      # unsure or tiny: a back, a figure across the room
            prev = samples[i - 1]["faces"] if i else []
            move = min((abs(p[0] - x) + abs(p[1] - y) for p in prev), default=1.0)
            centered = 1 - abs(x + w / 2 - 0.5)
            scored.append((h * (0.5 + centered) * conf - 2 * move, name, smp["t"], (x, y, w, h)))
    if not scored:
        die("no usable face in the edit (faces.json): pick the frame from the contact sheets")
    scored.sort(key=lambda z: -z[0])
    pool = []
    for z in scored:
        if all(n != z[1] or abs(z[2] - pt) >= args.spacing for _, n, pt, _ in pool):
            pool.append(z)
        if len(pool) == args.n * 2:
            break
    # A face much smaller than the video's best ones is a profile, a back of the head, someone far.
    biggest = max(z[3][3] for z in pool)
    pool = [z for z in pool if z[3][3] >= 0.8 * biggest]
    out = Path("02-work/covers")
    out.mkdir(parents=True, exist_ok=True)
    rated = []
    with tempfile.TemporaryDirectory() as tmp:
        for k, (sc, name, t, box) in enumerate(pool):
            f = grab(Path(material[name]["file"]), t, Path(tmp) / f"{k}.png")
            im = Image.open(f).convert("RGB")
            W, H = im.size
            x, y, w, h = box
            crop = np.asarray(im.crop((int(x * W), int(y * H), int((x + w) * W), int((y + h) * H))).convert("L"),
                              float)
            sharp = float(np.var(crop[1:-1, 1:-1] * 4 - crop[:-2, 1:-1] - crop[2:, 1:-1] - crop[1:-1, :-2]
                                 - crop[1:-1, 2:])) if crop.size > 100 else 0.0
            rated.append([sc, sharp, name, t, box, im])
    top_sharp = max(r[1] for r in rated) or 1.0
    for r in rated:
        r[0] = r[0] * (0.4 + 0.6 * r[1] / top_sharp)
    rated.sort(key=lambda r: -r[0])
    picks = rated[:args.n]
    thumbs = []
    for i, (_, _, name, t, box, im) in enumerate(picks, 1):
        W, H = im.size
        x, y, w, h = box
        d = ImageDraw.Draw(im)
        d.rectangle((x * W, y * H, (x + w) * W, (y + h) * H), outline=(0, 255, 120), width=max(2, W // 300))
        im.thumbnail((360, 360))
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, 150, 28), fill=(0, 0, 0))
        d.text((6, 6), f"{i}  {tc(t)}", fill=(255, 255, 0))
        thumbs.append(im)
    cols = 4
    cw, ch = max(t.width for t in thumbs), max(t.height for t in thumbs)
    sheet = Image.new("RGB", (cols * cw, ((len(thumbs) + cols - 1) // cols) * ch), (20, 20, 20))
    for i, t in enumerate(thumbs):
        sheet.paste(t, ((i % cols) * cw, (i // cols) * ch))
    sheet.save(out / "candidates.jpg", quality=90)
    for i, (_, _, name, t, box, _) in enumerate(picks, 1):
        print(f"{i:2d}. {name} @ {t:.2f} s  (face {box[3] * 100:.0f}% of the height)")
    print(f"contact sheet → {out / 'candidates.jpg'} — choose expression and gesture, not just the score")


def cmd_cutout(args) -> None:
    """Grab the frame (graded) and cut the person out; refuse an empty or broken cut."""
    out = Path("02-work/covers")
    out.mkdir(parents=True, exist_ok=True)
    if args.video:
        video = Path(args.video)
    else:
        mats = json.loads(Path(args.ingest, "material.json").read_text(encoding="utf-8"))["files"]
        vids = [m for m in mats if m.get("kind") == "video"]
        if len(vids) != 1:
            die("several videos: pass --video <file>")
        video = Path(vids[0]["file"])
    tag = f"{args.time:06.2f}"
    frame = grab(video, args.time, out / f"frame_{tag}.png")
    cut = cut_out(frame, out / f"cutout_{tag}.png")
    if not cut:
        die("the cut-out failed: try another candidate frame (or --no-cutout in the concept, if asked)")
    full = Image.open(cut)
    a = np.asarray(full.split()[-1], float) / 255
    cover = float((a > 0.5).mean())
    if not 0.03 <= cover <= 0.9:
        die(f"the cut-out looks wrong ({cover * 100:.0f}% of the frame): try another candidate frame")
    # Trimmed to the person, with the face located in it: the concept places the face, not a box.
    ys, xs = np.nonzero(a > 0.05)
    W, H = full.size
    pad = int(0.01 * max(W, H))
    box = tuple(int(v) for v in (max(0, xs.min() - pad), max(0, ys.min() - pad), min(W, xs.max() + pad), min(H, ys.max() + pad)))
    trimmed = full.crop(box)
    trimmed.save(cut)
    ta = np.asarray(trimmed.split()[-1], float)
    cols = ta.sum(0)
    body_cx = float((cols * np.arange(len(cols))).sum() / max(cols.sum(), 1) / trimmed.width)
    meta = {"width": trimmed.width, "height": trimmed.height, "face": None, "body_cx": round(body_cx, 4),
            "upper_cx": None,
            "touches_bottom": bool(box[3] >= H - 2), "frame": f"frame_{tag}.png"}
    import cv2
    from ingest import yunet
    im = cv2.imread(str(frame))
    _, found = yunet(im.shape[1], im.shape[0]).detect(im)
    if found is not None and len(found):
        fx, fy, fw, fh = [float(v) for v in max(found, key=lambda f: f[3])[:4]]
        meta["face"] = {"x": round((fx - box[0]) / trimmed.width, 4), "y": round((fy - box[1]) / trimmed.height, 4),
                        "w": round(fw / trimmed.width, 4), "h": round(fh / trimmed.height, 4)}
        # The visual weight of what a close-up shows — head and shoulders, down to ~3 face heights
        # below the face — not of an arm that a cover crops out anyway.
        y_end = int(min(trimmed.height, (fy - box[1]) + 4 * fh))
        up = ta[:max(y_end, 1)].sum(0)
        meta["upper_cx"] = round(float((up * np.arange(len(up))).sum() / max(up.sum(), 1) / trimmed.width), 4)
    # A coarse map of the silhouette (share of each cell covered): a concept can center what is
    # actually visible on the canvas, wherever the person is placed and cropped.
    gw = 40
    gh = max(1, round(gw * trimmed.height / trimmed.width))
    small = np.asarray(trimmed.split()[-1].resize((gw, gh), Image.BILINEAR), float) / 255
    meta["grid"] = {"cols": gw, "rows": gh, "cells": [[round(float(v), 2) for v in row] for row in small]}
    (out / f"cutout_{tag}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    plan_p = Path("02-work/covers.json")              # the concepts read it: <Person file=… />
    plan = json.loads(plan_p.read_text(encoding="utf-8")) if plan_p.exists() else {"platforms": [], "concepts": []}
    plan.setdefault("cutouts", {})[f"cutout_{tag}.png"] = meta
    plan_p.write_text(json.dumps(plan, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{out / f'frame_{tag}.png'}\n{cut}  {trimmed.width}×{trimmed.height}, face {meta['face']} "
          f"(0-1 of the cut-out) → cutout_{tag}.json — check hair, hands, edges")


def face_check(img_path: Path, platform: str) -> list[str]:
    """Is there a face, big enough, inside what the platform's crop keeps?"""
    os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
    import cv2
    from ingest import yunet
    im = cv2.imread(str(img_path))
    if im is None:
        return ["couldn't read the cover"]
    H, W = im.shape[:2]
    det = yunet(W, H)
    _, found = det.detect(im)
    if found is None or not len(found):
        return ["no face found — a cover with a person needs their face (or the concept has no person: fine)"]
    x, y, w, h = max(found, key=lambda f: f[3])[:4]
    probs = []
    need = 0.25 if platform == "youtube" else 0.10
    if h / H < need:
        probs.append(f"the face is small ({h / H * 100:.0f}% of the height; aim for {need * 100:.0f}%+): "
                     "it disappears at thumbnail size")
    cy = (y + h / 2) / H
    if platform != "youtube" and not 0.125 <= cy <= 0.875:
        probs.append("the face falls outside the profile grid's 3:4 crop")
    return probs


def cmd_render(args) -> None:
    import graphics
    plan_p = Path("02-work/covers.json")
    if not plan_p.exists():
        die("write 02-work/covers.json (platforms + 3 concepts) and their components first (references/covers.md)")
    plan = json.loads(plan_p.read_text(encoding="utf-8"))
    concepts, platforms = plan.get("concepts", []), plan.get("platforms", [])
    if not concepts or not platforms:
        die("covers.json needs platforms and concepts")
    if len(concepts) < 3:
        print(f"WARNING: {len(concepts)} concept(s): three different ideas give the user a real choice")
    gargs = argparse.Namespace(covers_only=True)
    graphics.cmd_sync(gargs)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path("02-work/covers")
    work.mkdir(parents=True, exist_ok=True)
    count: dict = {}
    for c in concepts:
        # A short's concepts (shorts.py) → cover_<short>_<platform>_<n>.jpg, numbered per short.
        tag = c.get("short") or ""
        count[tag] = n = count.get(tag, 0) + 1
        for pl in c.get("platforms") or platforms:
            png = (work / f"render_{c['id']}_{pl}.png").resolve()
            p = subprocess.run(graphics.remotion("still", f"Cover-{c['id']}-{pl}", str(png), "--image-format=png",
                                                 "--log=error"), cwd=graphics.PROJ)
            if p.returncode or not png.exists():
                die(f"concept {c['id']} ({pl}) didn't render (see above)")
            img = Image.open(png).convert("RGB")
            dst = out_dir / f"cover_{tag + '_' if tag else ''}{pl}_{n}.jpg"
            q = 92
            img.save(dst, quality=q, optimize=True)
            while pl == "youtube" and dst.stat().st_size > 2_000_000 and q > 60:     # YouTube's 2 MB limit
                q -= 8
                img.save(dst, quality=q, optimize=True)
            (_, core, avoid) = PLATFORMS[pl]
            W, H = img.size
            guides(img, (core[0] * W, core[1] * H, core[2] * W, core[3] * H), avoid).save(
                work / f"cover_{tag + '_' if tag else ''}{pl}_{n}.guides.jpg", quality=85)
            print(f"{dst}  ·  concept {c['id']}: {c.get('idea', '')}")
            if not c.get("no_person"):
                for prob in face_check(dst, pl):
                    print(f"WARNING: {dst.name}: {prob}")
    (work / "rendered.json").write_text(json.dumps({"design": design_hash(), "covers": plan_p.read_text(
        encoding="utf-8")}, indent=1), encoding="utf-8")
    print("now: cover.py preview — and look at them at real size")


def design_hash() -> str:
    import hashlib
    d = Path("02-work/design.json")
    return hashlib.sha1(d.read_bytes()).hexdigest()[:12] if d.exists() else ""


def cmd_preview(args) -> None:
    """The covers where people meet them: a profile grid (vertical) or a YouTube feed, at phone size."""
    out = Path("02-work/covers")
    out.mkdir(parents=True, exist_ok=True)
    rendered = out / "rendered.json"
    if rendered.exists() and json.loads(rendered.read_text(encoding="utf-8")).get("design") != design_hash():
        print("WARNING: the video's design (design.json) changed after the covers were rendered: cover.py render again")
    made = []
    for pl in ("reels", "tiktok", "shorts", "youtube"):
        files = sorted(Path(args.out_dir).glob(f"cover_{args.variant + '_' if args.variant else ''}{pl}_*.jpg"))
        if not files:
            continue
        if pl == "youtube":
            cw, chh = 400, 225
            canvas = Image.new("RGB", (cw * 3 + 40, chh + 90), (15, 15, 15))
            d = ImageDraw.Draw(canvas)
            for i in range(3):
                x = 10 + i * (cw + 10)
                if i < len(files):
                    canvas.paste(Image.open(files[i]).convert("RGB").resize((cw, chh), Image.LANCZOS), (x, 10))
                else:
                    d.rectangle((x, 10, x + cw, 10 + chh), fill=(45, 45, 45))
                d.rectangle((x, chh + 22, x + cw * 0.9, chh + 36), fill=(70, 70, 70))
                d.rectangle((x, chh + 46, x + cw * 0.55, chh + 58), fill=(50, 50, 50))
        else:
            cw, chh = 360, 480                       # the profile grid shows the central 3:4
            canvas = Image.new("RGB", (cw * 3 + 8, chh * 2 + 8), (0, 0, 0))
            d = ImageDraw.Draw(canvas)
            for i in range(6):
                x, y = (i % 3) * (cw + 4), (i // 3) * (chh + 4)
                if i < len(files):
                    im = Image.open(files[i]).convert("RGB")
                    W, H = im.size
                    top = int(H * 0.125)
                    canvas.paste(im.crop((0, top, W, H - top)).resize((cw, chh), Image.LANCZOS), (x, y))
                else:
                    d.rectangle((x, y, x + cw, y + chh), fill=(38, 38, 38))
        dst = out / f"preview_{args.variant + '_' if args.variant else ''}{pl}.png"
        canvas.save(dst)
        made.append(dst)
        print(f"{dst}  (look at it the size a phone shows it: does each read in a second?)")
    if not made:
        die("no covers in 03-delivery yet: cover.py render")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ingest", default="01-ingest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("candidates")
    c.add_argument("--n", type=int, default=12)
    c.add_argument("--spacing", type=float, default=1.5, help="seconds between picks from the same file")
    c.add_argument("--variant", help="a short or variant: frames from its own cut")
    k = sub.add_parser("cutout")
    k.add_argument("--time", type=float, required=True)
    k.add_argument("--video")
    r = sub.add_parser("render")
    r.add_argument("--out-dir", default="03-delivery")
    pv = sub.add_parser("preview")
    pv.add_argument("--out-dir", default="03-delivery")
    pv.add_argument("--variant", help="a short's covers (cover_<name>_…)")
    args = ap.parse_args()
    {"candidates": cmd_candidates, "cutout": cmd_cutout, "render": cmd_render,
     "preview": cmd_preview}[args.cmd](args)


if __name__ == "__main__":
    main()
