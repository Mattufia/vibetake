# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26", "scipy>=1.11"]
# ///
"""Shot matching: make every shot look alike before the shared correction.

    uv run match.py solve [files...] [--target <file>] [--zones auto|on|off] [--input-lut <format>.cube]
    uv run match.py add  --name <ref> --file <file> --t <s> --at <x>,<y>   # a reference picked by looking
    uv run match.py drop --name <ref> [--file <file>] [--restore]           # leave a reference out

What is measured are things present in every shot, never whole-frame statistics
(each shot's background differs): the person's skin, hair and clothes (rembg cut-out
+ face boxes), references added by pointing at them (`add`: the point is snapped to
the surface around it), and only as a last resort low-saturation surfaces per tonal
zone. Each reference is measured on several frames per shot.

Per shot: white balance (gains in linear light) → a smooth tone curve on L* →
a small color offset per tonal zone (shadows / mids / highlights) when the shots come
from different cameras. Target: a neutral base with headroom — skin and mids at the
shots' middle value, darks as deep as the deepest shot that still has texture, lights
as bright as the brightest that isn't clipped. `--target <file>` uses one shot instead.

Rules enforced here, not left to judgment:
 1. brightness order never inverts (the curve always rises),
 2. detail is kept (each reference's texture measured before/after),
 3. no new clipped blacks or whites,
 4. gentle changes (bounded slopes, gains and offsets),
 5. a shot that can't be matched well is flagged instead of forced.
A reference the allowed curve can't satisfy (lit differently in one shot, or
contradicting a nearby one) is dropped in that shot — lowest priority first — and
reported.

Output in 02-work/color/match/: match.json (per-file parameters, read by grade.py),
match.md (references before/after and warnings), sheet.jpg (each reference in every shot,
before | after), faces.jpg (faces at full resolution) and regions.jpg (what was measured).
grade.py build runs `solve` itself when there are several files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage, optimize
from scipy.interpolate import PchipInterpolator

sys.path.insert(0, str(Path(__file__).parent))
from _common import die, filter_path, find_font, tool  # noqa: E402
from grade import (LUMA, apply_cube, faces_at, from_lab, grab, load_material, read_cube, resolve,  # noqa: E402
                   to_disp, to_lab, to_lin)

WIDTH = 640                      # analysis width
FRAMES = 6                       # frames measured per shot (4 when there are many shots)
MASK_MODEL = "u2net_human_seg"   # person cut-out for measuring
# Tone curve, in L*. Past 100: what a positive exposure pushed over white, rolled back under it.
KNOTS = np.array([0, 8, 16, 25, 35, 45, 55, 65, 75, 85, 93, 100, 115, 140], float)
L_MAX = float(KNOTS[-1])
SLOPE = (0.55, 1.7)              # rule 4: the curve (after exposure) never much flatter or steeper
SLOPE_SHADOWS = (0.5, 1.4)       # below L* 25: flatter undoes lifted blacks; steep lifts noise and blocks
SLOPE_TEXTURE = 0.85             # rule 2: around skin (and any reference losing texture) contrast stays
TEXTURE_KEEP = 0.75              # rule 2: share of a reference's texture that must survive (skin 0.85)
LOST = 0.3                       # share of a reference crushed or burnt: its detail is lost at the source
MAX_GAIN_STOPS = 0.5             # rule 4: white balance, per channel
MAX_EXPOSURE = 1.5               # rule 4: stops
MAX_ZONE_OFFSET = 5.0            # rule 4: a, b units per tonal zone
MAX_NEW_CLIP = 0.5               # rule 3: percentage points of newly clipped pixels
FIT_TOL = 4.0                    # L*: a reference left further than this from its target conflicts
UNSTABLE = 6.0                   # L* spread across a shot's frames: the light on it changes (noted)
DARK_FLOOR, LIGHT_CEIL = 8.0, 92.0   # the base keeps texture in the darks and headroom in the lights
PRIORITY = {"skin": 3.0, "dark": 1.5, "light": 1.5, "mid": 1.0, "neutral": 0.3}
COLORS = [(255, 80, 80), (80, 200, 255), (255, 210, 60), (120, 255, 120), (255, 120, 255), (255, 160, 60),
          (160, 160, 255), (60, 255, 210)]


# ------------------------------------------------------------------ helpers

def L_of_Y(Y):
    f = np.where(Y > 0.008856, np.cbrt(np.clip(Y, 0, None)), 7.787 * Y + 16 / 116)
    return 116 * f - 16


def Y_of_L(L):
    fy = (L + 16) / 116
    return np.where(fy > 0.2069, fy ** 3, (fy - 16 / 116) / 7.787)


def tex_scale(L: float) -> float:
    """dL*/dlog2(Y) at a given L*: how many L* units one stop of light spans there."""
    # Floor: in near-black the "texture" is compression noise, not detail.
    return max(L if L <= 8 else (L + 16) / 3, 3.0) * np.log(2)


def zone_weights(L):
    """Shadows / mids / highlights, smooth and summing to 1; pure black and white stay out."""
    sh = np.clip((45 - L) / 30, 0, 1)
    hl = np.clip((L - 55) / 30, 0, 1)
    w = np.stack([sh, np.clip(1 - sh - hl, 0, 1), hl], -1)
    return w * (np.clip(L / 8, 0, 1) * np.clip((100 - L) / 8, 0, 1))[..., None]


def into_gamut(lin, Y):
    """Channels over 1 are pulled toward the pixel's own luminance instead of clipping (hue kept)."""
    Y = np.clip(Y, 0, 1)[:, None]
    m = lin.max(1, keepdims=True)
    t = np.where(m > 1, (1 - Y) / np.maximum(m - Y, 1e-6), 1.0)
    return Y + (lin - Y) * np.clip(t, 0, 1)


