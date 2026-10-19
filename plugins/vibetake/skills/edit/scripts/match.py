# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26", "scipy>=1.11"]
# ///
"""Shot matching: look at the shots side by side, group what belongs together, match
inside each group, nudge by hand — with the rules enforced by the code.

    uv run match.py solve [files...]                  # sheet + current matching (run again after each change)
    uv run match.py group --name kitchen A.mp4 B.mp4  # same place and light → matched to each other
           [--no-skin]                                # different people: skin isn't a shared reference
    uv run match.py skip  C.mp4 [--restore]           # not camera footage (screen, graphics, archive): untouched
    uv run match.py add   --name sign --file A.mp4 --t 3.2 --at 0.42,0.31   # point at something to measure
    uv run match.py drop  --name sign [--file A.mp4] [--restore]
    uv run match.py set   --file A.mp4 exposure=+0.3 black=-2 white=+3 temp=-4 tint=+2 contrast=+0.1 [--clear]

Nothing is decided from where things usually are in a frame: what belongs together, who
is who, what isn't camera footage and which light is intentional is decided by looking
at `sheet.jpg`. The code measures, fits and checks:

- In a group, every shot is brought to the group's common base: white balance (gains),
  exposure and a smooth tone curve, from shared references — the skin of the person
  (automatic, from the face boxes) and anything pointed at with `add`. A small color
  offset per tonal zone is added when the group mixes cameras. Target: a neutral base
  with headroom — skin and mids at the shots' middle value, darks as deep as the deepest
  shot that isn't crushed, lights as bright as the brightest that isn't burnt.
- A shot in no group keeps its own look (it gets its own correction in grade.py).
- `set` nudges a shot by hand, on top.

Rules enforced on everything applied (not left to judgment):
 1. brightness order never inverts (the curve always rises),
 2. detail is kept (texture of skin and references measured before/after),
 3. no new crushed blacks or burnt whites,
 4. gentle changes (bounded slopes, gains, offsets and nudges),
 5. what can't be matched well is flagged instead of forced.

Output in 02-work/color/match/: sheet*.jpg (every shot, before | after, with what was
measured), faces.jpg (faces at full resolution), match.md, match.json (read by grade.py).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage, optimize
from scipy.interpolate import PchipInterpolator

sys.path.insert(0, str(Path(__file__).parent))
from _common import die, filter_path, find_font, sdr_source, tool  # noqa: E402
from grade import (LUMA, TEMP_DIR, TINT_DIR, apply_cube, balance_gains, faces_at, from_lab, grab,  # noqa: E402
                   load_material, read_cube, resolve, to_disp, to_lab, to_lin)

WIDTH = 640                      # analysis width
FRAMES = 6                       # frames measured per shot (4 when there are many shots)
# Tone curve, in L*. Past 100: what a positive exposure pushed over white, rolled back under it.
KNOTS = np.array([0, 8, 16, 25, 35, 45, 55, 65, 75, 85, 93, 100, 115, 140], float)
L_MAX = float(KNOTS[-1])
SLOPE = (0.55, 1.7)              # rule 4: the curve (after exposure) never much flatter or steeper
SLOPE_SHADOWS = (0.5, 1.4)       # below L* 25: flatter undoes lifted blacks; steep lifts noise and blocks
SLOPE_TEXTURE = 0.85             # rule 2: around skin contrast stays
TEXTURE_KEEP = 0.75              # rule 2: share of a reference's texture that must survive (skin 0.85)
LOST = 0.3                       # share of a reference crushed or burnt: its detail is lost at the source
MAX_EXPOSURE = 1.5               # rule 4: stops
MAX_ZONE_OFFSET = 5.0            # rule 4: a, b units per tonal zone
MAX_NEW_CLIP = 0.5               # rule 3: percentage points of newly clipped pixels
FIT_TOL = 4.0                    # L*: a reference left further than this from its target conflicts
UNSTABLE = 6.0                   # L* spread across a shot's frames: the light on it changes (noted)
DARK_FLOOR, LIGHT_CEIL = 8.0, 92.0   # the base keeps texture in the darks and headroom in the lights
PRIORITY = {"skin": 3.0, "dark": 1.5, "light": 1.5, "mid": 1.0}
# Rule 4 for hand nudges: exposure in stops, black/white in L*, temp/tint in a*b* units, contrast ±.
NUDGES = {"exposure": (-2.0, 2.0), "black": (-10.0, 10.0), "white": (-10.0, 10.0),
          "temp": (-20.0, 20.0), "tint": (-20.0, 20.0), "contrast": (-0.3, 0.3)}
COLORS = [(255, 80, 80), (80, 200, 255), (255, 210, 60), (120, 255, 120), (255, 120, 255), (255, 160, 60)]


# ------------------------------------------------------------------ helpers

def L_of_Y(Y):
    Y = np.asarray(Y, float)
    f = np.where(Y > 0.008856, np.cbrt(np.clip(Y, 0, None)), 7.787 * Y + 16 / 116)
    return 116 * f - 16


def Y_of_L(L):
    fy = (np.asarray(L, float) + 16) / 116
    return np.where(fy > 0.2069, fy ** 3, (fy - 16 / 116) / 7.787)


def tex_scale(L: float) -> float:
    """dL*/dlog2(Y) at a given L*: how many L* units one stop of light spans there.
    Floor: in near-black the "texture" is compression noise, not detail."""
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


def identity_curve() -> list[float]:
    return np.minimum(KNOTS, 100.0).tolist()


def transform(params: dict | None):
    """A shot's match as a function on display RGB (any shape ending in 3). None → identity."""
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


