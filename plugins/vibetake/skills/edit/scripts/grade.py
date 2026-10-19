# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26", "scipy>=1.11"]
# ///
"""Color: measure, build the grade as a .cube, preview before/after.

    uv run grade.py analyze [files...] [--ingest 01-ingest] [--out 02-work/color]
    uv run grade.py propose [files...]   # Basic and Full side by side + previews + recommendation
    uv run grade.py build   [files...] [--level none|basic|full] [--look cinematic --intensity 60]
                            [--input-lut <format>.cube] [--no-match] [--match-target <file>] [--out 02-work/color]
                            [--creative-lut <user>.cube] [--ambient balanced|neutral|keep]
    uv run grade.py preview [files...] [--out 02-work/color]

Without files, uses every video in 01-ingest/material.json. Order inside the grade:
input transform (HDR tone mapping as a pre-filter; a log format's LUT) → shot matching
(match.py, with several files; also at level none) → correction (white balance and
exposure; one shared correction for the matched shots; for a single log file, done in
log before the LUT) → look → Rec.709. Everything after the HDR pre-filter is baked into one .cube per file and
applied with ffmpeg's lut3d; grade.json says, per file, which pre-filter and LUT to
use at render time. Always the whole frame: faces only serve to measure skin.

Looks: natural, cinematic, warm, cold, bw, vintage (intensity 0-100).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _common import HOME, die, ffprobe, filter_path, find_font, pick_tonemap, run, sdr_source, tc, tool  # noqa: E402

GAMMA = 2.4                  # display-referred math (BT.1886)
LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)
SKIN_LINE_DEG = 123.0        # where skin of any color sits on a vectorscope (the "I line")
STOP_IN_LOG = 0.08           # approx. code-value change per stop in common log curves
CAST_STRONG, CAST_MILD = 6.0, 3.0   # chroma (Lab) of what should be neutral
# Ambient: temperature runs along the daylight/tungsten line (blue ↔ amber) in a*b*, tint across it
# (green ↔ magenta). balanced keeps the temperature (eases it past TEMP_KEEP) and removes the tint.
TEMP_DIR = np.array([0.2, 1.0]) / math.hypot(0.2, 1.0)
TINT_DIR = np.array([1.0, -0.2]) / math.hypot(1.0, -0.2)
TEMP_KEEP = 12.0
AMBIENTS = ("balanced", "neutral", "keep")
LEVELS_STRENGTH = 0.7               # how far milky blacks / dull whites are pulled to full range
SKIN_MAX_SHIFT = 8.0                # degrees; global skin correction, so kept small
LEVELS = ("none", "basic", "full")
MAX_STOPS = 1.0
LUT_SIZE = 33
LOOKS = ("natural", "cinematic", "warm", "cold", "bw", "vintage")
# Optional repairs, before the correction (pre-filters, like HDR tone mapping).
DENOISE = {"light": "hqdn3d=2:1.5:3:3", "medium": "hqdn3d=4:3:6:4.5"}
MOIRE = "format=yuv444p16le,gblur=sigma=10:planes=6"      # blur only the chroma planes (U, V)


# --------------------------------------------------------------------- LUTs

def read_cube(path: Path) -> np.ndarray:
    """Returns table[b, g, r, 3] (the .cube format varies red fastest)."""
    size, rows, dmin, dmax = 0, [], np.zeros(3), np.ones(3)
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        p = line.strip().split()
        if not p or p[0].startswith("#"):
            continue
        if p[0] == "LUT_3D_SIZE":
            size = int(p[1])
        elif p[0] == "DOMAIN_MIN":
            dmin = np.array(p[1:4], float)
        elif p[0] == "DOMAIN_MAX":
            dmax = np.array(p[1:4], float)
        elif p[0][0] in "-.0123456789" and len(p) == 3:
            rows.append([float(x) for x in p])
    if not size or len(rows) != size ** 3:
        die(f"{path}: not a 3D .cube LUT")
    if np.any(dmin != 0) or np.any(dmax != 1):
        die(f"{path}: only 0-1 domain LUTs are supported")
    return np.array(rows, np.float32).reshape(size, size, size, 3)


def apply_cube(rgb: np.ndarray, table: np.ndarray) -> np.ndarray:
    """Trilinear interpolation; rgb in 0-1, any leading shape."""
    n = table.shape[0]
    x = np.clip(rgb, 0, 1) * (n - 1)
    i0 = np.floor(x).astype(int).clip(0, n - 2)
    f = x - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
    c = lambda db, dg, dr: table[b0 + db, g0 + dg, r0 + dr]  # noqa: E731
    c00 = c(0, 0, 0) * (1 - fr) + c(0, 0, 1) * fr
    c01 = c(0, 1, 0) * (1 - fr) + c(0, 1, 1) * fr
    c10 = c(1, 0, 0) * (1 - fr) + c(1, 0, 1) * fr
    c11 = c(1, 1, 0) * (1 - fr) + c(1, 1, 1) * fr
    return ((c00 * (1 - fg) + c01 * fg) * (1 - fb) + (c10 * (1 - fg) + c11 * fg) * fb).astype(np.float32)


def write_cube(path: Path, fn, title: str, size: int = LUT_SIZE) -> None:
    v = np.linspace(0, 1, size, dtype=np.float32)
    b, g, r = np.meshgrid(v, v, v, indexing="ij")
    out = np.clip(fn(np.stack([r, g, b], -1).reshape(-1, 3)), 0, 1)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(f"{x:.6f} {y:.6f} {z:.6f}" for x, y, z in out)
    path.write_text(f'TITLE "{title}"\nLUT_3D_SIZE {size}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n{body}\n',
                    encoding="utf-8")


# ---------------------------------------------------------- color utilities

def to_lin(x):
    return np.clip(x, 0, None) ** GAMMA


def to_disp(x):
    return np.clip(x, 0, None) ** (1 / GAMMA)


def luma(x):
    return x @ LUMA


def soft_clip(x, knee: float = 0.85):
    """Rolls highlights off instead of clipping them hard when a gain pushes them over 1."""
    over = x > knee
    y = x.copy()
    y[over] = knee + (1 - knee) * np.tanh((x[over] - knee) / (1 - knee))
    return y


def lab_chroma(rgb_disp: np.ndarray) -> tuple[float, float, float]:
    lin = to_lin(rgb_disp)
    m = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
    xyz = m @ lin / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    a, b = 500 * (f[0] - f[1]), 200 * (f[1] - f[2])
    return float(a), float(b), float(math.hypot(a, b))


def skin_angle(pixels: np.ndarray) -> float | None:
    if len(pixels) < 200:
        return None
    y = luma(pixels)
    cb = float(((pixels[:, 2] - y) / 1.8556).mean())
    cr = float(((pixels[:, 0] - y) / 1.5748).mean())
    return math.degrees(math.atan2(cr, cb))


def ab_split(a: float, b: float) -> tuple[float, float]:
    """A cast in a*b* as (temperature: + warm / − cool, tint: + magenta / − green)."""
    return float(np.dot([a, b], TEMP_DIR)), float(np.dot([a, b], TINT_DIR))


def balance_gains(items: list[tuple], pull: float = 0.5) -> np.ndarray:
    """Luminance-neutral channel gains that bring colors to target a*b*.
    items: (linear rgb, target a, target b, weight). pull: how much it prefers no change."""
    from scipy import optimize

    def gains_of(p):
        g = 2.0 ** np.array([p[0], 0.0, p[1]])
        return g / (g @ LUMA)
    if not items:
        return np.ones(3)

    def res(p):
        g, out = gains_of(p), []
        for lin, ta, tb, w in items:
            lab = to_lab(to_disp(np.asarray(lin) * g)[None])[0]
            out += [w * (lab[1] - ta), w * (lab[2] - tb)]
        return out + [pull * p[0], pull * p[1]]
    return gains_of(optimize.least_squares(res, [0.0, 0.0], bounds=([-1.0, -1.0], [1.0, 1.0])).x)


def saturate(x, s):
    y = luma(x)[..., None]
    return y + (x - y) * s


def s_curve(x, amount, pivot=0.45):
    if amount <= 0:
        return x
    g = 1 + 8 * amount
    sig = lambda v: 1 / (1 + np.exp(-g * (v - pivot)))  # noqa: E731
    lo, hi = sig(0.0), sig(1.0)
    return (sig(x) - lo) / (hi - lo)


def split_tone(x, shadows, highlights, amount):
    y = luma(x)[..., None]
    tint = (1 - y) ** 2 * np.array(shadows, np.float32) + y ** 2 * np.array(highlights, np.float32)
    out = x + tint * amount
    return out - (luma(out) - luma(x))[..., None]          # keep brightness


def vibrance(x, amount):
    """Saturation boost that spares what is already saturated (and skin, mostly)."""
    mx, mn = x.max(-1, keepdims=True), x.min(-1, keepdims=True)
    sat = (mx - mn) / np.maximum(mx, 1e-6)
    y = luma(x)[..., None]
    return y + (x - y) * (1 + amount * (1 - sat))


def skin_shift(x, center_deg: float, shift_deg: float):
    """Rotates hues near the measured skin hue toward the skin line, over the whole frame.
    Weighted by hue distance, chroma and luma so neutrals and strong colors don't move;
    other skin-like surfaces (wood) move too, which is why the shift is small and capped."""
    y = luma(x)
    cb, cr = (x[..., 2] - y) / 1.8556, (x[..., 0] - y) / 1.5748
    ang = np.degrees(np.arctan2(cr, cb))
    d = (ang - center_deg + 180) % 360 - 180
    chroma = np.hypot(cb, cr)
    w = (np.exp(-(d / 18) ** 2) * np.clip((chroma - 0.02) / 0.04, 0, 1)
         * np.clip((y - 0.08) / 0.1, 0, 1) * np.clip((0.95 - y) / 0.1, 0, 1))
    a = np.radians(shift_deg) * w
    cb2, cr2 = cb * np.cos(a) - cr * np.sin(a), cb * np.sin(a) + cr * np.cos(a)
    r, b = y + 1.5748 * cr2, y + 1.8556 * cb2
    g = (y - 0.2126 * r - 0.0722 * b) / 0.7152
    return np.stack([r, g, b], -1)


def look(x: np.ndarray, name: str | None, intensity: float) -> np.ndarray:
    if not name or intensity <= 0:
        return x
    t = intensity / 100
    if name == "natural":
        y = saturate(s_curve(x, 0.12), 1.06)
    elif name == "cinematic":
        y = s_curve(x, 0.35)
        y = split_tone(y, (-0.05, 0.01, 0.07), (0.06, 0.02, -0.05), 1.0)
        y = saturate(y, 0.88)
        y = 0.02 + y * 0.97                                  # slightly lifted blacks, soft top
    elif name == "warm":
        y = to_disp(to_lin(x) * np.array([1.08, 1.0, 0.88], np.float32))
        y = saturate(y, 1.05)
    elif name == "cold":
        y = to_disp(to_lin(x) * np.array([0.9, 1.0, 1.1], np.float32))
    elif name == "bw":
        y = np.repeat(s_curve(x @ np.array([0.30, 0.59, 0.11], np.float32), 0.25)[..., None], 3, -1)
    elif name == "vintage":
        y = s_curve(x, 0.12)
        y = split_tone(y, (-0.01, 0.03, 0.0), (0.05, 0.02, -0.04), 1.0)
        y = saturate(y, 0.75)
        y = 0.06 + y * 0.9                                   # faded blacks
    else:
        die(f"unknown look '{name}' (choose from {', '.join(LOOKS)})")
    return x + (np.clip(y, 0, 1) - x) * t


# ---------------------------------------------------------------- sampling

def load_material(ingest: Path) -> dict:
    p = ingest / "material.json"
    return {m["name"]: m for m in json.loads(p.read_text(encoding="utf-8"))["files"]} if p.exists() else {}


def resolve(files: list[str], material: dict) -> list[dict]:
    if not files:
        if not material:
            die("no files given and no 01-ingest/material.json")
        return [m for m in material.values() if m["kind"] == "video"]
    out = []
    for f in map(Path, files):
        m = material.get(f.name)
        if m is None:
            info = ffprobe(f)
            v = next(s for s in info["streams"] if s.get("codec_type") == "video")
            m = {"name": f.name, "file": str(f), "stem": f.stem, "duration": float(info["format"]["duration"]),
                 "display_width": v["width"], "display_height": v["height"], "hdr": None, "log_hint": None}
        out.append(m)
    return out


def sample_times(dur: float, n: int) -> list[float]:
    return [dur * (i + 0.5) / n for i in range(n)]


def grab(src: str, t: float, w: int, h: int, pre: str = "") -> np.ndarray:
    """A frame as float RGB 0-1; pre: filters before scaling (e.g. HDR tone mapping)."""
    vf = ",".join(x for x in (pre, f"scale={w}:{h}") if x)
    p = subprocess.run([tool("ffmpeg"), "-v", "error", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1",
                        "-vf", vf, "-f", "rawvideo", "-pix_fmt", "rgb48le", "-"],
                       capture_output=True, check=True)
    return (np.frombuffer(p.stdout, np.uint16).reshape(h, w, 3).astype(np.float32) / 65535)


def faces_at(faces: dict, name: str, t: float) -> list:
    samples = faces.get("files", {}).get(name) or []
    if not samples:
        return []
    s = min(samples, key=lambda s: abs(s["t"] - t))
    return s["faces"] if abs(s["t"] - t) < 1 else []


# ----------------------------------------------------------------- analysis

def measure(frames: list[np.ndarray], face_boxes: list[list]) -> dict:
    neutral, skin, lum = [], [], []
    for img, boxes in zip(frames, face_boxes):
        flat = img.reshape(-1, 3)
        y = luma(flat)
        lum.append(y)
        mid = (y > 0.15) & (y < 0.85)
        mx, mn = flat.max(1), flat.min(1)
        sat = (mx - mn) / np.maximum(mx, 1e-6)
        cand = np.where(mid)[0]
        if len(cand) > 500:
            # The least saturated third of mid-tones: neutral surfaces, even under a color cast.
            keep = cand[sat[cand] <= np.percentile(sat[cand], 33)]
            neutral.append(flat[keep])
        h, w = img.shape[:2]
        for x, yb, bw, bh, *_ in boxes:
            x0, x1 = int((x + 0.2 * bw) * w), int((x + 0.8 * bw) * w)
            # Cheeks and nose only: lips and eyes would bias the hue.
            y0, y1 = int((yb + 0.35 * bh) * h), int((yb + 0.62 * bh) * h)
            patch = img[max(y0, 0):max(y1, 0), max(x0, 0):max(x1, 0)].reshape(-1, 3)
            if len(patch):
                py = luma(patch)
                skin.append(patch[(py > 0.2) & (py < 0.9)])
    allY = np.concatenate(lum)
    out = {"median_luma": round(float(np.median(allY)), 3), "p1": round(float(np.percentile(allY, 1)), 3),
           "p99": round(float(np.percentile(allY, 99)), 3),
           # Burnt whites (luma), not saturated colors that merely max out one channel.
           "clipped_pct": round(100 * float(np.mean(allY > 0.97)), 2)}
    if neutral:
        n = np.concatenate(neutral)
        mean_lin = to_lin(n).mean(0)
        a, b, c = lab_chroma(to_disp(mean_lin))
        y = float(mean_lin @ LUMA)
        temp, tint = ab_split(a, b)
        out["neutral"] = {"a": round(a, 1), "b": round(b, 1), "cast_chroma": round(c, 1),
                          "temp": round(temp, 1), "tint": round(tint, 1), "lin": [float(v) for v in mean_lin],
                          "gains": [round(y / max(float(v), 1e-6), 4) for v in mean_lin]}
    if skin:
        ang = skin_angle(np.concatenate(skin))
        if ang is not None:
            out["skin_hue_deg"] = round(ang, 1)
            out["skin_offset_deg"] = round(ang - SKIN_LINE_DEG, 1)
    return out


def verdict(m: dict) -> list[str]:
    v = []
    c = m.get("neutral", {}).get("cast_chroma", 0)
    if c >= CAST_STRONG:
        v.append(f"strong color cast (chroma {c})")
    elif c >= CAST_MILD:
        v.append(f"mild color cast (chroma {c})")
    if abs(m.get("skin_offset_deg", 0)) > 10:
        v.append(f"skin {m['skin_offset_deg']:+.0f}° off the skin line")
    if m["median_luma"] < 0.2:
        v.append("underexposed")
    elif m["median_luma"] > 0.65:
        v.append("overexposed")
    if m["clipped_pct"] > 3:
        v.append(f"{m['clipped_pct']}% clipped highlights")
    return v


LUT_DIR = HOME / "luts"
_LUTS: dict[str, np.ndarray] = {}


def log_format(m: dict) -> str | None:
    """The log format the metadata names (D-Log M, S-Log3…), without where it was found."""
    hint = m.get("log_hint")
    return hint.split(" (")[0].strip() if hint else None


def lut_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def log_lut_path(m: dict, args) -> Path | None:
    """The conversion LUT for a log file: --input-lut if given, else the one in
    ~/.vibetake/luts/ named after its format (D-Log M.cube, slog3.cube…). None if the file
    isn't log, or its LUT isn't there yet."""
    flat = m.get("faces", {}).get("image", {}).get("flat")
    if not (m.get("log_hint") or flat or getattr(args, "force_log", False)):
        return None
    if getattr(args, "input_lut", None):
        return Path(args.input_lut)
    fmt = log_format(m)
    if not fmt or not LUT_DIR.is_dir():
        return None
    k = lut_key(fmt)
    cubes = sorted(LUT_DIR.glob("*.cube"))
    return (next((c for c in cubes if lut_key(c.stem) == k), None)
            or next((c for c in cubes if lut_key(c.stem).startswith(k) or k.startswith(lut_key(c.stem))), None))


def log_lut(m: dict, args) -> np.ndarray | None:
    p = log_lut_path(m, args)
    if p is None:
        return None
    if str(p) not in _LUTS:
        _LUTS[str(p)] = read_cube(p)
    return _LUTS[str(p)]


def missing_lut_note(m: dict) -> str:
    fmt = log_format(m)
    return ("LOG footage without its conversion LUT: measured as-is, don't correct yet — "
            + (f"put the official {fmt} LUT in {LUT_DIR / (fmt + '.cube')}" if fmt
               else "which log format? pass its LUT with --input-lut"))


def analysis_frames(m: dict, input_lut: np.ndarray | None, n: int = 10) -> tuple[list, list]:
    # HDR is measured tone-mapped (the working copy, or on the fly if it was cleaned up),
    # which is what the grade starts from.
    src, pre = sdr_source(m)
    w = 480
    h = int(round(w * m["display_height"] / m["display_width"] / 2)) * 2
    times = sample_times(m["duration"], n)
    frames = [grab(src, t, w, h, pre) for t in times]
    if input_lut is not None:
        frames = [apply_cube(f, input_lut) for f in frames]
    return frames, times


def cmd_analyze(args) -> dict:
    material, faces = load_material(Path(args.ingest)), {}
    fpath = Path(args.ingest) / "faces.json"
    if fpath.exists():
        faces = json.loads(fpath.read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report, lines = {}, ["# Color analysis", ""]
    for m in resolve(args.files, material):
        is_log = bool(m.get("log_hint") or (m.get("faces", {}).get("image", {}).get("flat")))
        lut = log_lut(m, args)
        frames, times = analysis_frames(m, lut)
        meas = measure(frames, [faces_at(faces, m["name"], t) for t in times])
        meas["verdict"] = verdict(meas)
        if is_log and lut is None:
            meas["verdict"].insert(0, missing_lut_note(m))
        elif lut is not None:
            meas["lut"] = str(log_lut_path(m, args))
        report[m["name"]] = meas
        nt = meas.get("neutral", {})
        lines += [f"## {m['name']}",
                  f"- exposure: median luma {meas['median_luma']} · p1 {meas['p1']} · p99 {meas['p99']} · "
                  f"clipped {meas['clipped_pct']}%",
                  f"- neutrals: cast chroma {nt.get('cast_chroma', '—')} (a {nt.get('a', '—')}, b {nt.get('b', '—')}; "
                  "+a magenta/−a green, +b yellow/−b blue)",
                  f"- skin: {meas['skin_hue_deg']}° ({meas['skin_offset_deg']:+.0f}° vs skin line)"
                  if "skin_hue_deg" in meas else "- skin: no faces measured",
                  "- verdict: " + ("; ".join(meas["verdict"]) or "nothing to correct — leave it unless a look is asked"),
                  ""]
    if len(report) > 1:
        meds = [r["median_luma"] for r in report.values()]
        casts = [r.get("neutral", {}).get("b", 0) for r in report.values()]
        lines.append(f"Whole frame (backgrounds differ, so only a hint): median luma spread "
                     f"{max(meds) - min(meds):.2f}, warm/cool spread {max(casts) - min(casts):.1f} (b). "
                     "build matches the shots on shared references (match.py).")
    (out / "color.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    (out / "color.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return report


# --------------------------------------------------------------------- build

def log_stop_size(table: np.ndarray) -> float:
    """Code-value change per stop of a log format, measured on its own conversion LUT
    (along the gray axis, over the mid-tones). Log curves differ; one constant doesn't fit all."""
    c = np.linspace(0.02, 0.98, 97, dtype=np.float32)
    y = to_lin(apply_cube(np.repeat(c[:, None], 3, 1), table)) @ LUMA
    ok = (y > 0.02) & (y < 0.6)
    if ok.sum() < 5:
        return STOP_IN_LOG
    return float(np.clip(np.polyfit(np.log2(y[ok]), c[ok], 1)[0], 0.03, 0.25))