def transform(params: dict | None):
    """The per-shot match as a function on display RGB (any shape ending in 3). None → identity."""
    if not params:
        return lambda x: x
    g = np.asarray(params.get("gains", [1, 1, 1]), np.float32) * np.float32(2.0 ** params.get("exposure", 0.0))
    curve = PchipInterpolator(KNOTS, params["curve"]) if params.get("curve") else None
    zones = np.asarray(params["zones"], np.float32) if params.get("zones") else None

    def fn(x: np.ndarray) -> np.ndarray:
        shape = x.shape
        lin = to_lin(x.reshape(-1, 3)) * g
        Y = lin @ LUMA
        if curve is not None:
            Y2 = np.clip(Y_of_L(np.clip(curve(np.clip(L_of_Y(np.clip(Y, 0, None)), 0, L_MAX)), 0, 100)), 0, 1)
            k = Y2 / np.maximum(Y, 1e-6)
            lin = np.where((Y > 1e-5)[:, None], lin * k[:, None], Y2[:, None])
            Y = Y2
        out = to_disp(into_gamut(lin, Y))
        if zones is not None:
            lab = to_lab(out)
            lab[:, 1:] += zone_weights(lab[:, 0]) @ zones
            out = from_lab(lab)
        return np.clip(out, 0, 1).reshape(shape).astype(np.float32)
    return fn


def gray_ramp_ok(fn) -> bool:
    """Rule 1 on the real transform: a gray ramp comes out in the same order."""
    r = np.repeat(np.linspace(0, 1, 512, dtype=np.float32)[:, None], 3, 1)
    y = fn(r) @ LUMA
    return bool(np.all(np.diff(y) >= -1e-4))


def otsu(v: np.ndarray) -> float:
    hist, edges = np.histogram(v, 64)
    c = (edges[:-1] + edges[1:]) / 2
    w0 = np.cumsum(hist).astype(float)
    w1 = w0[-1] - w0
    m0 = np.cumsum(hist * c)
    with np.errstate(divide="ignore", invalid="ignore"):
        sb = w0 * w1 * (m0 / w0 - (m0[-1] - m0) / w1) ** 2
    return float(c[int(np.nanargmax(np.where(np.isfinite(sb), sb, -1)))])


def write_png(path: Path, img: np.ndarray) -> None:
    h, w = img.shape[:2]
    subprocess.run([tool("ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb48le", "-s", f"{w}x{h}",
                    "-i", "-", str(path)], input=(np.clip(img, 0, 1) * 65535).astype("<u2").tobytes(), check=True)


def read_gray(path: Path, w: int, h: int) -> np.ndarray:
    p = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", str(path), "-vf", f"scale={w}:{h}", "-f", "rawvideo",
                        "-pix_fmt", "gray", "-"], capture_output=True, check=True)
    return np.frombuffer(p.stdout, np.uint8).reshape(h, w) / 255.0


# ----------------------------------------------------------------- frames

def is_log(m: dict, args) -> bool:
    return bool(getattr(args, "input_lut", None) and (m.get("log_hint") or m.get("faces", {}).get("image", {})
                                                     .get("flat") or getattr(args, "force_log", False)))


def source(m: dict) -> str:
    src = (m.get("normalized") or {}).get("file") if m.get("hdr") else None
    return src or m["file"]


def frame_size(m: dict) -> tuple[int, int]:
    return WIDTH, int(round(WIDTH * m["display_height"] / m["display_width"] / 2)) * 2


def used_ranges(m: dict, edl: dict | None) -> list[tuple[float, float]]:
    """The parts of the file the edit keeps; the whole file when there's no edit yet."""
    segs = [(s["in"], s["out"]) for s in (edl or {}).get("segments", [])
            if s.get("name", Path(s.get("source", "")).name) == m["name"] and s["out"] > s["in"]]
    return segs or [(0.0, m["duration"])]


def pick_times(m: dict, faces: dict, n: int, ranges: list[tuple[float, float]]) -> list[float]:
    """Spread over what the edit uses, preferring times with a face (the person is the best
    reference): what's measured is what the viewer will see."""
    inside = lambda t: any(a <= t <= b for a, b in ranges)  # noqa: E731
    with_face = [s["t"] for s in faces.get("files", {}).get(m["name"], []) if s["faces"] and inside(s["t"])]
    if len(with_face) >= n:
        return [with_face[i] for i in np.linspace(0, len(with_face) - 1, n).round().astype(int)]
    total = sum(b - a for a, b in ranges)
    times = []
    for k in range(n):
        pos = total * (k + 0.5) / n
        for a, b in ranges:
            if pos <= b - a:
                times.append(round(a + pos, 2))
                break
            pos -= b - a
    return times


def load(m: dict, t: float, lut) -> np.ndarray:
    w, h = frame_size(m)
    img = grab(source(m), max(0.0, min(t, m["duration"] - 0.05)), w, h)
    return apply_cube(img, lut) if lut is not None else img


