# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Helpers for this video's design: the colors that are already in it, and contrast.

    uv run design.py palette [--n 6]        # dominant colors of the edit → 02-work/palette.png
    uv run design.py contrast "#FFD400" "#0E1630"

palette samples frames across the edit (graded, as the viewer sees them), clusters
their colors and prints them by share, with luminance and saturation, plus the most
saturated one as an accent candidate and its complement. A design that borrows from
the footage looks like it belongs to the video; one that ignores it looks pasted on.
contrast gives the WCAG ratio: text needs ≥4.5 (≥3 for very large headlines) against
what's behind it.
"""
from __future__ import annotations

import argparse
import colorsys
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _common import die, filter_path, tool, wcag_luminance as luminance  # noqa: E402


def hex_to_rgb(h: str) -> np.ndarray:
    h = h.strip().lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], float)


def rgb_to_hex(c) -> str:
    return "#" + "".join(f"{int(round(max(0, min(255, v)))):02X}" for v in c)


def contrast(a: np.ndarray, b: np.ndarray) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def sample_pixels(n_frames: int = 24) -> np.ndarray:
    edl = json.loads(Path("02-work/edl.json").read_text(encoding="utf-8")) if Path("02-work/edl.json").exists() else None
    grade = {}
    if Path("02-work/color/grade.json").exists():
        grade = json.loads(Path("02-work/color/grade.json").read_text(encoding="utf-8"))
    if edl:
        moments = []
        total = sum(s["out"] - s["in"] for s in edl["segments"])
        for k in range(n_frames):
            t = (k + 0.5) / n_frames * total
            for s in edl["segments"]:
                d = s["out"] - s["in"]
                if t <= d:
                    moments.append((s["source"], s.get("name", Path(s["source"]).name), s["in"] + t))
                    break
                t -= d
    else:
        raw = sorted(p for p in Path("00-raw").rglob("*") if p.suffix.lower() in (".mp4", ".mov", ".mkv", ".mts"))
        if not raw:
            die("no edit and no footage to read colors from")
        moments = [(str(raw[0]), raw[0].name, 1.0 + k) for k in range(n_frames)]
    px = []
    for src, name, t in moments:
        g = grade.get(name) or {}
        vf = [x for x in (g.get("pre"), f"lut3d=file='{filter_path(g['lut'])}'" if g.get("lut") else None) if x]
        vf.append("scale=64:64")
        raw = subprocess.run([tool("ffmpeg"), "-v", "error", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1", "-vf",
                              ",".join(vf), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        if len(raw) == 64 * 64 * 3:
            px.append(np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(float))
    if not px:
        die("couldn't read frames")
    return np.concatenate(px)


def kmeans(x: np.ndarray, k: int, iters: int = 25) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(3)
    c = x[rng.choice(len(x), k, replace=False)]
    for _ in range(iters):
        lab = np.argmin(((x[:, None, :] - c[None]) ** 2).sum(2), 1)
        c = np.array([x[lab == i].mean(0) if (lab == i).any() else c[i] for i in range(k)])
    return c, np.bincount(lab, minlength=k) / len(x)


def cmd_palette(args) -> None:
    x = sample_pixels()
    cents, share = kmeans(x, args.n)
    order = np.argsort(-share)
    rows = []
    for i in order:
        r, g, b = cents[i] / 255
        h, l, s = colorsys.rgb_to_hls(r, g, b)
        rows.append({"hex": rgb_to_hex(cents[i]), "share": round(float(share[i]), 3), "lum": round(luminance(cents[i]), 3),
                     "sat": round(s, 2), "hue": round(h * 360)})
    accent = max(rows, key=lambda r: r["sat"] * (0.3 + min(r["lum"], 0.6)))
    ah = accent["hue"] / 360
    comp = colorsys.hls_to_rgb((ah + 0.5) % 1, 0.55, 0.85)
    for r in rows:
        print(f"{r['hex']}  {r['share'] * 100:5.1f}%  lum {r['lum']:.2f}  sat {r['sat']:.2f}  hue {r['hue']:3d}°")
    print(f"accent candidate (most saturated in the footage): {accent['hex']} · complement {rgb_to_hex(np.array(comp) * 255)}")
    # Swatch image for looking at it.
    w = 120
    img = np.zeros((90, w * len(rows), 3), np.uint8)
    for k, r in enumerate(rows):
        img[:, k * w:(k + 1) * w] = hex_to_rgb(r["hex"])
    out = Path("02-work/palette.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([tool("ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{img.shape[1]}x90",
                    "-i", "-", "-frames:v", "1", "-update", "1", str(out)], input=img.tobytes(), check=True)
    (out.with_suffix(".json")).write_text(json.dumps({"colors": rows, "accent": accent["hex"]}, indent=1), encoding="utf-8")
    print(f"swatches → {out}")


def cmd_contrast(args) -> None:
    r = contrast(hex_to_rgb(args.a), hex_to_rgb(args.b))
    verdict = "OK for text" if r >= 4.5 else "OK only for very large headlines" if r >= 3 else "too low"
    print(f"{args.a} on {args.b}: {r:.2f}:1 — {verdict}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("palette")
    p.add_argument("--n", type=int, default=6)
    c = sub.add_parser("contrast")
    c.add_argument("a")
    c.add_argument("b")
    args = ap.parse_args()
    {"palette": cmd_palette, "contrast": cmd_contrast}[args.cmd](args)


if __name__ == "__main__":
    main()