# ------------------------------------------------------------------- setup

def setup_path(mdir: Path) -> Path:
    return mdir / "setup.json"


def load_setup(mdir: Path) -> dict:
    p = setup_path(mdir)
    s = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    for k, v in (("groups", {}), ("skip", []), ("added", {}), ("drop", []), ("nudge", {})):
        s.setdefault(k, v)
    return s


def save_setup(mdir: Path, s: dict) -> None:
    mdir.mkdir(parents=True, exist_ok=True)
    setup_path(mdir).write_text(json.dumps(s, indent=1), encoding="utf-8")


def group_of(setup: dict, name: str) -> str | None:
    return next((g for g, v in setup["groups"].items() if name in v["files"]), None)


# ----------------------------------------------------------------- frames

def is_log(m: dict, args) -> bool:
    return bool(getattr(args, "input_lut", None) and (m.get("log_hint") or m.get("faces", {}).get("image", {})
                                                     .get("flat") or getattr(args, "force_log", False)))


def source(m: dict) -> tuple[str, str]:
    """The SDR picture: (file, filter) — see _common.sdr_source."""
    return sdr_source(m)


def frame_size(m: dict) -> tuple[int, int]:
    return WIDTH, int(round(WIDTH * m["display_height"] / m["display_width"] / 2)) * 2


def used_ranges(m: dict, edl: dict | None) -> list[tuple[float, float]]:
    """The parts of the file the edit keeps; the whole file when there's no edit yet."""
    segs = [(s["in"], s["out"]) for s in (edl or {}).get("segments", [])
            if s.get("name", Path(s.get("source", "")).name) == m["name"] and s["out"] > s["in"]]
    return segs or [(0.0, m["duration"])]


def pick_times(m: dict, faces: dict, n: int, ranges: list[tuple[float, float]]) -> list[float]:
    """Spread over what the edit uses, preferring times with a face: what's measured is what
    the viewer will see, and one correction per shot follows its most common light."""
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
    src, pre = source(m)
    img = grab(src, max(0.0, min(t, m["duration"] - 0.05)), w, h, pre)
    return apply_cube(img, lut) if lut is not None else img


# ---------------------------------------------------------------- regions

def skin_region(lab: np.ndarray, box) -> np.ndarray | None:
    """Cheeks and nose of the biggest face (no lips, eyes, beard or glasses)."""
    h, w = lab.shape[:2]
    x, y, bw, bh = box[:4]
    reg = np.zeros((h, w), bool)
    reg[int(max(y + 0.35 * bh, 0) * h):int(min(y + 0.62 * bh, 1) * h),
        int(max(x + 0.2 * bw, 0) * w):int(min(x + 0.8 * bw, 1) * w)] = True
    L = lab[..., 0]
    reg &= (L > 8) & (L < 97)
    if reg.sum() < 80:
        return None
    reg &= np.linalg.norm(lab - np.median(lab[reg], 0), axis=-1) < 14
    return reg if reg.sum() >= 80 else None


def snap(lab: np.ndarray, x: float, y: float, tol: float, window: float = 0.18) -> np.ndarray | None:
    """The surface around a point: connected pixels of similar color, near the point. Compared
    on a smoothed copy, so texture doesn't break it apart and a tight tolerance keeps it from
    leaking into a similar background."""
    h, w = lab.shape[:2]
    px, py = min(int(x * w), w - 1), min(int(y * h), h - 1)
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