def person_masks(work: list[tuple[str, np.ndarray]], mdir: Path) -> dict[str, np.ndarray]:
    """Person masks for the sampled frames, cached by file and time. A light model (rembg
    u2net_human_seg, ~0.4 s a frame on CPU) is enough to measure; the heavy one is for covers."""
    d_in, d_out = mdir / "masks" / "in", mdir / "masks" / MASK_MODEL
    d_in.mkdir(parents=True, exist_ok=True)
    d_out.mkdir(parents=True, exist_ok=True)
    todo = [(k, img) for k, img in work if not (d_out / f"{k}.png").exists()]
    if todo:
        uvx = shutil.which("uvx")
        if not uvx:
            print("WARNING: uvx not found — no person cut-out, matching on skin and other references only")
            return {}
        for f in d_in.glob("*.png"):
            f.unlink()
        for k, img in todo:
            write_png(d_in / f"{k}.png", img)
        print(f"cutting out the person on {len(todo)} frames (rembg {MASK_MODEL})…", flush=True)
        p = subprocess.run([uvx, "--python", "3.12", "--from", "rembg[cpu,cli]", "rembg", "p", "-om", "-m",
                            MASK_MODEL, str(d_in), str(d_out)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if p.returncode != 0:
            print(f"WARNING: rembg failed ({(p.stderr or p.stdout).strip().splitlines()[-1:]}) — "
                  "matching on skin and other references only")
    out = {}
    for k, img in work:
        f = d_out / f"{k}.png"
        if f.exists():
            # Pulled in a little: the light model's edge runs a few pixels into the background.
            out[k] = ndimage.binary_erosion(read_gray(f, img.shape[1], img.shape[0]) > 0.5, iterations=3)
    return out


# ---------------------------------------------------------------- regions

def person_regions(lab: np.ndarray, box, person: np.ndarray | None) -> dict[str, np.ndarray]:
    """Skin (cheeks and nose), hair (around the top of the head) and clothes (below the chin),
    from the face box and the person's cut-out. Without a cut-out, only skin."""
    h, w = lab.shape[:2]
    x, y, bw, bh = box[:4]
    X, Y, BW, BH = x * w, y * h, bw * w, bh * h
    yy, xx = np.mgrid[0:h, 0:w]

    def rect(x0, x1, y0, y1):
        return (xx >= X + x0 * BW) & (xx < X + x1 * BW) & (yy >= Y + y0 * BH) & (yy < Y + y1 * BH)
    L = lab[..., 0]
    skin = rect(0.2, 0.8, 0.35, 0.62) & (L > 8) & (L < 97)
    if person is not None:
        skin &= person
    if skin.sum() < 80:
        return {}
    near_skin = np.linalg.norm(lab - np.median(lab[skin], 0), axis=-1) < 14
    out = {"skin": skin & near_skin}            # no beard, glasses or shadows under the cheekbone
    if person is None:
        return out
    hair = rect(-0.25, 1.25, -0.4, 0.35) & person & ~rect(0.1, 0.9, 0.2, 1.05) & ~near_skin
    if hair.sum() >= 120:
        out["hair"] = hair
    clothes = rect(-0.9, 1.9, 1.4, 3.2) & person & ~near_skin
    if clothes.sum() >= 300:
        out["clothes"] = clothes          # split into garments later, the same way in every shot
    return out


def split_clothes(data: dict) -> None:
    """A dark and a light garment (sweater and jeans) are two references — decided once for all
    shots with one brightness threshold, so the same garment has the same name everywhere."""
    vals = [d["clothes_L"][k] for d in data.values() for k in d.get("clothes_L", {})]
    if not vals:
        return
    allL = np.concatenate(vals)
    thr = otsu(allL)
    lo, hi = allL[allL < thr], allL[allL >= thr]
    if min(len(lo), len(hi)) < 0.15 * len(allL) or np.median(hi) - np.median(lo) < 15:
        return                                                  # one garment, or one tone
    for d in data.values():
        items = d["regions"].pop("clothes", [])
        for i, reg in items:
            idx = np.flatnonzero(reg)
            Lv = d["clothes_L"][i]
            for name, sel in (("clothes_dark", Lv < thr), ("clothes_light", Lv >= thr)):
                if sel.sum() >= 150:
                    m = np.zeros(reg.shape, bool)
                    m.flat[idx[sel]] = True
                    d["regions"].setdefault(name, []).append((i, m))


def neutral_regions(lab: np.ndarray, exclude: np.ndarray) -> dict[str, np.ndarray]:
    """Last resort: the least saturated third of each tonal zone, outside the person."""
    L, C = lab[..., 0], np.hypot(lab[..., 1], lab[..., 2])
    out = {}
    for name, (lo, hi) in {"neutral_dark": (6, 30), "neutral_mid": (30, 70), "neutral_light": (70, 95)}.items():
        zone = (L >= lo) & (L < hi) & ~exclude
        if zone.sum() >= 300:
            out[name] = zone & (C <= np.percentile(C[zone], 33))
    return out


def snap(lab: np.ndarray, x: float, y: float, tol: float, window: float = 0.18) -> np.ndarray | None:
    """The surface around a point: connected pixels of similar color, near the point."""
    h, w = lab.shape[:2]
    px, py = min(int(x * w), w - 1), min(int(y * h), h - 1)
    # Compared on a smoothed copy: the surface's own texture doesn't break it apart, and a
    # tight tolerance keeps it from leaking into a similar background.
    soft = ndimage.gaussian_filter(lab, (2.5, 2.5, 0))
    seed = np.median(soft[max(py - 3, 0):py + 4, max(px - 3, 0):px + 4].reshape(-1, 3), 0)
    yy, xx = np.mgrid[0:h, 0:w]
    near = (np.linalg.norm(soft - seed, axis=-1) < tol) & (abs(xx - px) < window * w) & (abs(yy - py) < window * h)
    lbl, _ = ndimage.label(near)
    if lbl[py, px] == 0:
        return None
    reg = ndimage.binary_erosion(lbl == lbl[py, px], iterations=1)   # edges blend with the neighbors
    return reg if reg.sum() >= 100 else None


# ------------------------------------------------------------ measurement

def stats(samples: list[tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]]) -> dict | None:
    """samples: (display rgb, lab, high-pass L, region) per frame."""
    pix, labs, texs, meds = [], [], [], []
    for img, lab, hp, reg in samples:
        if reg.sum() < 50:
            continue
        pix.append(img[reg])
        labs.append(lab[reg])
        inner = ndimage.binary_erosion(reg, iterations=2)       # texture inside, not the region's edge
        med = float(np.median(lab[reg][:, 0]))
        # Texture as contrast in log light (L* per doubling of light at this level): the same
        # surface reads the same in a darker or brighter shot; only detail actually lost lowers it.
        texs.append(float(np.std(hp[inner if inner.sum() >= 30 else reg])) / tex_scale(med))
        meds.append(med)
    if not pix:
        return None
    P, Lb = np.concatenate(pix), np.concatenate(labs)
    L = Lb[:, 0]
    lo, hi = np.percentile(L, [10, 90])
    core = (L >= lo) & (L <= hi)
    return {"L": round(float(np.median(L)), 2), "a": round(float(np.median(Lb[:, 1])), 2),
            "b": round(float(np.median(Lb[:, 2])), 2), "lin": to_lin(P[core]).mean(0).round(6).tolist(),
            "tex": round(float(np.median(texs)), 3), "spread": round(float(np.std(meds)), 2),
            "split": two_lights(meds),
            # Crushed to black or burnt to white: detail that isn't in the file any more.
            "clip": round(float(np.mean((L < 3) | (L > 97))), 4), "n": int(len(P)), "frames": len(pix)}


def two_lights(meds: list[float]) -> bool:
    """The shot spends a similar time under two different lights (half inside, half outside):
    one correction can't suit both. A short moment under other light doesn't count."""
    if len(meds) < 4:
        return False
    v = np.sort(meds)
    gaps = np.diff(v)
    k = int(np.argmax(gaps))
    return bool(gaps[k] > 8 and min(k + 1, len(v) - k - 1) >= 0.35 * len(v))


def measure(frames: list[np.ndarray], regions: dict[str, list[tuple[int, np.ndarray]]], fn=None) -> dict:
    labs, hps, imgs = {}, {}, {}
    out = {}
    for ref, items in regions.items():
        samples = []
        for i, reg in items:
            if i not in labs:
                imgs[i] = fn(frames[i]) if fn else frames[i]
                labs[i] = to_lab(imgs[i])
                hps[i] = labs[i][..., 0] - ndimage.gaussian_filter(labs[i][..., 0], 1.5)
            samples.append((imgs[i], labs[i], hps[i], reg))
        s = stats(samples)
        if s:
            out[ref] = s
    return out


def clipped(frames: list[np.ndarray], fn=None) -> float:
    """% of pixels at pure black or white (luma)."""
    y = np.concatenate([((fn(f) if fn else f).reshape(-1, 3) @ LUMA)[::3] for f in frames])
    return 100 * float(np.mean((y < 0.004) | (y > 0.996)))


# --------------------------------------------------------------------- fit

def zone_of(L: float) -> str:
    return "dark" if L < 30 else "light" if L > 70 else "mid"


def targets(meas: dict, refs: list[str], target_file: str | None) -> dict:
    T = {}
    for r in refs:
        vals = {f: meas[f][r] for f in meas if r in meas[f]}
        if len(vals) < 2:
            continue
        Ls = np.array([v["L"] for v in vals.values()])
        med = float(np.median(Ls))
        if target_file and target_file in vals:
            tL, ta, tb = vals[target_file]["L"], vals[target_file]["a"], vals[target_file]["b"]
        else:
            ta = float(np.median([v["a"] for v in vals.values()]))
            tb = float(np.median([v["b"] for v in vals.values()]))
            if med < 30:
                # Darks: as deep as the deepest shot that isn't crushed — black hair stays black.
                ok = [v["L"] for v in vals.values() if v["clip"] < 0.05]
                tL = max(min(ok) if ok else med, DARK_FLOOR)
            elif med > 70:
                # Lights: as bright as the brightest that isn't clipped, with headroom.
                ok = [v["L"] for v in vals.values() if v["clip"] < 0.01]
                tL = min(max(ok) if ok else med, LIGHT_CEIL)
            else:
                tL = med
        T[r] = {"L": round(float(tL), 2), "a": round(ta, 2), "b": round(tb, 2), "zone": zone_of(med)}
    return T


def fit_gains(items: list[tuple[list, float, float, float]]) -> np.ndarray:
    """items: (linear rgb, target a, target b, weight). Luminance-neutral gains."""
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
        return out + [0.5 * p[0], 0.5 * p[1]]                # small pull toward no change
    r = optimize.least_squares(res, [0.0, 0.0], bounds=([-MAX_GAIN_STOPS] * 2, [MAX_GAIN_STOPS] * 2))
    return gains_of(r.x)


def seg_rows(Ls: np.ndarray) -> np.ndarray:
    """y(L) = y0 + Σ slope_k · overlap(segment k, [0, L]) — linear in (y0, slopes)."""
    dx = np.diff(KNOTS)
    A = np.zeros((len(Ls), 1 + len(dx)))
    A[:, 0] = 1
    for k in range(len(dx)):
        A[:, 1 + k] = np.clip(Ls - KNOTS[k], 0, dx[k])
    return A


def fit_curve(pts: list[tuple[float, float, float]], protect: list[float], white: float = 100.0) -> np.ndarray:
    """pts: (L now, L target, weight); white: where the original white sits after exposure.
    Bounded slopes make it monotone and gentle by construction."""
    dx = np.diff(KNOTS)
    K = len(dx)
    lo, hi = np.full(K, SLOPE[0]), np.full(K, SLOPE[1])
    sh = KNOTS[1:] <= 25
    lo[sh], hi[sh] = SLOPE_SHADOWS
    over = KNOTS[:-1] >= 100                                 # the shoulder for what went past white
    lo[over], hi[over] = 0.05, 1.0
    for Lp in protect:
        near = (KNOTS[1:] > Lp - 6) & (KNOTS[:-1] < Lp + 6)
        lo[near] = np.maximum(lo[near], SLOPE_TEXTURE)
    hi = np.maximum(hi, lo)
    rows, rhs = [], []
    for a, (_, Lt, w) in zip(seg_rows(np.array([p[0] for p in pts])), pts):
        rows.append(w * a)
        rhs.append(w * Lt)
    for k in range(K - 1):                                   # smooth: neighbor slopes alike
        r = np.zeros(1 + K)
        r[1 + k], r[2 + k] = -8, 8
        rows.append(r)
        rhs.append(0)
    for k in range(K):                                       # close to the original where nothing asks
        r = np.zeros(1 + K)
        r[1 + k] = 2
        rows.append(r)
        rhs.append(2)
    # The original white stays white (no new clipping, no dimmed whites), wherever exposure moved it.
    rows += [5 * seg_rows(np.array([white]))[0], np.r_[1, np.zeros(K)]]
    rhs += [5 * 100, 0]
    sol = optimize.lsq_linear(np.array(rows), np.array(rhs), bounds=(np.r_[0, lo], np.r_[4, hi])).x
    y = np.r_[sol[0], sol[0] + np.cumsum(sol[1:] * dx)]
    return np.round(np.minimum(y, 100.0), 3)


def fit_zones(items: list[tuple[float, float, float, float]]) -> list | None:
    """items: (target L, Δa, Δb, weight) still missing after balance + curve."""
    if not items:
        return None
    W = np.array([zone_weights(np.array(L)) * w for L, _, _, w in items])
    D = np.array([[da * w, db * w] for _, da, db, w in items])
    A = np.vstack([W, 0.7 * np.eye(3)])
    off = np.linalg.lstsq(A, np.vstack([D, np.zeros((3, 2))]), rcond=None)[0]
    off = np.clip(off, -MAX_ZONE_OFFSET, MAX_ZONE_OFFSET)
    return np.round(off, 2).tolist() if np.abs(off).max() >= 0.5 else None


def solve_file(name: str, frames, regions, before: dict, T: dict, prio: dict, zones_on: bool) -> dict:
    usable, notes, split = [], [], []
    for r, s in before.items():
        if r not in T:
            continue
        if s["n"] < 150:
            notes.append(f"{r}: too small here — not used")
        elif s["clip"] > LOST:
            notes.append(f"{r}: detail lost at the source in this shot (crushed or clipped) — it can't be "
                         "recovered, so it isn't forced")
        else:
            usable.append(r)
            # The light changing within the shot doesn't stop it: one correction for the whole
            # shot, set by what's most common (medians). Only an even split is worth a warning.
            if s.get("split"):
                split.append(r)
            elif s["spread"] > UNSTABLE:
                notes.append(f"{r}: the light on it changes within the shot (spread {s['spread']}) — "
                             "corrected for the most common light")
    if not usable:
        return {"status": "not matched", "params": None, "used": [], "dropped": [], "notes": notes,
                "warnings": ["no reference shared with the other shots — left to the shared correction"]}
    dropped, protect_extra = [], []
    for _ in range(12):
        w = {r: prio[r] for r in usable}
        gains = fit_gains([(before[r]["lin"], T[r]["a"], T[r]["b"], w[r]) for r in usable])
        sub = {r: regions[r] for r in usable}
        wb = measure(frames, sub, transform({"gains": gains.tolist()}))
        # Exposure first (one gain for the whole picture, keeps the shape); the curve only fixes
        # what exposure can't: crushed or lifted blacks, contrast.
        stops = sorted((float(np.log2(Y_of_L(T[r]["L"]) / max(Y_of_L(wb[r]["L"]), 1e-5))), w[r]) for r in usable)
        acc, half, exposure = 0.0, sum(x[1] for x in stops) / 2, 0.0
        for v, wt in stops:                                  # weighted median
            acc += wt
            if acc >= half:
                exposure = float(np.clip(v, -MAX_EXPOSURE, MAX_EXPOSURE))
                break
        base = {"gains": np.round(gains, 5).tolist(), "exposure": round(exposure, 3)}
        wb = measure(frames, sub, transform(base))
        protect = [wb[r]["L"] for r in usable if r == "skin"] + protect_extra
        curve = fit_curve([(wb[r]["L"], T[r]["L"], w[r]) for r in usable], protect,
                          white=float(min(L_of_Y(2.0 ** exposure), L_MAX)))
        params = {**base, "curve": curve.tolist()}
        fn = PchipInterpolator(KNOTS, curve)
        res = {r: float(fn(wb[r]["L"]) - T[r]["L"]) for r in usable}
        bad = [r for r in usable if abs(res[r]) > FIT_TOL]
        if bad and len(usable) > 1:
            worst = max(bad, key=lambda r: abs(res[r]) / w[r])
            dropped.append({"ref": worst, "why": f"conflicts with the other references (left {res[worst]:+.1f} L* "
                            "off): lit differently in this shot, or too close in brightness to a stronger one"})
            usable.remove(worst)
            continue
        if zones_on:
            cur = measure(frames, {r: regions[r] for r in usable}, transform(params))
            params["zones"] = fit_zones([(T[r]["L"], T[r]["a"] - cur[r]["a"], T[r]["b"] - cur[r]["b"], w[r])
                                         for r in usable])
        after = measure(frames, {r: regions[r] for r in usable}, transform(params))
        # Rule 2, checked on the result: texture kept; if not, protect it and refit once.
        thin = [r for r in usable if after[r]["tex"] < before[r]["tex"] * (0.85 if r == "skin" else TEXTURE_KEEP)]
        if thin and not protect_extra:
            protect_extra = [wb[r]["L"] for r in thin]
            continue
        break
    final = transform(params)
    after = measure(frames, regions, final)
    warnings = []
    if not gray_ramp_ok(final):                              # rule 1 (can't happen by construction)
        return {"status": "not matched", "params": None, "used": usable, "dropped": dropped, "notes": notes,
                "warnings": ["the curve would invert brightness — not applied"]}
    for r in usable:
        keep = after[r]["tex"] / max(before[r]["tex"], 1e-6)
        if keep < (0.85 if r == "skin" else TEXTURE_KEEP):
            warnings.append(f"{r} keeps only {keep:.0%} of its texture")
    new_clip = clipped(frames, final) - clipped(frames)
    if new_clip > MAX_NEW_CLIP:
        warnings.append(f"{new_clip:.1f}% more pixels at pure black/white than the original")
    left = [abs(after[r]["L"] - T[r]["L"]) for r in usable]
    if max(left) > FIT_TOL:
        warnings.append(f"still {max(left):.1f} L* off on {usable[int(np.argmax(left))]}")
    if len(usable) == 1:
        warnings.append(f"only one reference ({usable[0]}): the rest of the tonal range isn't matched")
    if split:
        warnings.append(f"the light changes halfway through the shot ({', '.join(split)}): one correction "
                        "can't suit both halves — say so in the delivery")
    if all(r.startswith("neutral_") for r in usable):
        warnings.append("matched on neutral surfaces only (low confidence: backgrounds differ between shots)")
    return {"status": "partial" if warnings else "ok", "params": params, "used": usable, "dropped": dropped,
            "notes": notes, "warnings": warnings, "after": after, "new_clip_pct": round(new_clip, 2)}


# ------------------------------------------------------------------ sheets

def resize(img: np.ndarray, h: int) -> np.ndarray:
    z = h / img.shape[0]
    return np.clip(ndimage.zoom(img, (z, z, 1), order=1), 0, 1)


def save_sheet(path: Path, rows: list[np.ndarray], labels: list[str]) -> None:
    W = max(r.shape[1] for r in rows)
    rows = [np.pad(r, ((0, 0), (0, W - r.shape[1]), (0, 0))) for r in rows]
    sheet = np.concatenate(rows, 0)
    h, w = sheet.shape[:2]
    font = find_font()
    draw, y = [], 0
    for r, txt in zip(rows, labels):
        txt = txt.replace(":", " ").replace("'", "")
        draw.append(f"drawtext={'fontfile=' + chr(39) + filter_path(font) + chr(39) + ':' if font else ''}"
                    f"text='{txt}':x=10:y={y + 8}:fontsize=20:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=6")
        y += r.shape[0]
    subprocess.run([tool("ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                    "-i", "-", "-vf", ",".join(draw) or "null", "-q:v", "3", str(path)],
                   input=(sheet * 255).astype(np.uint8).tobytes(), check=True)


def bbox(mask: np.ndarray, pad: float = 0.12) -> tuple[slice, slice]:
    ys, xs = np.nonzero(mask)
    h, w = mask.shape
    py, px = int((ys.max() - ys.min()) * pad) + 4, int((xs.max() - xs.min()) * pad) + 4
    return slice(max(ys.min() - py, 0), min(ys.max() + py, h)), slice(max(xs.min() - px, 0), min(xs.max() + px, w))


PERSON = ("skin", "hair", "clothes", "clothes_dark", "clothes_light")
CELL_H, CELL_W = 200, 260


def cell(img: np.ndarray) -> np.ndarray:
    """A crop at a fixed height, centered and capped in width."""
    c = resize(img, CELL_H)
    if c.shape[1] > CELL_W:
        x0 = (c.shape[1] - CELL_W) // 2
        c = c[:, x0:x0 + CELL_W]
    return np.pad(c, ((0, 0), (0, CELL_W - c.shape[1]), (0, 0)))


def pair(d: dict, fn, items: list[tuple[int, np.ndarray]]) -> np.ndarray:
    """Before | after of the frame where the region is largest, cropped to it."""
    i, reg = max(items, key=lambda x: int(x[1].sum()))
    sy, sx = bbox(reg)
    crop = d["frames"][i][sy, sx]
    return np.concatenate([cell(crop), np.zeros((CELL_H, 3, 3)), cell(fn(crop))], 1)


def sheets(mdir: Path, items, data: dict, results: dict, T: dict, lut_for, faces: dict) -> None:
    """sheet.jpg: one row per reference — the same thing in every shot, before | after.
    regions.jpg: what was measured in each shot. faces.jpg: faces at full resolution."""
    fns = {m["name"]: transform(results[m["name"]]["params"]) for m in items}
    blank = np.zeros((CELL_H, 2 * CELL_W + 3, 3))
    gap = np.zeros((CELL_H, 14, 3))
    rows, labels = [], []
    groups = [("person", PERSON)] + [(r, (r,)) for r in sorted(T, key=lambda r: T[r]["L"])]
    for label, refs in groups:
        cells, any_ = [], False
        for m in items:
            d = data[m["name"]]
            got = [(i, reg) for r in refs for i, reg in d["regions"].get(r, [])]
            if label == "person" and got:
                # The whole person: union of their regions on the frame that has most of them.
                i = max({i for i, _ in got}, key=lambda i: sum(j == i for j, _ in got))
                got = [(i, np.logical_or.reduce([reg for j, reg in got if j == i]))]
            cells.append(pair(d, fns[m["name"]], got) if got else blank)
            any_ |= bool(got)
        if any_:
            rows.append(np.concatenate([x for c in cells for x in (c, gap)][:-1], 1))
            used = " · ".join(f"{Path(m['name']).stem}{'' if label == 'person' or label in results[m['name']]['used'] else ' (not used)'}"
                              for m in items)
            labels.append(f"{label} — {used} — each: before | after")
    if rows:
        save_sheet(mdir / "sheet.jpg", rows, labels)

    reg_rows, reg_labels, face_rows, face_labels = [], [], [], []
    for m in items:
        d, res = data[m["name"]], results[m["name"]]
        idx = [i for v in d["regions"].values() for i, _ in v]
        if not idx:
            continue
        i = max(set(idx), key=idx.count)
        over = d["frames"][i].copy()
        for k, (r, v) in enumerate(sorted(d["regions"].items())):
            for j, reg in v:
                if j == i:
                    over[reg] = over[reg] * 0.35 + np.array(COLORS[k % len(COLORS)]) / 255 * 0.65
        reg_rows.append(resize(over, 300))
        reg_labels.append(f"{m['name']} · {res['status']} · " + ", ".join(
            f"{r}" for k, r in enumerate(sorted(d["regions"])) if any(j == i for j, _ in d["regions"][r])))
        with_face = [t for t in d["times"] if faces_at(faces, m["name"], t)]
        if with_face:
            t = with_face[len(with_face) // 2]
            x, y, bw, bh = max(faces_at(faces, m["name"], t), key=lambda b: b[3])[:4]
            W, H = m["display_width"], m["display_height"]
            full = grab(source(m), t, W - W % 2, H - H % 2)
            if lut_for(m) is not None:
                full = apply_cube(full, lut_for(m))
            cy, cx = (y + bh / 2) * full.shape[0], (x + bw / 2) * full.shape[1]
            half = max(bw * full.shape[1], bh * full.shape[0]) * 0.8
            crop = full[int(max(cy - half, 0)):int(cy + half), int(max(cx - half, 0)):int(cx + half)]
            face_rows.append(np.concatenate([resize(crop, 512), np.zeros((512, 6, 3)),
                                             resize(fns[m["name"]](crop), 512)], 1))
            face_labels.append(f"{m['name']} · before | after")
    if reg_rows:
        save_sheet(mdir / "regions.jpg", reg_rows, reg_labels)
    if face_rows:
        save_sheet(mdir / "faces.jpg", face_rows, face_labels)


# ------------------------------------------------------------------- solve

def refs_file(mdir: Path) -> Path:
    return mdir / "refs.json"


def load_refs(mdir: Path) -> dict:
    p = refs_file(mdir)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"added": {}, "drop": []}


def cache_key(items, args, refs: dict, edl: dict | None) -> str:
    parts = [[m["name"], Path(m["file"]).stat().st_size if Path(m["file"]).exists() else 0,
              Path(m["file"]).stat().st_mtime if Path(m["file"]).exists() else 0] for m in items]
    parts += [getattr(args, "input_lut", None), getattr(args, "force_log", False), getattr(args, "match_target", None),
              getattr(args, "zones", "auto"), getattr(args, "no_person", False), refs, FRAMES, KNOTS.tolist(),
              MASK_MODEL, [used_ranges(m, edl) for m in items]]
    return hashlib.sha1(json.dumps(parts, default=str).encode()).hexdigest()[:16]


def solve(items: list[dict], args) -> dict:
    """Returns {file name: {"status", "params", ...}} and writes match.json / match.md / sheets."""
    mdir = Path(getattr(args, "match_dir", None) or "02-work/color/match")
    mdir.mkdir(parents=True, exist_ok=True)
    refs_cfg = load_refs(mdir)
    ep = Path(getattr(args, "edl", None) or "02-work/edl.json")
    edl = json.loads(ep.read_text(encoding="utf-8")) if ep.exists() else None
    key = cache_key(items, args, refs_cfg, edl)
    mj = mdir / "match.json"
    if mj.exists():
        old = json.loads(mj.read_text(encoding="utf-8"))
        if old.get("key") == key:
            return old["files"]
    ingest = Path(getattr(args, "ingest", "01-ingest"))
    faces = json.loads((ingest / "faces.json").read_text(encoding="utf-8")) if (ingest / "faces.json").exists() else {}
    lut = read_cube(Path(args.input_lut)) if getattr(args, "input_lut", None) else None
    lut_for = lambda m: lut if is_log(m, args) else None  # noqa: E731

    data = {}
    n = FRAMES if len(items) <= 8 else 4
    for m in items:
        times = pick_times(m, faces, n, used_ranges(m, edl))
        data[m["name"]] = {"times": times, "frames": [load(m, t, lut_for(m)) for t in times], "regions": {},
                           "clothes_L": {}}
    any_face = any(faces_at(faces, m["name"], t) for m in items for t in data[m["name"]]["times"])
    masks = {}
    if any_face and not getattr(args, "no_person", False):
        masks = person_masks([(f"{Path(m['name']).stem}_{t:.2f}", img) for m in items
                              for t, img in zip(data[m["name"]]["times"], data[m["name"]]["frames"])], mdir)

    for m in items:
        d = data[m["name"]]
        for i, (t, img) in enumerate(zip(d["times"], d["frames"])):
            lab = to_lab(img)
            person = masks.get(f"{Path(m['name']).stem}_{t:.2f}")
            boxes = faces_at(faces, m["name"], t)
            regs = person_regions(lab, max(boxes, key=lambda b: b[3]), person) if boxes else {}
            exclude = person if person is not None else np.zeros(lab.shape[:2], bool)
            if boxes and person is None:
                x, y, bw, bh = max(boxes, key=lambda b: b[3])[:4]
                h, w = lab.shape[:2]
                exclude = np.zeros((h, w), bool)
                exclude[int(max(y - bh, 0) * h):int(min(y + 4 * bh, 1) * h),
                        int(max(x - bw, 0) * w):int(min(x + 2 * bw, 1) * w)] = True
            regs.update(neutral_regions(lab, exclude))
            if "clothes" in regs:
                d["clothes_L"][i] = lab[..., 0][regs["clothes"]]
            for r, reg in regs.items():
                d["regions"].setdefault(r, []).append((i, reg))
        # References added by looking (snapped to the surface around the point, a few frames around t).
        for r, where in refs_cfg.get("added", {}).items():
            spot = where.get(m["name"])
            if not spot:
                continue
            tol = spot.get("tolerance", 6.0)
            for dt in (-0.4, 0.0, 0.4):
                img = load(m, spot["t"] + dt, lut_for(m))
                reg = snap(to_lab(img), spot["x"], spot["y"], tol)
                if reg is not None:
                    d["frames"].append(img)
                    d["regions"].setdefault(r, []).append((len(d["frames"]) - 1, reg))
    split_clothes(data)
    for m in items:
        for drop in refs_cfg.get("drop", []):
            if drop.get("file") in (None, m["name"]):
                data[m["name"]]["regions"].pop(drop["name"], None)

    before = {m["name"]: measure(data[m["name"]]["frames"], data[m["name"]]["regions"]) for m in items}
    names = sorted({r for b in before.values() for r in b})
    shared = [r for r in names if not r.startswith("neutral_") and sum(r in b for b in before.values()) >= 2]
    use = shared if len(shared) >= 2 else names       # neutral surfaces only as a last resort
    target_file = getattr(args, "match_target", None)
    T = targets(before, use, target_file)
    prio = {r: (PRIORITY["skin"] if r == "skin" else PRIORITY["neutral"] if r.startswith("neutral_")
                else refs_cfg.get("added", {}).get(r, {}).get("_priority") or PRIORITY[T[r]["zone"]]) for r in T}
    cams = {json.dumps([m.get("camera"), m.get("log_hint"), (m.get("color") or {}).get("transfer"),
                        (m.get("color") or {}).get("primaries")]) for m in items}
    zones_mode = getattr(args, "zones", "auto")
    zones_on = zones_mode == "on" or (zones_mode == "auto" and len(cams) > 1)

    results = {}
    for m in items:
        d = data[m["name"]]
        b = {r: v for r, v in before[m["name"]].items() if r in T}
        results[m["name"]] = solve_file(m["name"], d["frames"], d["regions"], b, T, prio, zones_on)
        res = results[m["name"]]
        print(f"match {m['name']}: {res['status']} · refs {', '.join(res['used']) or '—'}"
              + (f" · dropped {', '.join(x['ref'] for x in res['dropped'])}" if res["dropped"] else "")
              + (f" · {'; '.join(res['warnings'])}" if res["warnings"] else ""))

    report(mdir, items, before, results, T, zones_on, target_file)
    try:
        sheets(mdir, items, data, results, T, lut_for, faces)
    except Exception as e:  # the sheet is a check, never a reason to stop the grade
        print(f"WARNING: comparison sheet failed: {e}")
    files = {n: {k: v for k, v in r.items() if k != "after"} for n, r in results.items()}
    mj.write_text(json.dumps({"key": key, "targets": T, "zones": zones_on, "files": files,
                              "measured": {"before": before, "after": {n: r.get("after") for n, r in results.items()}}},
                             indent=1), encoding="utf-8")
    return files


def report(mdir: Path, items, before, results, T, zones_on, target_file) -> None:
    names = [m["name"] for m in items]
    lines = ["# Shot matching", "",
             f"Target: {'shot ' + target_file if target_file else 'neutral base with headroom'}"
             f" · color per tonal zone: {'on (different cameras)' if zones_on else 'off'}", "",
             "| reference | target L* | " + " | ".join(names) + " | spread before → after |",
             "|---|---|" + "---|" * len(names) + "---|"]
    for r, t in T.items():
        cells, b_vals, a_vals = [], [], []
        for n in names:
            b = before[n].get(r)
            a = (results[n].get("after") or {}).get(r) if r in results[n]["used"] else None
            if b is None:
                cells.append("—")
                continue
            b_vals.append(b["L"])
            if a:
                a_vals.append(a["L"])
                cells.append(f"{b['L']:.1f} → {a['L']:.1f} (tex {a['tex'] / max(b['tex'], 1e-6):.0%})")
            else:
                a_vals.append(b["L"])
                cells.append(f"{b['L']:.1f} (not used)")
        spread = lambda v: f"{max(v) - min(v):.1f}" if len(v) > 1 else "—"  # noqa: E731
        lines.append(f"| {r} | {t['L']:.1f} | " + " | ".join(cells) + f" | {spread(b_vals)} → {spread(a_vals)} |")
    lines += ["", "tex = share of the reference's texture kept (detail).", ""]
    for n in names:
        res = results[n]
        lines.append(f"## {n}: {res['status']}")
        for x in res["dropped"]:
            lines.append(f"- dropped {x['ref']}: {x['why']}")
        for x in res["notes"]:
            lines.append(f"- {x}")
        for x in res["warnings"]:
            lines.append(f"- WARNING: {x}")
        if res.get("params"):
            p = res["params"]
            lines.append(f"- gains {p['gains']} · exposure {p.get('exposure', 0):+.2f} stops · curve {p['curve']}"
                         + (f" · zones {p['zones']}" if p.get("zones") else ""))
        lines.append("")
    lines += ["Check before rendering: sheet.jpg (each reference in every shot, before | after), faces.jpg",
              "(faces at full resolution: no plastic, flat skin) and regions.jpg (what was measured).",
              "Add a reference: match.py add --name <ref> --file <file> --t <s> --at <x>,<y> (once per file);",
              "leave one out: match.py drop --name <ref> [--file <file>]; then grade.py build again."]
    (mdir / "match.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


# --------------------------------------------------------------------- CLI

def cmd_add(args) -> None:
    mdir = Path(args.match_dir)
    mdir.mkdir(parents=True, exist_ok=True)
    cfg = load_refs(mdir)
    x, y = (float(v) for v in args.at.split(","))
    material = load_material(Path(args.ingest))
    m = resolve([args.file], material)[0]
    lut = read_cube(Path(args.input_lut)) if args.input_lut and is_log(m, args) else None
    img = load(m, args.t, lut)
    reg = snap(to_lab(img), x, y, args.tolerance)
    if reg is None:
        die(f"nothing to measure at {args.at} in {m['name']} @ {args.t}s: pick a flatter spot, or raise --tolerance")
    spot = {"t": args.t, "x": x, "y": y, "tolerance": args.tolerance}
    entry = cfg.setdefault("added", {}).setdefault(args.name, {})
    entry[m["name"]] = spot
    if args.priority:
        entry["_priority"] = args.priority
    refs_file(mdir).write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    print(f"{args.name} in {m['name']}: {int(reg.sum())} px around ({x:.2f}, {y:.2f}) @ {args.t}s")


def cmd_drop(args) -> None:
    mdir = Path(args.match_dir)
    cfg = load_refs(mdir)
    d = {"name": args.name, "file": args.file}
    cfg["drop"] = [x for x in cfg.get("drop", []) if x != d]
    if not args.restore:
        cfg["drop"].append(d)
    mdir.mkdir(parents=True, exist_ok=True)
    refs_file(mdir).write_text(json.dumps(cfg, indent=1), encoding="utf-8")
    print(f"{'restored' if args.restore else 'dropped'} {args.name}" + (f" in {args.file}" if args.file else ""))


def cmd_solve(args) -> None:
    items = resolve(args.files, load_material(Path(args.ingest)))
    if len(items) < 2:
        die("matching needs at least two files")
    solve(items, args)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("solve", "add", "drop"):
        s = sub.add_parser(name)
        s.add_argument("--ingest", default="01-ingest")
        s.add_argument("--match-dir", default="02-work/color/match")
        s.add_argument("--input-lut")
        s.add_argument("--force-log", action="store_true")
        if name == "solve":
            s.add_argument("files", nargs="*")
            s.add_argument("--target", dest="match_target", help="match to this shot instead of the neutral base")
            s.add_argument("--zones", choices=("auto", "on", "off"), default="auto",
                           help="color per tonal zone: auto = only when the cameras differ")
            s.add_argument("--no-person", action="store_true", help="don't cut out the person (no rembg)")
            s.add_argument("--edl", default="02-work/edl.json", help="measure only what the edit uses (if it exists)")
        if name == "add":
            s.add_argument("--name", required=True)
            s.add_argument("--file", required=True)
            s.add_argument("--t", type=float, required=True, help="time in the source file (s)")
            s.add_argument("--at", required=True, help="x,y of the point, 0-1 of the frame")
            s.add_argument("--tolerance", type=float, default=6.0, help="color distance that counts as the same surface")
            s.add_argument("--priority", type=float, help="weight when references clash (skin is 3, darks/lights 1.5)")
        if name == "drop":
            s.add_argument("--name", required=True)
            s.add_argument("--file")
            s.add_argument("--restore", action="store_true")
    args = ap.parse_args()
    {"solve": cmd_solve, "add": cmd_add, "drop": cmd_drop}[args.cmd](args)


if __name__ == "__main__":
    main()
