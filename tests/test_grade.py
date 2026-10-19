# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Color tests for grade.py on synthetic clips.

    uv run tests/test_grade.py

1. A neutral scene with a strong warm cast: analyze must flag it, build must
   remove it (measured, not by eye).
2. The same scene without a cast: auto mode must leave it alone.
3. Simulated log: the warm scene encoded with a generic log curve, graded with
   that curve's inverse as the "official LUT": the cast is corrected in log and
   the result lands close to the original exposure.
4. The user's creative LUT is applied after the correction.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
GRADE = Path(__file__).resolve().parents[1] / "plugins" / "vibetake" / "skills" / "edit" / "scripts" / "grade.py"
FF = shutil.which("ffmpeg")
# Neutral gray texture with a few small colored patches (a scene with real neutrals).
SCENE = ("nullsrc=s=640x360:d=4:r=25,geq=lum='110+70*sin(X/29)*cos(Y/21)+20*sin(T)':cb=128:cr=128,"
         "drawbox=x=40:y=40:w=90:h=90:color=red@1:t=fill,drawbox=x=500:y=230:w=90:h=90:color=blue@1:t=fill,"
         "drawbox=x=280:y=260:w=70:h=70:color=green@1:t=fill")
WARM = "colorbalance=rs=0.12:rm=0.12:rh=0.08:bs=-0.12:bm=-0.12:bh=-0.08"
X264 = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p"]


def to_log(lin):            # generic log encoding: ~0.08 per stop, lifted black, no real white
    return 0.1 + 0.75 * np.log2(1 + 31 * lin) / 5


def from_log(code):
    return np.clip((2 ** ((np.clip(code, 0.1, 1) - 0.1) * 5 / 0.75) - 1) / 31, 0, 1)


def write_cube(path: Path, fn, n: int = 33) -> None:
    v = np.linspace(0, 1, n)
    b, g, r = np.meshgrid(v, v, v, indexing="ij")
    out = np.clip(fn(np.stack([r, g, b], -1).reshape(-1, 3)), 0, 1)
    path.write_text(f"LUT_3D_SIZE {n}\n" + "\n".join(f"{x:.6f} {y:.6f} {z:.6f}" for x, y, z in out) + "\n")


def ff(cwd: Path, *args: str) -> None:
    subprocess.run([FF, "-hide_banner", "-v", "error", "-y", *args], check=True, cwd=cwd)


def grade(cwd: Path, *args: str) -> None:
    subprocess.run(["uv", "run", str(GRADE), *args], cwd=cwd, check=True, capture_output=True)


