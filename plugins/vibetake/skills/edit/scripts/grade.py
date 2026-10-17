# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Color: measure, build the grade as a .cube, preview before/after.

    uv run grade.py analyze [files...] [--ingest 01-ingest] [--out 02-work/color]
    uv run grade.py build   [files...] [--correct auto|on|off] [--look cinematic --intensity 60]
                            [--input-lut <format>.cube] [--match] [--out 02-work/color]
                            [--creative-lut <user>.cube] [--reference <image> (experimental)]
    uv run grade.py preview [files...] [--out 02-work/color]
    uv run grade.py look --look cinematic --intensity 60 --file <name>.cube   # look alone, for other editors

Without files, uses every video in 01-ingest/material.json. Order inside the grade:
input transform (HDR tone mapping as a pre-filter; a log format's LUT) → correction
(white balance and exposure, done in log before the LUT for log footage) → look →
Rec.709. Everything after the HDR pre-filter is baked into one .cube per file and
applied with ffmpeg's lut3d; grade.json says, per file, which pre-filter and LUT to
use at render time. Always the whole frame: faces only serve to measure skin.

Looks: natural, cinematic, warm, cold, bw, vintage (intensity 0-100).
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _common import die, ffprobe, filter_path, find_font, run, tc, tool  # noqa: E402

GAMMA = 2.4                  # display-referred math (BT.1886)
LUMA = np.array([0.2126, 0.7152, 0.0722], np.float32)
SKIN_LINE_DEG = 123.0        # where skin of any color sits on a vectorscope (the "I line")
STOP_IN_LOG = 0.08           # approx. code-value change per stop in common log curves
CAST_STRONG, CAST_MILD = 6.0, 3.0   # chroma (Lab) of what should be neutral
MILD_STRENGTH = 0.6                 # share of a mild cast removed in auto mode
MAX_STOPS = 1.0
LUT_SIZE = 33
LOOKS = ("natural", "cinematic", "warm", "cold", "bw", "vintage")


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


def grab(src: str, t: float, w: int, h: int) -> np.ndarray:
    p = subprocess.run([tool("ffmpeg"), "-v", "error", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1",
                        "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb48le", "-"],
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
        out["neutral"] = {"a": round(a, 1), "b": round(b, 1), "cast_chroma": round(c, 1),
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


def analysis_frames(m: dict, input_lut: np.ndarray | None, n: int = 10) -> tuple[list, list]:
    # HDR is measured on the tone-mapped working copy, which is what the grade starts from.
    src = (m.get("normalized") or {}).get("file") if m.get("hdr") else None
    src = src or m["file"]
    w = 480
    h = int(round(w * m["display_height"] / m["display_width"] / 2)) * 2
    times = sample_times(m["duration"], n)
    frames = [grab(src, t, w, h) for t in times]
    if input_lut is not None:
        frames = [apply_cube(f, input_lut) for f in frames]
    return frames, times


def cmd_analyze(args) -> dict:
    material, faces = load_material(Path(args.ingest)), {}
    fpath = Path(args.ingest) / "faces.json"
    if fpath.exists():
        faces = json.loads(fpath.read_text(encoding="utf-8"))
    lut = read_cube(Path(args.input_lut)) if args.input_lut else None
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report, lines = {}, ["# Color analysis", ""]
    for m in resolve(args.files, material):
        is_log = bool(m.get("log_hint") or (m.get("faces", {}).get("image", {}).get("flat")))
        frames, times = analysis_frames(m, lut if is_log or args.input_lut else None)
        meas = measure(frames, [faces_at(faces, m["name"], t) for t in times])
        meas["verdict"] = verdict(meas)
        if is_log and lut is None:
            meas["verdict"].insert(0, "LOG footage without its conversion LUT: measured as-is, don't correct yet")
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
        lines.append(f"Shot match: median luma spread {max(meds) - min(meds):.2f}, warm/cool spread "
                     f"{max(casts) - min(casts):.1f} (b). Above 0.08 / 4, use --match.")
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


def lab_stats(frames: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    lab = to_lab(np.concatenate([f.reshape(-1, 3) for f in frames])[::7])
    return lab.mean(0), lab.std(0) + 1e-3


def grade_fn(gains, stops: float, is_log: bool, input_lut, look_name, intensity, stop_size: float = STOP_IN_LOG,
             creative=None, transfer=None):
    """creative: (table, intensity) of a user LUT. transfer: (src_mean, src_std, ref_mean, ref_std) in Lab."""
    gains = np.asarray(gains, np.float32)

    def fn(x: np.ndarray) -> np.ndarray:
        if is_log:
            # Correction in log, before the LUT: exposure and white balance as offsets.
            x = np.clip(x + (np.log2(gains) + stops) * stop_size, 0, 1)
            x = apply_cube(x, input_lut) if input_lut is not None else x
        else:
            x = to_disp(soft_clip(to_lin(x) * gains * 2 ** stops))
        x = np.clip(x, 0, 1)
        if transfer is not None:
            sm, ss, rm, rs, t = transfer
            moved = from_lab((to_lab(x) - sm) / ss * rs + rm)
            x = np.clip(x + (np.clip(moved, 0, 1) - x) * t, 0, 1)
        if creative is not None:
            table, t = creative
            x = x + (apply_cube(x, table) - x) * t
        return look(np.clip(x, 0, 1), look_name, intensity)
    return fn


def cmd_build(args) -> None:
    out = Path(args.out)
    if args.look and args.look not in LOOKS:
        die(f"unknown look '{args.look}' (choose from {', '.join(LOOKS)})")
    report = cmd_analyze(args)
    material = load_material(Path(args.ingest))
    lut = read_cube(Path(args.input_lut)) if args.input_lut else None
    items = resolve(args.files, material)
    target = float(np.median([report[m["name"]]["median_luma"] for m in items])) if args.match else None
    plan = {}
    for m in items:
        r = report[m["name"]]
        is_log = bool(args.input_lut and (m.get("log_hint") or m.get("faces", {}).get("image", {}).get("flat")
                                          or args.force_log))
        # The baseline is the most natural image: strong casts fully, mild ones partly
        # (a warm room keeps some warmth). Skin offsets are only reported.
        cast = r.get("neutral", {}).get("cast_chroma", 0)
        correct = args.correct == "on" or (args.correct == "auto" and (
            cast >= CAST_MILD or any("exposed" in v for v in r["verdict"])))
        strength = 1.0 if args.correct == "on" or cast >= CAST_STRONG else MILD_STRENGTH
        gains, stops, notes = [1.0, 1.0, 1.0], 0.0, []
        if correct and cast >= 1 and "neutral" in r:
            gains = [round(g ** strength, 4) for g in r["neutral"]["gains"]]
            notes.append(f"white balance (cast {cast}{', partial' if strength < 1 else ''})")
        if correct and ("underexposed" in r["verdict"] or "overexposed" in r["verdict"]):
            # Half of the way to a 0.4 median: conservative, the scene may be meant to be dark/bright.
            stops = float(np.clip(math.log2(0.4 / max(r["median_luma"], 0.02)) * GAMMA / 2, -MAX_STOPS, MAX_STOPS))
            notes.append(f"exposure {stops:+.2f} stops")
        if target is not None:
            stops = float(np.clip(stops + math.log2(target / max(r["median_luma"], 0.02)) * GAMMA,
                                  -MAX_STOPS, MAX_STOPS))
            notes.append(f"matched to median luma {target:.2f}")
        stop_size = log_stop_size(lut) if is_log else STOP_IN_LOG
        if is_log:
            notes.insert(0, f"log → Rec.709 with {Path(args.input_lut).name} ({stop_size:.3f}/stop)")
        frames, _ = analysis_frames(m, None)

        def through(g, s):
            fn = grade_fn(g, s, is_log, lut if is_log else None, None, 0, stop_size)
            return measure([fn(f.reshape(-1, 3)).reshape(f.shape) for f in frames], [[] for _ in frames])

        after = through(gains, stops)
        if gains != [1.0, 1.0, 1.0] and strength == 1.0:
            # Refine: the log offsets and the soft clip are approximations, so re-measure.
            for _ in range(3):
                if after.get("neutral", {}).get("cast_chroma", 0) < 1:
                    break
                gains = [round(g * a, 4) for g, a in zip(gains, after["neutral"]["gains"])]
                after = through(gains, stops)
        creative = transfer = None
        if args.creative_lut:
            creative = (read_cube(Path(args.creative_lut)), args.creative_intensity / 100)
            notes.append(f"creative LUT {Path(args.creative_lut).name} {args.creative_intensity:.0f}%")
        if args.reference:
            # Experimental: move the shot's Lab mean/spread toward the reference image's.
            base = grade_fn(gains, stops, is_log, lut if is_log else None, None, 0, stop_size)
            sm, ss = lab_stats([base(f.reshape(-1, 3)).reshape(f.shape) for f in frames])
            rm, rs = lab_stats([reference_image(Path(args.reference))])
            transfer = (sm, ss, rm, rs, args.reference_intensity / 100)
            notes.append(f"matched to reference {Path(args.reference).name} {args.reference_intensity:.0f}% "
                         "(experimental)")
        if args.look:
            notes.append(f"look {args.look} {args.intensity:.0f}%")
        cube = out / f"{m['stem']}.cube"
        write_cube(cube, grade_fn(gains, stops, is_log, lut if is_log else None, args.look, args.intensity,
                                  stop_size, creative, transfer), f"vibetake {m['name']}")
        pre = ""
        if m.get("hdr"):
            from ingest import pick_tonemap
            pre = pick_tonemap()[1]
            notes.insert(0, f"HDR {m['hdr']} tone-mapped before the LUT")
        plan[m["name"]] = {"source": m["file"], "pre": pre, "lut": str(cube), "notes": notes,
                           "params": {"gains": gains, "stops": round(stops, 3), "log": is_log,
                                      "look": args.look, "intensity": args.intensity}}
        plan[m["name"]]["check"] = {"cast_before": r.get("neutral", {}).get("cast_chroma"),
                                    "cast_after": after.get("neutral", {}).get("cast_chroma"),
                                    "median_luma_before": r["median_luma"], "median_luma_after": after["median_luma"]}
        print(f"{m['name']}: {', '.join(notes) or 'no change'} · cast {plan[m['name']]['check']['cast_before']} → "
              f"{plan[m['name']]['check']['cast_after']} · luma {r['median_luma']} → {after['median_luma']}")
    gp = out / "grade.json"
    old = json.loads(gp.read_text(encoding="utf-8")) if gp.exists() else {}
    gp.write_text(json.dumps({**old, **plan}, indent=1), encoding="utf-8")
    print(f"grade plan → {gp}")


def reference_image(path: Path) -> np.ndarray:
    info = ffprobe(path)
    v = next(s for s in info["streams"] if s.get("codec_type") == "video")
    w = 480
    h = int(round(w * v["height"] / v["width"] / 2)) * 2
    return grab(str(path), 0, w, h)


def cmd_look(args) -> None:
    if args.look not in LOOKS:
        die(f"unknown look '{args.look}' (choose from {', '.join(LOOKS)})")
    path = Path(args.file or f"{args.look}_{int(args.intensity)}.cube")
    write_cube(path, lambda x: look(x, args.look, args.intensity), f"vibetake {args.look} {args.intensity:.0f}%")
    print(path)


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
    for name in ("analyze", "build", "preview"):
        s = sub.add_parser(name)
        s.add_argument("files", nargs="*")
        s.add_argument("--ingest", default="01-ingest")
        s.add_argument("--out", default="02-work/color")
        s.add_argument("--input-lut", help="the log format's official conversion LUT (.cube)")
        if name == "build":
            s.add_argument("--correct", choices=("auto", "on", "off"), default="auto",
                           help="auto: strong casts fully, mild ones partly, exposure when clearly off")
            s.add_argument("--look", choices=LOOKS)
            s.add_argument("--intensity", type=float, default=60)
            s.add_argument("--match", action="store_true", help="match exposure across files")
            s.add_argument("--force-log", action="store_true", help="treat files as log even if not flagged")
            s.add_argument("--creative-lut", help="a user's creative LUT (Rec.709 in and out), after correction")
            s.add_argument("--creative-intensity", type=float, default=100)
            s.add_argument("--reference", help="image (or video frame) whose palette to approach — experimental")
            s.add_argument("--reference-intensity", type=float, default=60)
        if name == "preview":
            s.add_argument("--n", type=int, default=3, help="frames per file")
    s = sub.add_parser("look")
    s.add_argument("--look", required=True, choices=LOOKS)
    s.add_argument("--intensity", type=float, default=60)
    s.add_argument("--file")
    args = ap.parse_args()
    {"analyze": cmd_analyze, "build": cmd_build, "preview": cmd_preview, "look": cmd_look}[args.cmd](args)


if __name__ == "__main__":
    main()