_XYZ = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]], np.float32)
_WHITE = np.array([0.95047, 1.0, 1.08883], np.float32)


def to_lab(rgb):
    xyz = (to_lin(rgb) @ _XYZ.T) / _WHITE
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def from_lab(lab):
    fy = (lab[..., 0] + 16) / 116
    f = np.stack([fy + lab[..., 1] / 500, fy, fy - lab[..., 2] / 200], -1)
    xyz = np.where(f > 0.2069, f ** 3, (f - 16 / 116) / 7.787) * _WHITE
    return to_disp(np.clip(xyz @ np.linalg.inv(_XYZ).T, 0, None))


def grade_fn(gains, stops: float, is_log: bool, input_lut, look_name, intensity, stop_size: float = STOP_IN_LOG,
             creative=None, finish: dict | None = None, match_fn=None):
    """creative: (table, intensity) of a user LUT.
    finish: level extras after the correction — levels (black, white), skin (center, shift deg),
    vibrance, contrast. match_fn: the shot's match (match.py), right after the input transform;
    the correction then works in display light, the same for every matched shot."""
    gains = np.asarray(gains, np.float32)
    finish = finish or {}

    def fn(x: np.ndarray) -> np.ndarray:
        if is_log and match_fn is None:
            # Correction in log, before the LUT: exposure and white balance as offsets.
            x = np.clip(x + (np.log2(gains) + stops) * stop_size, 0, 1)
            x = apply_cube(x, input_lut) if input_lut is not None else x
        else:
            if is_log and input_lut is not None:
                x = apply_cube(x, input_lut)
            if match_fn is not None:
                x = match_fn(x)
            x = to_disp(soft_clip(to_lin(x) * gains * 2 ** stops))
        x = np.clip(x, 0, 1)
        if "levels" in finish:
            b, w = finish["levels"]
            x = np.clip(x + ((x - b) / (w - b) - x) * LEVELS_STRENGTH, 0, 1)
        if "skin" in finish:
            x = skin_shift(x, *finish["skin"])
        if finish.get("contrast"):
            x = s_curve(x, finish["contrast"])
        if finish.get("vibrance"):
            x = vibrance(x, finish["vibrance"])
        x = np.clip(x, 0, 1)
        if creative is not None:
            table, t = creative
            x = x + (apply_cube(x, table) - x) * t
        return look(np.clip(x, 0, 1), look_name, intensity)
    return fn


