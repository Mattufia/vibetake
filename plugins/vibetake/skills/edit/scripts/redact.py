# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26", "opencv-python-headless>=4.9"]
# ///
"""Blur or pixelate something in a segment (a face, a plate, a logo, a screen) and follow it.

    uv run redact.py 3 --show [--at 12.4]                  # a frame with a 0-1 grid, to read the box off
    uv run redact.py 3 --box 0.62,0.18,0.10,0.16 [--at 12.4] [--mode blur|pixelate] [--static]
    uv run redact.py 3 --clear                             # removes segment 3's redactions

Segments are numbered from 1 as in edl.json. --box is x,y,w,h of the thing (0-1 of the source
picture) at --at (source seconds, default the segment's in). The box is tracked forward and back
over the whole segment (--static: it stays put, for something that doesn't move) and stored in the
segment's "redact" list; render.py blurs it, a little bigger than the box. Check the contact
sheet it writes (02-work/redact_<n>.jpg: the box drawn on frames across the segment): where the
tracker lost it, give another --box at that moment for the stretch after it, or use --static.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _common import die, tool  # noqa: E402

W = 640          # tracking width
KEY_FPS = 10     # keys stored per second (render.py interpolates between them)


def frames(src: str, a: float, b: float) -> tuple[list[np.ndarray], float]:
    """The source's frames between a and b at W px wide (ffmpeg turns them upright like render.py)."""
    info = json.loads(subprocess.run([tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                                      "stream=r_frame_rate", "-of", "json", src], capture_output=True,
                                     text=True, check=True).stdout)["streams"][0]
    num, den = info["r_frame_rate"].split("/")
    fps = float(num) / float(den)
    raw = subprocess.run([tool("ffmpeg"), "-v", "error", "-ss", f"{a:.4f}", "-t", f"{b - a:.4f}", "-i", src,
                          "-vf", f"scale={W}:-2", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                         capture_output=True, check=True).stdout
    probe = subprocess.run([tool("ffmpeg"), "-v", "error", "-ss", f"{a:.4f}", "-i", src, "-frames:v", "1",
                            "-vf", f"scale={W}:-2", "-f", "image2pipe", "-c:v", "png", "-"], capture_output=True, check=True).stdout
    h = cv2.imdecode(np.frombuffer(probe, np.uint8), cv2.IMREAD_COLOR).shape[0]
    n = len(raw) // (W * h * 3)
    return [np.frombuffer(raw, np.uint8, W * h * 3, i * W * h * 3).reshape(h, W, 3) for i in range(n)], fps


def track(imgs: list[np.ndarray], box: tuple) -> tuple[list, int]:
    """MIL tracker from the first image on: a box per image, and how many it lost (kept the last box)."""
    t = cv2.TrackerMIL_create()
    t.init(imgs[0], tuple(int(v) for v in box))
    out, lost = [box], 0
    for im in imgs[1:]:
        ok, b = t.update(im)
        lost += not ok
        out.append(tuple(b) if ok else out[-1])
    return out, lost


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("segment", type=int)
    ap.add_argument("--box")
    ap.add_argument("--at", type=float)
    ap.add_argument("--mode", choices=["blur", "pixelate"], default="blur")
    ap.add_argument("--static", action="store_true")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--clear", action="store_true")
    ap.add_argument("--edl", default="02-work/edl.json")
    args = ap.parse_args()
    edl_p = Path(args.edl)
    edl = json.loads(edl_p.read_text(encoding="utf-8"))
    if not 1 <= args.segment <= len(edl["segments"]):
        die(f"segment {args.segment}: the edl has {len(edl['segments'])}")
    seg = edl["segments"][args.segment - 1]
    at = seg["in"] if args.at is None else args.at
    if not seg["in"] <= at <= seg["out"]:
        die(f"--at {at} is outside segment {args.segment} ({seg['in']:.2f}–{seg['out']:.2f} s of the source)")

    if args.clear:
        seg.pop("redact", None)
        edl_p.write_text(json.dumps(edl, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"segment {args.segment}: no redactions")
        return
    if args.show:
        img = frames(seg["source"], at, at + 0.05)[0][0].copy()
        h = img.shape[0]
        for i in range(1, 10):
            cv2.line(img, (W * i // 10, 0), (W * i // 10, h), (0, 255, 255), 1)
            cv2.line(img, (0, h * i // 10), (W, h * i // 10), (0, 255, 255), 1)
            cv2.putText(img, f".{i}", (W * i // 10 + 2, 12), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1)
            cv2.putText(img, f".{i}", (2, h * i // 10 - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 255, 255), 1)
        out = edl_p.parent / f"redact_{args.segment}_frame.jpg"
        cv2.imwrite(str(out), img)
        print(f"{out}  (segment {args.segment} at {at:.2f} s, grid every 0.1)")
        return
    if not args.box:
        die("--box x,y,w,h (0-1), or --show to read it off a frame")
    x, y, w, h = (float(v) for v in args.box.split(","))

    entry = {"mode": args.mode}
    if args.static:
        entry["box"] = [x, y, w, h]
        keys = [[seg["in"], x, y, w, h], [seg["out"], x, y, w, h]]
        lost = 0
    else:
        back, _ = frames(seg["source"], seg["in"], at)
        fwd, fps = frames(seg["source"], at, seg["out"])
        if not fwd:
            die("couldn't read the segment's frames")
        ih = fwd[0].shape[0]
        px = (x * W, y * ih, w * W, h * ih)
        ahead, l1 = track(fwd, px)
        behind, l2 = track([fwd[0], *back[::-1]], px) if back else ([px], 0)
        boxes = behind[:0:-1] + ahead          # in time order, from the segment's in
        t0 = at - (len(behind) - 1) / fps
        step = max(1, round(fps / KEY_FPS))
        keys = [[round(t0 + i / fps, 3), *(round(v, 4) for v in (b[0] / W, b[1] / ih, b[2] / W, b[3] / ih))]
                for i, b in enumerate(boxes) if i % step == 0 or i == len(boxes) - 1]
        entry["keys"] = keys
        lost = l1 + l2
    seg.setdefault("redact", []).append(entry)
    edl_p.write_text(json.dumps(edl, indent=2, ensure_ascii=False), encoding="utf-8")

    # The check: the box (as render.py will grow it) on 6 frames across the segment.
    shots = []
    for tt in np.linspace(seg["in"], seg["out"] - 0.05, 6):
        im = frames(seg["source"], tt, tt + 0.05)[0][0].copy()
        ih = im.shape[0]
        kt = [k[0] for k in keys]
        bx, by, bw, bh = (float(np.interp(tt, kt, [k[i] for k in keys])) for i in range(1, 5))
        cv2.rectangle(im, (int((bx - bw * 0.1) * W), int((by - bh * 0.1) * ih)),
                      (int((bx + bw * 1.1) * W), int((by + bh * 1.1) * ih)), (0, 0, 255), 2)
        cv2.putText(im, f"{tt:.2f}s", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        shots.append(im)
    sheet = np.vstack([np.hstack(shots[:3]), np.hstack(shots[3:])])
    out = edl_p.parent / f"redact_{args.segment}.jpg"
    cv2.imwrite(str(out), sheet)
    warn = f" · WARNING: lost it on {lost} frames (kept the last box) — check the sheet" if lost else ""
    print(f"segment {args.segment}: {args.mode} {'fixed' if args.static else f'tracked ({len(keys)} keys)'}"
          f" · check {out}{warn}")


if __name__ == "__main__":
    main()