def two_lights(meds: list[float]) -> bool:
    """The shot spends a similar time under two different lights (half inside, half outside):
    one correction can't suit both. A short moment under other light doesn't count."""
    if len(meds) < 4:
        return False
    v = np.sort(meds)
    gaps = np.diff(v)
    k = int(np.argmax(gaps))
    return bool(gaps[k] > 8 and min(k + 1, len(v) - k - 1) >= 0.35 * len(v))


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


def hint(frames: list[np.ndarray], fn=None) -> dict:
    """Whole-frame numbers for the sheet: only a hint (backgrounds differ), never matched on."""
    px = np.concatenate([(fn(f) if fn else f).reshape(-1, 3)[::5] for f in frames])
    lab = to_lab(px)
    C = np.hypot(lab[:, 1], lab[:, 2])
    mid = (lab[:, 0] > 20) & (lab[:, 0] < 85)
    neutral = lab[mid][C[mid] <= np.percentile(C[mid], 33)] if mid.sum() > 100 else lab[:1]
    a, b = float(np.median(neutral[:, 1])), float(np.median(neutral[:, 2]))
    return {"L50": round(float(np.median(lab[:, 0])), 1), "L2": round(float(np.percentile(lab[:, 0], 2)), 1),
            "L98": round(float(np.percentile(lab[:, 0], 98)), 1),
            "temp": round(float(np.dot([a, b], TEMP_DIR)), 1), "tint": round(float(np.dot([a, b], TINT_DIR)), 1)}


# --------------------------------------------------------------------- fit

def zone_of(L: float) -> str:
    return "dark" if L < 30 else "light" if L > 70 else "mid"


def targets(meas: dict, refs: list[str], target_file: str | None) -> dict:
    T = {}
    for r in refs:
        vals = {f: meas[f][r] for f in meas if r in meas[f] and meas[f][r]["clip"] <= LOST}
        if len(vals) < 2:
            continue
        med = float(np.median([v["L"] for v in vals.values()]))
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
                # Lights: as bright as the brightest that isn't burnt, with headroom.
                ok = [v["L"] for v in vals.values() if v["clip"] < 0.01]
                tL = min(max(ok) if ok else med, LIGHT_CEIL)
            else:
                tL = med
        T[r] = {"L": round(float(tL), 2), "a": round(ta, 2), "b": round(tb, 2), "zone": zone_of(med)}
    return T


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


def fit_shot(frames, regions, before: dict, T: dict, prio: dict, zones_on: bool) -> dict:
    """One shot of a group toward the group's targets."""
    usable, notes = [], []
    for r, s in before.items():
        if r not in T:
            continue
        if s["n"] < 150:
            notes.append(f"{r}: too small here — not used")
        elif s["clip"] > LOST:
            notes.append(f"{r}: detail lost at the source in this shot (crushed or burnt) — not forced")
        else:
            usable.append(r)
    if not usable:
        return {"status": "not matched", "params": None, "used": [], "dropped": [], "notes": notes,
                "warnings": ["nothing measured here is shared with the rest of the group — point at something "
                             "with `add`, or take it out of the group"]}
    dropped, protect_extra = [], []
    for _ in range(12):
        w = {r: prio[r] for r in usable}
        gains = balance_gains([(before[r]["lin"], T[r]["a"], T[r]["b"], w[r]) for r in usable])
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
            dropped.append({"ref": worst, "why": f"contradicts the other references (left {res[worst]:+.1f} L* "
                            "off): lit differently in this shot, or too close in brightness to a stronger one"})
            usable.remove(worst)
            continue
        if zones_on:
            cur = measure(frames, sub, transform(params))
            params["zones"] = fit_zones([(T[r]["L"], T[r]["a"] - cur[r]["a"], T[r]["b"] - cur[r]["b"], w[r])
                                         for r in usable])
        after = measure(frames, sub, transform(params))
        # Rule 2 on the result: texture kept; if not, protect it and refit once.
        thin = [r for r in usable if after[r]["tex"] < before[r]["tex"] * (0.85 if r == "skin" else TEXTURE_KEEP)]
        if thin and not protect_extra:
            protect_extra = [wb[r]["L"] for r in thin]
            continue
        break
    warnings = []
    if len(usable) == 1:
        warnings.append(f"only one reference ({usable[0]}): the rest of the tonal range follows it loosely")
    return {"status": "ok", "params": params, "used": usable, "dropped": dropped, "notes": notes,
            "warnings": warnings}