def plan_correction(r: dict, args, level: str, levels_ok: bool, through) -> tuple:
    """Correction from a measurement r; through(gains, stops, finish) measures the result.
    Returns gains, stops, finish, notes, the measurement after."""
    # White balance by ambient: the temperature of the light (warm ↔ cool) is the place's mood,
    # the tint (green ↔ magenta) is almost always an error (fluorescents, cheap LEDs, mixed light).
    ambient = getattr(args, "ambient", "balanced")
    gains, stops, notes, finish = [1.0, 1.0, 1.0], 0.0, [], {}
    n = r.get("neutral")
    target = None
    if n and level != "none":
        temp, tint = ab_split(n["a"], n["b"])
        keep_temp = 0.0 if ambient == "neutral" else temp
        if ambient == "balanced" and abs(temp) > TEMP_KEEP:
            keep_temp = math.copysign(TEMP_KEEP + (abs(temp) - TEMP_KEEP) / 2, temp)   # ease an extreme one
        keep_tint = tint if ambient == "keep" else 0.0
        target = keep_temp * TEMP_DIR + keep_tint * TINT_DIR
        if math.hypot(n["a"] - target[0], n["b"] - target[1]) >= 1.5:
            gains = [round(float(g), 4) for g in balance_gains([(n["lin"], *target, 1.0)], pull=0.0)]
            notes.append(f"white balance ({ambient}): tint {tint:+.1f} → {keep_tint:+.1f}, "
                         f"temperature {temp:+.1f} → {keep_temp:+.1f}")
        else:
            target = None
    if level != "none" and ("underexposed" in r["verdict"] or "overexposed" in r["verdict"]):
        # Half of the way to a 0.4 median: conservative, the scene may be meant to be dark/bright.
        stops = float(np.clip(math.log2(0.4 / max(r["median_luma"], 0.02)) * GAMMA / 2, -MAX_STOPS, MAX_STOPS))
        notes.append(f"exposure {stops:+.2f} stops")
    if level != "none" and levels_ok and (r["p1"] > 0.06 or r["p99"] < 0.88):
        black = min(max(r["p1"] - 0.01, 0.0), 0.08) if r["p1"] > 0.06 else 0.0
        white = max(r["p99"] + 0.01, 0.85) if r["p99"] < 0.88 else 1.0
        finish["levels"] = (round(black, 3), round(white, 3))
        notes.append("black/white points")
    after = through(gains, stops)
    if target is not None:
        # Refine: the log offsets and the soft clip are approximations, so re-measure.
        for _ in range(3):
            na = after.get("neutral")
            if not na or math.hypot(na["a"] - target[0], na["b"] - target[1]) < 1:
                break
            step = balance_gains([(na["lin"], *target, 1.0)], pull=0.0)
            gains = [round(float(g * s), 4) for g, s in zip(gains, step)]
            after = through(gains, stops)
    if level == "full":
        # Skin toward the skin line: half the offset, capped — global, so kept small.
        off = after.get("skin_offset_deg")
        if off is not None and abs(off) > 5:
            shift = float(np.clip(-off * 0.5, -SKIN_MAX_SHIFT, SKIN_MAX_SHIFT))
            finish["skin"] = (after["skin_hue_deg"], round(shift, 1))
            notes.append(f"skin {shift:+.1f}° toward the skin line (global, gentle)")
        finish["contrast"], finish["vibrance"] = 0.06, 0.08
        notes.append("contrast and vibrance tidy-up")
    if finish:
        after = through(gains, stops, finish)
    return gains, stops, finish, notes, after