def read(cwd: Path, name: str) -> dict:
    return json.loads((cwd / "02-work" / "color" / name).read_text(encoding="utf-8"))


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-grade-"))
    ff(tmp, "-f", "lavfi", "-i", SCENE, *X264, "clean.mp4")
    ff(tmp, "-i", "clean.mp4", "-vf", WARM, *X264, "warm.mp4")
    write_cube(tmp / "to_log.cube", lambda d: to_log(d ** 2.4))
    write_cube(tmp / "from_log.cube", lambda c: from_log(c) ** (1 / 2.4))
    ff(tmp, "-i", "warm.mp4", "-vf", "format=rgb48le,lut3d=file=to_log.cube", "-c:v", "libx265",
       "-preset", "ultrafast", "-pix_fmt", "yuv420p10le", "-x265-params", "log-level=error", "log.mov")

    checks = {}
    grade(tmp, "analyze", "warm.mp4", "clean.mp4")
    a = read(tmp, "color.json")
    checks["warm cast detected (chroma ≥ 6)"] = a["warm.mp4"]["neutral"]["cast_chroma"] >= 6
    checks["clean scene: no clear problem"] = not a["clean.mp4"]["verdict"]

    grade(tmp, "build", "warm.mp4", "clean.mp4", "--no-match")   # per-file correction (matching: test_match.py)
    plan = read(tmp, "grade.json")
    warm = plan["warm.mp4"]["check"]
    t_before, t_after = warm["temp_tint_before"], warm["temp_tint_after"]
    checks[f"balanced (default): warmth kept, tint removed (temp {t_before[0]} → {t_after[0]}, "
           f"tint {t_before[1]} → {t_after[1]})"] = t_after[0] > 0.6 * min(t_before[0], 12) and abs(t_after[1]) < 1.5
    grade(tmp, "build", "warm.mp4", "--no-match", "--ambient", "neutral", "--out", "neutral")
    neutral = json.loads((tmp / "neutral" / "grade.json").read_text())["warm.mp4"]["check"]
    checks[f"neutral: the whole cast removed (chroma {neutral['cast_after']} < 2)"] = neutral["cast_after"] < 2
    checks["clean scene left alone (unity gains)"] = plan["clean.mp4"]["params"]["gains"] == [1.0, 1.0, 1.0]

    grade(tmp, "build", "log.mov", "--input-lut", "from_log.cube", "--force-log", "--ambient", "neutral")
    log = read(tmp, "grade.json")["log.mov"]["check"]
    clean_luma = a["clean.mp4"]["median_luma"]
    checks["log: cast corrected before the LUT (< 2.5)"] = log["cast_after"] < 2.5
    checks[f"log: exposure near the original (±0.05 of {clean_luma})"] = abs(log["median_luma_after"]
                                                                             - clean_luma) < 0.05

    # Creative: the user's LUT (here, one that warms) is applied after the correction.
    write_cube(tmp / "user.cube", lambda d: np.clip(d * np.array([1.08, 1.0, 0.86]), 0, 1))
    grade(tmp, "build", "clean.mp4", "--creative-lut", "user.cube", "--out", "creative")
    ff(tmp, "-i", "clean.mp4", "-vf", "format=rgb48le,lut3d=file=creative/clean.cube", *X264, "looked.mp4")
    grade(tmp, "analyze", "looked.mp4", "--out", "creative")
    looked_b = json.loads((tmp / "creative" / "color.json").read_text())["looked.mp4"]["neutral"]["b"]
    clean_b = a["clean.mp4"]["neutral"]["b"]
    checks[f"the user's creative LUT is applied (b {clean_b} → {looked_b})"] = looked_b > clean_b + 3

    # Levels: "none" touches nothing; propose writes a recommendation for the color question.
    grade(tmp, "build", "warm.mp4", "--level", "none", "--out", "none")
    p = json.loads((tmp / "none" / "grade.json").read_text())["warm.mp4"]["params"]
    checks["level none: untouched"] = p["gains"] == [1.0, 1.0, 1.0] and not p["finish"] and p["stops"] == 0
    grade(tmp, "propose", "warm.mp4", "--out", "prop")
    prop = (tmp / "prop" / "propose.md").read_text(encoding="utf-8")
    checks["propose: previews for basic and full + recommendation"] = (
        "Recommended:" in prop and list((tmp / "prop" / "basic").glob("preview_*.jpg"))
        and list((tmp / "prop" / "full").glob("preview_*.jpg")))

    # Repairs (only when asked): denoise and moiré go into the pre-filter; the moiré one
    # softens false color (chroma) without touching detail (luma).
    grade(tmp, "build", "clean.mp4", "--denoise", "light", "--moire", "--out", "repair")
    pre = json.loads((tmp / "repair" / "grade.json").read_text())["clean.mp4"]["pre"]
    checks["repairs go in the pre-filter, before the correction"] = "hqdn3d" in pre and "gblur" in pre
    ff(tmp, "-f", "lavfi", "-i", "nullsrc=s=320x240:d=0.2,geq=lum='128+60*sin(X/2)':cb='128+50*sin(X/1.3+Y/1.7)':"
       "cr='128+50*cos(X/1.1-Y/1.9)'", "-frames:v", "1", "moire.png")
    planes = []
    sys.path.insert(0, str(GRADE.parent))
    from grade import MOIRE
    for vf in ("format=yuv444p", f"{MOIRE},format=yuv444p"):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(tmp / "moire.png"), "-vf", vf, "-f", "rawvideo",
                              "-"], capture_output=True, check=True).stdout
        planes.append(np.frombuffer(raw, np.uint8).reshape(3, 240, 320).astype(float))
    luma_kept = np.abs(planes[0][0] - planes[1][0]).mean() < 1
    chroma_down = planes[1][1].std() < 0.3 * planes[0][1].std()
    checks["moiré repair: false color down, detail kept"] = luma_kept and chroma_down

    # Global skin shift: skin-like colors move toward the skin line; neutrals and blue don't.
    sys.path.insert(0, str(GRADE.parent))
    import grade as g
    colors = np.array([[0.80, 0.55, 0.50],   # reddish skin
                       [0.50, 0.50, 0.50],   # gray
                       [0.20, 0.30, 0.80]], np.float32)  # blue
    hue = lambda c: float(np.degrees(np.arctan2((c[0] - g.luma(c)) / 1.5748, (c[2] - g.luma(c)) / 1.8556)))  # noqa: E731
    skin_before = hue(colors[0])
    out = g.skin_shift(colors, skin_before, 8.0)
    checks["skin shift moves skin toward the line"] = abs(hue(out[0]) - 123) < abs(skin_before - 123)
    checks["skin shift leaves gray and blue"] = np.allclose(out[1:], colors[1:], atol=1e-3)

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print("warm:", warm, "\nlog:", log)
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