def nudged(params: dict | None, nudge: dict) -> dict:
    """Hand nudges on top of the matched params, all on the same curve (bounded, rule 4)."""
    p = {k: v for k, v in (params or {}).items()}
    g = np.asarray(p.get("gains", [1.0, 1.0, 1.0]), float)
    if nudge.get("temp") or nudge.get("tint"):
        d = nudge.get("temp", 0) * TEMP_DIR + nudge.get("tint", 0) * TINT_DIR
        g = g * balance_gains([(np.full(3, 0.18), float(d[0]), float(d[1]), 1.0)], pull=0.0)
    p["gains"] = np.round(g, 5).tolist()
    x = np.minimum(KNOTS, 100.0)
    y = np.asarray(p.get("curve") or identity_curve(), float)
    if nudge.get("exposure"):
        y = L_of_Y(Y_of_L(y) * 2.0 ** nudge["exposure"])
    if nudge.get("black"):
        y = y + nudge["black"] * np.clip(1 - x / 40, 0, 1) ** 1.5
    if nudge.get("white"):
        y = y + nudge["white"] * np.clip((x - 55) / 45, 0, 1) ** 1.5
    if nudge.get("contrast"):
        y = y + nudge["contrast"] * (y - 50) * np.clip(1 - ((y - 50) / 50) ** 2, 0, 1)
    y = np.where(y > 90, 90 + 10 * np.tanh((y - 90) / 10), y)          # a shoulder, not a hard clip
    p["curve"] = np.round(np.maximum.accumulate(np.clip(y, 0, 100)), 3).tolist()
    return p


def check(frames, regions, before: dict, params: dict | None) -> list[str]:
    """Rules 1–3 on the final transform of a shot (matching + nudges)."""
    if not params:
        return []
    fn = transform(params)
    if not gray_ramp_ok(fn):
        return ["the brightness order would invert — not applied"]
    out = []
    after = measure(frames, regions, fn)
    for r in after:
        if r in before:
            keep = after[r]["tex"] / max(before[r]["tex"], 1e-6)
            if keep < (0.85 if r == "skin" else TEXTURE_KEEP):
                out.append(f"{r} keeps only {keep:.0%} of its texture (flat, plastic)")
            elif r == "skin" and keep > 1.6:
                out.append(f"skin gets {keep - 1:.0%} more contrast (harsh) — look at faces.jpg")
    new_clip = clipped(frames, fn) - clipped(frames)
    if new_clip > MAX_NEW_CLIP:
        out.append(f"{new_clip:.1f}% more pixels crushed to black or burnt to white than the original")
    curve = np.asarray(params.get("curve") or identity_curve())
    slopes = np.diff(curve[KNOTS <= 100]) / np.diff(KNOTS[KNOTS <= 100])
    if slopes.min() < 0.3:
        out.append("part of the tonal range is nearly flattened (detail lost there)")
    return out


# ------------------------------------------------------------------ sheets

def resize(img: np.ndarray, h: int) -> np.ndarray:
    z = h / img.shape[0]
    return np.clip(ndimage.zoom(img, (z, z, 1), order=1), 0, 1)


def save_sheet(path: Path, canvas: np.ndarray, labels: list[tuple[int, int, str]]) -> None:
    h, w = canvas.shape[:2]
    font = find_font()
    ff = f"fontfile='{filter_path(font)}':" if font else ""
    draw = [f"drawtext={ff}text='{t.replace(':', ' ').replace(chr(39), '').replace('%', ' pct')}':x={x}:y={y}:"
            "fontsize=18:fontcolor=white:box=1:boxcolor=black@0.65:boxborderw=5" for x, y, t in labels]
    subprocess.run([tool("ffmpeg"), "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
                    "-i", "-", "-vf", ",".join(draw) or "null", "-q:v", "3", str(path)],
                   input=(np.clip(canvas, 0, 1) * 255).astype(np.uint8).tobytes(), check=True)


def outline(img: np.ndarray, regions: list[tuple[np.ndarray, tuple]]) -> np.ndarray:
    out = img.copy()
    for reg, color in regions:
        edge = reg & ~ndimage.binary_erosion(reg, iterations=2)
        out[edge] = np.array(color) / 255
    return out