def cmd_build(args) -> None:
    out = Path(args.out)
    if args.look and args.look not in LOOKS:
        die(f"unknown look '{args.look}' (choose from {', '.join(LOOKS)})")
    report = cmd_analyze(args)
    material = load_material(Path(args.ingest))
    items = resolve(args.files, material)
    faces = {}
    if (Path(args.ingest) / "faces.json").exists():
        faces = json.loads((Path(args.ingest) / "faces.json").read_text(encoding="utf-8"))
    level = args.level
    ctx = {}
    for m in items:
        lut = log_lut(m, args)                      # this file's own conversion (formats can differ)
        is_log = lut is not None
        frames, times = analysis_frames(m, None)
        ctx[m["name"]] = {"is_log": is_log, "lut": lut, "lut_path": log_lut_path(m, args),
                          "stop_size": log_stop_size(lut) if is_log else STOP_IN_LOG,
                          "frames": frames, "boxes": [faces_at(faces, m["name"], t) for t in times],
                          "match": None, "mfn": None}
    # Matching first (match.py: the groups, references and nudges decided by looking at its
    # sheet) — also at level none, matching isn't a look. Then one correction per group,
    # measured on the matched group; a shot in no group gets its own.
    matched = {}
    if not getattr(args, "no_match", False):
        import match as matcher
        matched = matcher.solve(items, args)
        for name, res in matched.items():
            if name in ctx:
                ctx[name]["match"] = res
                ctx[name]["mfn"] = matcher.transform(res["params"]) if res.get("params") else None

    def through_for(names):
        def through(g, s, fin=None):
            fr, bx = [], []
            for n in names:
                c = ctx[n]
                fn = grade_fn(g, s, c["is_log"], c["lut"], None, 0, c["stop_size"], finish=fin,
                              match_fn=c["mfn"])
                fr += [fn(f.reshape(-1, 3)).reshape(f.shape) for f in c["frames"]]
                bx += c["boxes"]
            return measure(fr, bx)
        return through

    def unit_of(name: str) -> str:
        res = ctx[name]["match"] or {}
        return f"group {res['group']}" if res.get("group") and res.get("status") in ("ok", "partial") else name

    units: dict[str, list[str]] = {}
    for m in items:
        if (ctx[m["name"]]["match"] or {}).get("status") != "skipped":
            units.setdefault(unit_of(m["name"]), []).append(m["name"])
    corrections = {}
    for u, names in units.items():
        through = through_for(names)
        r0 = through([1.0, 1.0, 1.0], 0.0)
        r0["verdict"] = verdict(r0)
        corrections[u] = plan_correction(r0, args, level, not all(ctx[n]["is_log"] for n in names), through)
    plan = {}
    for m in items:
        r, c = report[m["name"]], ctx[m["name"]]
        is_log, stop_size, frames = c["is_log"], c["stop_size"], c["frames"]
        res = c["match"] or {}
        skipped = res.get("status") == "skipped"
        notes = [f"level {level}"]
        if skipped:
            # Not camera footage (screen, graphics, archive): no correction, no look.
            gains, stops, finish, after = [1.0, 1.0, 1.0], 0.0, {}, r
            notes.append("skipped: not camera footage, left untouched")
        else:
            u = unit_of(m["name"])
            gains, stops, finish, own, _ = corrections[u]
            if res.get("status") in ("ok", "partial"):
                notes.append(f"matched in {u} ({res['status']}; references: {', '.join(res.get('used', []))})")
            elif res.get("status") == "not matched":
                notes.append("not matched: " + "; ".join(res.get("warnings", [])))
            notes += [f"{'shared' if u != m['name'] else 'own'}: {x}" for x in own]
            after = through_for([m["name"]])(gains, stops, finish or None)
        if is_log:
            notes.insert(0, f"log → Rec.709 with {c['lut_path'].name} ({stop_size:.3f}/stop)")
        creative = None
        look_name = None if skipped else args.look
        if args.creative_lut and not skipped:
            creative = (read_cube(Path(args.creative_lut)), args.creative_intensity / 100)
            notes.append(f"creative LUT {Path(args.creative_lut).name} {args.creative_intensity:.0f}%")
        if look_name:
            notes.append(f"look {args.look} {args.intensity:.0f}%")
        cube = out / f"{m['stem']}.cube"
        write_cube(cube, grade_fn(gains, stops, is_log, c["lut"], look_name, args.intensity,
                                  stop_size, creative, finish, c["mfn"]), f"vibetake {m['name']}")
        pre = ""
        if m.get("hdr"):
            pre = pick_tonemap()[1]
            notes.insert(0, f"HDR {m['hdr']} tone-mapped before the LUT")
        # Repairs go before the correction: correcting amplifies noise and false color.
        repairs = []
        if getattr(args, "denoise", "off") != "off":
            repairs.append(DENOISE[args.denoise])
            notes.append(f"noise reduction {args.denoise}")
        if getattr(args, "moire", False):
            repairs.append(MOIRE)
            notes.append("false color (moiré) softened in the chroma")
        pre = ",".join(x for x in (pre, *repairs) if x)
        plan[m["name"]] = {"source": m["file"], "pre": pre, "lut": str(cube), "notes": notes,
                           "params": {"level": level, "gains": gains, "stops": round(stops, 3), "log": is_log,
                                      "finish": finish, "look": look_name, "intensity": args.intensity,
                                      "match": (c["match"] or {}).get("params"),
                                      "match_status": (matched.get(m["name"]) or {}).get("status")}}
        plan[m["name"]]["check"] = {"cast_before": r.get("neutral", {}).get("cast_chroma"),
                                    "cast_after": after.get("neutral", {}).get("cast_chroma"),
                                    "temp_tint_before": [r.get("neutral", {}).get("temp"), r.get("neutral", {}).get("tint")],
                                    "temp_tint_after": [after.get("neutral", {}).get("temp"),
                                                        after.get("neutral", {}).get("tint")],
                                    "median_luma_before": r["median_luma"], "median_luma_after": after["median_luma"],
                                    "skin_offset_before": r.get("skin_offset_deg"),
                                    "skin_offset_after": after.get("skin_offset_deg")}
        print(f"{m['name']}: {', '.join(notes) or 'no change'} · cast {plan[m['name']]['check']['cast_before']} → "
              f"{plan[m['name']]['check']['cast_after']} · luma {r['median_luma']} → {after['median_luma']}")
    gp = out / "grade.json"
    old = json.loads(gp.read_text(encoding="utf-8")) if gp.exists() else {}
    gp.write_text(json.dumps({**old, **plan}, indent=1), encoding="utf-8")
    print(f"grade plan → {gp}")


