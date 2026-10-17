# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Framing for every segment of 02-work/edl.json: a virtual camera operator plus punch-ins.

    uv run reframe.py [--punch auto|off] [--zoom 1.12] [--edl 02-work/edl.json]

The crop follows the face like an operator, not like a tracker: inside a dead zone
the camera holds still; when the face leaves it, the camera moves with eased
acceleration and a speed limit, then settles. The path is computed over each
source file's own timeline (so it's continuous across segments from the same
take) and stored per segment as keyframes. Punch-ins: at every jump cut (two
consecutive segments from the same file with something removed between them) the
zoom alternates between 1.0 and --zoom, centered on the face, so the jump reads
as a cut to a closer shot. Writes the result back into edl.json; render.py
applies it.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _common import die  # noqa: E402

RATE = 10            # path samples per second
DEADZONE = 0.12      # share of the crop width/height the face may move without the camera following
OMEGA = 2 * np.pi * 0.7   # spring stiffness (how fast the camera catches up)
MAX_SPEED = 0.35     # crop widths per second
MAX_ACCEL = 0.6      # crop widths per second², so a pan starts and stops gently
FACE_Y = 0.40        # where the face center sits, from the top of the crop, when there's room
HAIR = 0.30          # head above the detected face box, as a share of its height
TIGHT = 0.45         # face height / crop height above which a punch-in is halved


def face_track(samples: list[dict], duration: float) -> tuple[np.ndarray, ...]:
    """Largest face per path sample — center x, center y, height — interpolated to RATE
    (NaN where there's no face for more than 1.5 s: the camera holds there)."""
    t = np.arange(0, duration, 1 / RATE)
    nan = np.full(len(t), np.nan)
    biggest = [max(s["faces"], key=lambda f: f[3]) if s["faces"] else None for s in samples]
    st = np.array([s["t"] for s, b in zip(samples, biggest) if b])
    if not len(st):
        return t, nan, nan.copy(), nan.copy()
    b = np.array([f[:4] for f in biggest if f])
    x = np.interp(t, st, b[:, 0] + b[:, 2] / 2)
    y = np.interp(t, st, b[:, 1] + b[:, 3] / 2)
    h = np.interp(t, st, b[:, 3])
    far = np.array([np.min(np.abs(st - ti)) for ti in t]) > 1.5
    x[far], y[far], h[far] = np.nan, np.nan, np.nan
    return t, x, y, h