def shot_sheets(mdir: Path, items, data: dict, results: dict, info: dict) -> list[Path]:
    """Every shot, before | after, with its group and what was measured outlined — the page
    to look at for grouping, skipping, pointing and nudging. Several pages for many shots."""
    for old in mdir.glob("sheet*.jpg"):
        old.unlink()
    H, per_page, cols = 300, 12, 3
    cells = []
    for m in items:
        d, res = data[m["name"]], results[m["name"]]
        i = d["rep"]
        img = d["frames"][i]
        regs = [(reg, COLORS[k % len(COLORS)]) for k, (r, v) in enumerate(sorted(d["regions"].items()))
                for j, reg in v if j == i]
        pair = np.concatenate([resize(outline(img, regs), H), np.zeros((H, 4, 3)),
                               resize(transform(res["params"])(img), H)], 1)
        h = info[m["name"]]
        txt = [f"{Path(m['name']).stem} @ {d['times'][i]:.2f}s · {res.get('group') or 'no group'} · {res['status']}",
               f"before | after · L {h['L50']:.0f} · temp {h['temp']:+.0f} · tint {h['tint']:+.0f}"]
        cells.append((pair, txt))
    paths = []
    for page in range(0, len(cells), per_page):
        chunk = cells[page:page + per_page]
        cw = max(c[0].shape[1] for c in chunk) + 12
        rows = [chunk[k:k + cols] for k in range(0, len(chunk), cols)]
        canvas = np.zeros((len(rows) * (H + 12), cw * min(cols, len(chunk)), 3))
        labels = []
        for r, row in enumerate(rows):
            for c, (pair, txt) in enumerate(row):
                y0, x0 = r * (H + 12), c * cw
                canvas[y0:y0 + H, x0:x0 + pair.shape[1]] = pair
                labels += [(x0 + 6, y0 + 6, txt[0]), (x0 + 6, y0 + H - 30, txt[1])]
        p = mdir / (f"sheet_{page // per_page + 1}.jpg" if len(cells) > per_page else "sheet.jpg")
        save_sheet(p, canvas, labels)
        paths.append(p)
    return paths