def cmd_propose(args) -> None:
    """Builds Basic and Full side by side with previews, and recommends one, for the color question."""
    out = Path(args.out)
    report = None
    for level in ("basic", "full"):
        sub = argparse.Namespace(**{**vars(args), "level": level, "out": str(out / level), "look": None,
                                    "force_log": args.force_log, "creative_lut": None,
                                    "creative_intensity": 100, "intensity": 60, "n": 2})
        cmd_build(sub)
        cmd_preview(sub)
        report = report or json.loads((out / level / "color.json").read_text(encoding="utf-8"))
    skin = any(abs(r.get("skin_offset_deg", 0)) > 10 for r in report.values())
    anything = any(r["verdict"] for r in report.values())
    rec = "full" if skin else "basic" if anything else "none"
    findings = "; ".join(f"{n}: {', '.join(r['verdict']) or 'nothing to correct'}" for n, r in report.items())
    lines = ["# Color proposal", "", f"Findings: {findings}", f"Recommended: {rec}", "",
             f"- Basic: shots matched + exposure, white balance, black/white points → previews in {out / 'basic'}",
             f"- Full: Basic + skin (global, gentle), contrast and vibrance → previews in {out / 'full'}",
             "- None: only the shots matched (a single file: the material as is)",
             "- Custom: free text (a look, the user's LUT; a reference image: look at it and pick a look)"]
    if len(report) > 1 and not args.no_match:
        lines.append(f"- Shot matching: {Path(args.match_dir) / 'match.md'}, sheet.jpg and faces.jpg — check them")
    (out / "propose.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


# ------------------------------------------------------------------- preview

def cmd_preview(args) -> None:
    out = Path(args.out)
    plan = json.loads((out / "grade.json").read_text(encoding="utf-8"))
    material = load_material(Path(args.ingest))
    names = [Path(f).name for f in args.files] or list(plan)
    font = find_font()
    label = lambda txt: (f",drawtext={'fontfile=' + chr(39) + filter_path(font) + chr(39) + ':' if font else ''}"  # noqa: E731
                         f"text='{txt}':x=12:y=12:fontsize=30:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=8")
    for name in names:
        p = plan.get(name) or die(f"{name} has no grade yet: run build first")
        dur = float((material.get(name) or {}).get("duration") or ffprobe(Path(p["source"]))["format"]["duration"])
        for i, t in enumerate(sample_times(dur, args.n)):
            pre = f"{p['pre']}," if p["pre"] else ""
            scope = "format=yuv444p,{}"
            fc = (f"[0:v]{pre}scale=960:-2,format=rgb48le,split=2[b][x];"
                  f"[x]lut3d=file='{filter_path(p['lut'])}'[a];"
                  f"[b]split=3[b1][b2][b3];[a]split=3[a1][a2][a3];"
                  f"[b2]{scope.format('waveform=g=green:fl=numbers:o=0.7')},scale=960:240[bw];"
                  f"[a2]{scope.format('waveform=g=green:fl=numbers:o=0.7')},scale=960:240[aw];"
                  f"[b3]{scope.format('vectorscope=m=color3:g=green:o=0.7')},crop=iw*0.6:ih*0.6,scale=240:240,pad=960:240:360:0[bv];"
                  f"[a3]{scope.format('vectorscope=m=color3:g=green:o=0.7')},crop=iw*0.6:ih*0.6,scale=240:240,pad=960:240:360:0[av];"
                  f"[b1]format=rgb24{label('before')}[b1l];[a1]format=rgb24{label('after')}[a1l];"
                  f"[bw]format=rgb24[bw2];[aw]format=rgb24[aw2];[bv]format=rgb24[bv2];[av]format=rgb24[av2];"
                  f"[b1l][bw2][bv2]vstack=3[L];[a1l][aw2][av2]vstack=3[R];[L][R]hstack")
            dst = out / f"preview_{Path(name).stem}_{i + 1}.jpg"
            run([tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", p["source"], "-frames:v", "1",
                 "-filter_complex", fc, "-q:v", "3", str(dst)])
            print(f"{dst}  ({tc(t)})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("analyze", "propose", "build", "preview"):
        s = sub.add_parser(name)
        s.add_argument("files", nargs="*")
        s.add_argument("--ingest", default="01-ingest")
        s.add_argument("--out", default="02-work/color")
        s.add_argument("--input-lut", help="force this conversion LUT for every log file (default: each file's "
                                          "format's LUT from ~/.vibetake/luts/)")
        if name == "propose":
            s.add_argument("--force-log", action="store_true", help="treat files as log even if not flagged")
        if name in ("build", "propose"):
            s.add_argument("--no-match", action="store_true", help="don't match the shots to each other")
            s.add_argument("--match-target", help="match to this shot instead of the neutral base")
            s.add_argument("--zones", choices=("auto", "on", "off"), default="auto",
                           help="matching color per tonal zone: auto = only when the cameras differ")
            s.add_argument("--ambient", choices=AMBIENTS, default="balanced",
                           help="white balance: balanced keeps the light's warmth or coolness and removes "
                                "green/magenta tints · neutral removes both · keep leaves both")
            s.add_argument("--edl", default="02-work/edl.json", help="match on what the edit uses (if it exists)")
            s.add_argument("--match-dir", default="02-work/color/match")
        if name == "build":
            s.add_argument("--level", choices=LEVELS, default="basic",
                           help="none: only shot matching · basic: + exposure, white balance, black/white · "
                                "full: basic + skin, contrast, vibrance")
            s.add_argument("--denoise", choices=("off", *DENOISE), default="off",
                           help="noise reduction, only when asked (grain can be wanted): light or medium")
            s.add_argument("--moire", action="store_true",
                           help="soften false color (moiré) in the chroma — only if a 100%% still of the raw shows it")
            s.add_argument("--look", choices=LOOKS)
            s.add_argument("--intensity", type=float, default=60)
            s.add_argument("--force-log", action="store_true", help="treat files as log even if not flagged")
            s.add_argument("--creative-lut", help="a user's creative LUT (Rec.709 in and out), after correction")
            s.add_argument("--creative-intensity", type=float, default=100)
        if name == "preview":
            s.add_argument("--n", type=int, default=3, help="frames per file")
    args = ap.parse_args()
    {"analyze": cmd_analyze, "propose": cmd_propose, "build": cmd_build, "preview": cmd_preview}[args.cmd](args)


if __name__ == "__main__":
    main()