def operate(target: np.ndarray, size: float, start: float | None = None) -> np.ndarray:
    """Camera position (crop center, 0-1 of the source) following target like an operator.
    size: crop size on this axis (0-1). The center is kept so the crop stays inside the frame."""
    lo, hi = size / 2, 1 - size / 2
    if hi <= lo:
        return np.full(len(target), 0.5)
    first = target[~np.isnan(target)][0] if (~np.isnan(target)).any() else 0.5
    c = np.clip(start if start is not None else first, lo, hi)
    v, dt, out, chasing = 0.0, 1 / RATE, [], False
    for tg in target:
        if not np.isnan(tg) and abs(tg - c) > size * 0.45:
            # The face is (nearly) out of the crop: an operator wouldn't pan for seconds after
            # it — it's a cut to the new framing.
            c, v, chasing = float(np.clip(tg, lo, hi)), 0.0, False
            out.append(c)
            continue
        if not np.isnan(tg):
            err = tg - c
            # Start moving when the face leaves the dead zone; keep going until it's re-centered.
            if abs(err) > DEADZONE * size:
                chasing = True
            elif abs(err) < DEADZONE * size * 0.25:
                chasing = False
            goal = tg if chasing else c
        else:
            goal = c
        a = OMEGA ** 2 * (goal - c) - 2 * OMEGA * v
        a = float(np.clip(a, -MAX_ACCEL * size, MAX_ACCEL * size))      # eases in instead of jerking
        v = float(np.clip(v + a * dt, -MAX_SPEED * size, MAX_SPEED * size))
        c = float(np.clip(c + v * dt, lo, hi))
        if c in (lo, hi):
            v = 0.0
        out.append(c)
    return np.array(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edl", default="02-work/edl.json")
    ap.add_argument("--ingest", default="01-ingest")
    ap.add_argument("--punch", choices=("auto", "off"), default="auto")
    ap.add_argument("--zoom", type=float, default=1.12, help="punch-in zoom (1.10–1.15 reads as a new shot)")
    args = ap.parse_args()

    edl_path = Path(args.edl)
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    ing = Path(args.ingest)
    faces = json.loads((ing / "faces.json").read_text(encoding="utf-8"))["files"] if (ing / "faces.json").exists() \
        else {}
    material = {m["name"]: m for m in json.loads((ing / "material.json").read_text(encoding="utf-8"))["files"]}
    W, H = edl["output"]["width"], edl["output"]["height"]
    out_aspect = W / H

    # Zoom per segment: alternate at jump cuts within the same file.
    segs = edl["segments"]
    zooms, prev = [], None
    for s in segs:
        z = 1.0
        if args.punch == "auto" and prev is not None and prev["name"] == s["name"] and s["in"] - prev["out"] > 0.05:
            z = args.zoom if zooms[-1] == 1.0 else 1.0
        zooms.append(z)
        prev = s

    paths: dict[tuple[str, float], tuple] = {}
    warnings = set()
    for s, z in zip(segs, zooms):
        m = material.get(s["name"]) or die(f"{s['name']} isn't in material.json")
        sw, sh = m["display_width"], m["display_height"]
        # Crop size (0-1 of the source) for the output aspect at zoom 1, then zoomed.
        cw, ch = (min(1.0, sh * out_aspect / sw), min(1.0, sw / out_aspect / sh))
        if z > 1:
            # A face that already fills the frame gets half the punch-in: closer would be too tight.
            _, _, _, fh_all = face_track(faces.get(s["name"], []), m["duration"])
            seg_h = fh_all[int(s["in"] * RATE):int(s["out"] * RATE) + 1]
            if (~np.isnan(seg_h)).any() and np.nanmedian(seg_h) / ch > TIGHT:
                z = 1 + (z - 1) / 2
        cw, ch = cw / z, ch / z
        if cw * sw < W * 0.98:
            warnings.add(f"{s['name']}: the crop is {cw * sw:.0f} px wide for a {W} px output — upscaled "
                         f"{W / (cw * sw):.2f}×, softer image")
        key = (s["name"], z)
        if key not in paths:
            t, fx, fy, fh = face_track(faces.get(s["name"], []), m["duration"])
            # Vertical target: face center at FACE_Y of the crop, but never so low that the
            # top of the head is cut — the box starts at the brows, hair adds ~30% above.
            hair_top = fy - fh / 2 - HAIR * fh - 0.02
            ty = np.minimum(fy + (0.5 - FACE_Y) * ch, hair_top + ch / 2)
            paths[key] = (t, operate(fx, cw), operate(ty, ch))
        t, px, py = paths[key]
        i0, i1 = int(s["in"] * RATE), int(np.ceil(s["out"] * RATE)) + 1
        keys = [[round(float(t[i] - s["in"]), 3), round(float(px[i]), 4), round(float(py[i]), 4)]
                for i in range(max(i0, 0), min(i1, len(t)))]
        moving = (max(k[1] for k in keys) - min(k[1] for k in keys)) if keys else 0
        s["frame"] = {"zoom": z, "crop": [round(cw, 4), round(ch, 4)], "keys": keys}
        print(f"[{s['in']:7.2f}–{s['out']:7.2f}] {s['name']}  zoom {z:.2f}  "
              f"{'pans ' + format(moving * sw, '.0f') + ' px' if moving * sw > 4 else 'holds still'}")
    for w in sorted(warnings):
        print("WARNING:", w)
    edl_path.write_text(json.dumps(edl, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"framing → {edl_path}")


if __name__ == "__main__":
    main()