def faces_sheet(mdir: Path, items, data: dict, results: dict, faces: dict, lut_for) -> None:
    """Faces at full resolution, before | after: flat or plastic skin shows here."""
    for old in mdir.glob("faces.jpg"):
        old.unlink()
    rows, labels, y = [], [], 0
    for m in items:
        res = results[m["name"]]
        if not res.get("params"):
            continue
        with_face = [t for t in data[m["name"]]["times"] if faces_at(faces, m["name"], t)]
        if not with_face:
            continue
        t = with_face[len(with_face) // 2]
        x, yb, bw, bh = max(faces_at(faces, m["name"], t), key=lambda b: b[3])[:4]
        W, H = m["display_width"], m["display_height"]
        full = grab(source(m)[0], t, W - W % 2, H - H % 2, source(m)[1])
        if lut_for(m) is not None:
            full = apply_cube(full, lut_for(m))
        cy, cx = (yb + bh / 2) * full.shape[0], (x + bw / 2) * full.shape[1]
        half = max(bw * full.shape[1], bh * full.shape[0]) * 0.8
        crop = full[int(max(cy - half, 0)):int(cy + half), int(max(cx - half, 0)):int(cx + half)]
        rows.append(np.concatenate([resize(crop, 512), np.zeros((512, 6, 3)),
                                    resize(transform(res["params"])(crop), 512)], 1))
        labels.append((8, y + 8, f"{m['name']} · before | after"))
        y += 512
    if rows:
        W = max(r.shape[1] for r in rows)
        save_sheet(mdir / "faces.jpg", np.concatenate([np.pad(r, ((0, 0), (0, W - r.shape[1]), (0, 0)))
                                                       for r in rows], 0), labels)


# ------------------------------------------------------------------- solve

def cache_key(items, args, setup: dict, edl: dict | None) -> str:
    parts = [[m["name"], Path(m["file"]).stat().st_size if Path(m["file"]).exists() else 0,
              Path(m["file"]).stat().st_mtime if Path(m["file"]).exists() else 0] for m in items]
    parts += [getattr(args, "input_lut", None), getattr(args, "force_log", False), getattr(args, "match_target", None),
              getattr(args, "zones", "auto"), setup, FRAMES, KNOTS.tolist(), [used_ranges(m, edl) for m in items]]
    return hashlib.sha1(json.dumps(parts, default=str).encode()).hexdigest()[:16]


def solve(items: list[dict], args) -> dict:
    """Returns {file name: {"status", "group", "params", ...}}; writes match.json, match.md and sheets."""
    mdir = Path(getattr(args, "match_dir", None) or "02-work/color/match")
    mdir.mkdir(parents=True, exist_ok=True)
    setup = load_setup(mdir)
    ep = Path(getattr(args, "edl", None) or "02-work/edl.json")
    edl = json.loads(ep.read_text(encoding="utf-8")) if ep.exists() else None
    key = cache_key(items, args, setup, edl)
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
        d = data[m["name"]] = {"times": times, "frames": [load(m, t, lut_for(m)) for t in times], "regions": {},
                               "rep": len(times) // 2}
        g = group_of(setup, m["name"])
        skin_on = g is None or setup["groups"][g].get("skin", True)
        for i, (t, img) in enumerate(zip(times, d["frames"])):
            boxes = faces_at(faces, m["name"], t)
            reg = skin_region(to_lab(img), max(boxes, key=lambda b: b[3])) if boxes and skin_on else None
            if reg is not None:
                d["regions"].setdefault("skin", []).append((i, reg))
        for r, where in setup["added"].items():                # pointed at by looking
            spot = where.get(m["name"])
            if not spot:
                continue
            for dt in (-0.4, 0.0, 0.4):
                img = load(m, spot["t"] + dt, lut_for(m))
                reg = snap(to_lab(img), spot["x"], spot["y"], spot.get("tolerance", 6.0))
                if reg is not None:
                    d["frames"].append(img)
                    d["regions"].setdefault(r, []).append((len(d["frames"]) - 1, reg))
        for drop in setup["drop"]:
            if drop.get("file") in (None, m["name"]):
                d["regions"].pop(drop["name"], None)
    before = {m["name"]: measure(data[m["name"]]["frames"], data[m["name"]]["regions"]) for m in items}

    results, T_all = {}, {}
    names = {m["name"] for m in items}
    for gname, g in setup["groups"].items():
        members = [m for m in items if m["name"] in g["files"] and m["name"] not in setup["skip"]]
        if len(members) < 2:
            continue
        refs = sorted({r for m in members for r in before[m["name"]]})
        target = getattr(args, "match_target", None)
        T = targets({m["name"]: before[m["name"]] for m in members}, refs, target if target in g["files"] else None)
        T_all[gname] = T
        prio = {r: PRIORITY["skin"] if r == "skin" else setup["added"].get(r, {}).get("_priority")
                or PRIORITY[T[r]["zone"]] for r in T}
        cams = {json.dumps([m.get("camera"), m.get("log_hint"), (m.get("color") or {}).get("transfer")])
                for m in members}
        zones_on = getattr(args, "zones", "auto") == "on" or (getattr(args, "zones", "auto") == "auto"
                                                               and len(cams) > 1)
        for m in members:
            d = data[m["name"]]
            res = fit_shot(d["frames"], d["regions"], {r: v for r, v in before[m["name"]].items() if r in T},
                           T, prio, zones_on)
            results[m["name"]] = {**res, "group": gname}
    for m in items:
        name, d = m["name"], data[m["name"]]
        if name in setup["skip"]:
            results[name] = {"status": "skipped", "group": None, "params": None, "used": [], "dropped": [],
                             "notes": ["not camera footage: left untouched"], "warnings": []}
            continue
        res = results.get(name) or {"status": "alone", "group": group_of(setup, name), "params": None, "used": [],
                                    "dropped": [], "notes": [], "warnings": []}
        nudge = setup["nudge"].get(name)
        if nudge:
            res["params"] = nudged(res["params"], nudge)
            res["notes"].append("nudged by hand: " + ", ".join(f"{k} {v:+g}" for k, v in nudge.items()))
        skin = before[name].get("skin")
        if skin and skin.get("split"):
            res["warnings"].append("the light changes halfway through the shot: one correction can't suit both "
                                   "halves — say so in the delivery")
        elif skin and skin["spread"] > UNSTABLE:
            res["notes"].append(f"the light changes within the shot (spread {skin['spread']}): corrected for the "
                                "most common light")
        problems = check(d["frames"], d["regions"], before[name], res["params"])
        if any("invert" in p for p in problems):
            res["params"] = None
        res["warnings"] += problems
        if res["status"] == "ok" and res["warnings"]:
            res["status"] = "partial"
        res["after"] = measure(d["frames"], d["regions"], transform(res["params"]))
        res["sheet_t"] = d["times"][d["rep"]]          # the frame on the sheet: for pointing with `add`
        results[name] = res
    for gname, g in setup["groups"].items():
        missing = [f for f in g["files"] if f not in names]
        if missing:
            print(f"WARNING: group {gname} names files that aren't here: {', '.join(missing)}")

    info = {m["name"]: hint(data[m["name"]]["frames"][:n]) for m in items}
    report(mdir, items, before, results, T_all, info, setup)
    try:
        sheets = shot_sheets(mdir, items, data, results, info)
        faces_sheet(mdir, items, data, results, faces, lut_for)
        print("look at: " + ", ".join(str(p) for p in sheets) + (f", {mdir / 'faces.jpg'}"
                                                                   if (mdir / "faces.jpg").exists() else ""))
    except Exception as e:  # the sheet is a check, never a reason to stop the grade
        print(f"WARNING: sheet failed: {e}")
    files = {n_: {k: v for k, v in r.items() if k != "after"} for n_, r in results.items()}
    mj.write_text(json.dumps({"key": key, "targets": T_all, "files": files,
                              "measured": {"before": before, "after": {n_: r.get("after") for n_, r in results.items()},
                                           "hint": info}}, indent=1), encoding="utf-8")
    return files


def report(mdir: Path, items, before, results, T_all, info, setup) -> None:
    lines = ["# Shot matching", ""]
    if not setup["groups"]:
        lines += ["No groups yet: look at sheet.jpg and group the shots that share place and light",
                  "(`match.py group --name <scene> <files...>`). Shots in no group keep their own look.", ""]
    for gname, T in T_all.items():
        files = [m["name"] for m in items if results[m["name"]].get("group") == gname]
        lines += [f"## Group {gname}", "", "| reference | target L* | " + " | ".join(files) + " | spread before → after |",
                  "|---|---|" + "---|" * len(files) + "---|"]
        for r, t in T.items():
            cells, b_vals, a_vals = [], [], []
            for f in files:
                b = before[f].get(r)
                a = (results[f].get("after") or {}).get(r)
                if b is None:
                    cells.append("—")
                    continue
                b_vals.append(b["L"])
                if a and r in results[f]["used"]:
                    a_vals.append(a["L"])
                    cells.append(f"{b['L']:.1f} → {a['L']:.1f} (tex {a['tex'] / max(b['tex'], 1e-6):.0%})")
                else:
                    cells.append(f"{b['L']:.1f} (not used)")
            spread = lambda v: f"{max(v) - min(v):.1f}" if len(v) > 1 else "—"  # noqa: E731
            lines.append(f"| {r} | {t['L']:.1f} | " + " | ".join(cells) + f" | {spread(b_vals)} → {spread(a_vals)} |")
        lines.append("")
    lines += ["## Shots", ""]
    for m in items:
        res, h = results[m["name"]], info[m["name"]]
        lines.append(f"- **{m['name']}** (sheet frame @ {res.get('sheet_t', 0):.2f}s) — "
                     f"{res.get('group') or 'no group'} · {res['status']} · "
                     f"{m.get('camera') or 'camera ?'} · {m.get('recorded') or ''} · whole frame (hint only): "
                     f"L {h['L2']}/{h['L50']}/{h['L98']}, temp {h['temp']:+.1f}, tint {h['tint']:+.1f}")
        for x in res["dropped"]:
            lines.append(f"  - dropped {x['ref']}: {x['why']}")
        for x in res["notes"]:
            lines.append(f"  - {x}")
        for x in res["warnings"]:
            lines.append(f"  - WARNING: {x}")
    lines += ["", "tex = share of a reference's texture kept. temp: + warm / − cool; tint: + magenta / − green.",
              "Look at sheet.jpg and faces.jpg before grade.py build."]
    (mdir / "match.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


# --------------------------------------------------------------------- CLI

def names_of(files: list[str], ingest: str) -> list[str]:
    return [m["name"] for m in resolve(files, load_material(Path(ingest)))]


def cmd_group(args) -> None:
    mdir = Path(args.match_dir)
    s = load_setup(mdir)
    if args.clear:
        s["groups"].pop(args.name, None)
    else:
        files = names_of(args.files, args.ingest)
        for g in s["groups"].values():
            g["files"] = [f for f in g["files"] if f not in files]
        s["groups"][args.name] = {"files": files, "skin": not args.no_skin}
    s["groups"] = {k: v for k, v in s["groups"].items() if v["files"]}
    save_setup(mdir, s)
    print(json.dumps(s["groups"], indent=1))


def cmd_skip(args) -> None:
    mdir = Path(args.match_dir)
    s = load_setup(mdir)
    files = names_of(args.files, args.ingest)
    s["skip"] = [f for f in s["skip"] if f not in files] + ([] if args.restore else files)
    save_setup(mdir, s)
    print("skipped: " + (", ".join(s["skip"]) or "none"))


def cmd_add(args) -> None:
    mdir = Path(args.match_dir)
    s = load_setup(mdir)
    x, y = (float(v) for v in args.at.split(","))
    m = resolve([args.file], load_material(Path(args.ingest)))[0]
    lut = read_cube(Path(args.input_lut)) if args.input_lut and is_log(m, args) else None
    reg = snap(to_lab(load(m, args.t, lut)), x, y, args.tolerance)
    if reg is None:
        die(f"nothing to measure at {args.at} in {m['name']} @ {args.t}s: pick a flatter spot, or raise --tolerance")
    entry = s["added"].setdefault(args.name, {})
    entry[m["name"]] = {"t": args.t, "x": x, "y": y, "tolerance": args.tolerance}
    if args.priority:
        entry["_priority"] = args.priority
    save_setup(mdir, s)
    print(f"{args.name} in {m['name']}: {int(reg.sum())} px around ({x:.2f}, {y:.2f}) @ {args.t}s")


def cmd_drop(args) -> None:
    mdir = Path(args.match_dir)
    s = load_setup(mdir)
    d = {"name": args.name, "file": args.file}
    s["drop"] = [x for x in s["drop"] if x != d] + ([] if args.restore else [d])
    save_setup(mdir, s)
    print(f"{'restored' if args.restore else 'dropped'} {args.name}" + (f" in {args.file}" if args.file else ""))


def cmd_set(args) -> None:
    mdir = Path(args.match_dir)
    s = load_setup(mdir)
    name = names_of([args.file], args.ingest)[0]
    nudge = {} if args.clear else dict(s["nudge"].get(name, {}))
    for kv in args.values:
        k, _, v = kv.partition("=")
        if k not in NUDGES:
            die(f"unknown control '{k}' (choose from {', '.join(NUDGES)})")
        lo, hi = NUDGES[k]
        val = float(v)
        if not lo <= val <= hi:
            print(f"{k} {val:+g} is outside the gentle range ({lo:+g} … {hi:+g}): capped")
        nudge[k] = float(np.clip(val, lo, hi))
    s["nudge"] = {**s["nudge"], name: {k: v for k, v in nudge.items() if v}}
    s["nudge"] = {k: v for k, v in s["nudge"].items() if v}
    save_setup(mdir, s)
    print(f"{name}: {s['nudge'].get(name) or 'no nudges'}")


def cmd_solve(args) -> None:
    items = resolve(args.files, load_material(Path(args.ingest)))
    if not items:
        die("no files")
    solve(items, args)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("solve", "group", "skip", "add", "drop", "set"):
        s = sub.add_parser(name)
        s.add_argument("--ingest", default="01-ingest")
        s.add_argument("--match-dir", default="02-work/color/match")
        s.add_argument("--input-lut")
        s.add_argument("--force-log", action="store_true")
        if name == "solve":
            s.add_argument("files", nargs="*")
            s.add_argument("--target", dest="match_target", help="match a group to this shot instead of its base")
            s.add_argument("--zones", choices=("auto", "on", "off"), default="auto",
                           help="color per tonal zone: auto = only when a group mixes cameras")
            s.add_argument("--edl", default="02-work/edl.json", help="measure only what the edit uses (if it exists)")
        if name in ("group", "skip"):
            s.add_argument("files", nargs="*")
            s.add_argument("--clear" if name == "group" else "--restore", action="store_true")
        if name == "group":
            s.add_argument("--name", required=True)
            s.add_argument("--no-skin", action="store_true", help="different people: skin isn't shared")
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
        if name == "set":
            s.add_argument("--file", required=True)
            s.add_argument("values", nargs="*", help="exposure=+0.3 black=-2 white=+3 temp=-4 tint=+2 contrast=+0.1")
            s.add_argument("--clear", action="store_true")
    args = ap.parse_args()
    {"solve": cmd_solve, "group": cmd_group, "skip": cmd_skip, "add": cmd_add, "drop": cmd_drop,
     "set": cmd_set}[args.cmd](args)


if __name__ == "__main__":
    main()
