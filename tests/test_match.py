# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26", "scipy>=1.11"]
# ///
"""Shot matching tests (match.py + grade.py) on synthetic shots.

    uv run tests/test_match.py

The same three objects (dark, mid, light — each with texture) filmed in three shots
that differ the way real ones do: a different background each time (gray wall,
white wall, dark room — the trap for whole-frame statistics), and different
rendering (A: normal · B: darker with crushed blacks · C: lifted blacks and a cool
cast, like another camera). Done the way the AI does it after looking at the sheet:
the shots are grouped (match.py group) and the objects pointed at (match.py add).

1. Before grouping nothing is matched: every shot keeps its own look.
2. After matching, every reference lands within a few L* across the group (it started far apart).
3. The darks stay dark: the target is the deepest shot that isn't crushed, not a gray average.
4. Detail is kept: each reference keeps most of its texture.
5. The order of brightness never inverts (gray axis of every .cube).
6. A reference lit differently in one shot (a "decoy" next to the mid one) is dropped there.
7. A grouped shot with nothing in common with the rest is flagged "not matched", not forced.
8. Level none still matches; --no-match leaves it untouched.
9. skip leaves a shot untouched (no correction, no look); set nudges a shot, capped to a gentle range.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from scipy import ndimage

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
FF = shutil.which("ffmpeg")
W, H, N = 640, 360, 50
X264 = ["-c:v", "libx264", "-preset", "ultrafast", "-crf", "10", "-pix_fmt", "yuv444p"]
rng = np.random.default_rng(7)


def texture(h, w, amount, sigma=1.6):
    t = ndimage.gaussian_filter(rng.standard_normal((h, w)), sigma)
    return 1 + amount * t / t.std()


# Scene-linear objects: (name, linear rgb, texture, position per shot)
OBJECTS = [("dark", (0.014, 0.012, 0.011), 0.45), ("mid", (0.26, 0.17, 0.12), 0.18),
           ("light", (0.62, 0.62, 0.6), 0.08), ("decoy", (0.11, 0.13, 0.16), 0.15)]
POS = {"A": [(60, 40), (230, 40), (400, 40), (230, 200)],
       "B": [(90, 60), (260, 60), (430, 60), (260, 220)],
       "C": [(40, 30), (210, 30), (380, 30), (210, 190)]}
SIZE = 110
BACKGROUND = {"A": 0.18, "B": 1.0, "C": 0.002}
TEX = {name: texture(SIZE, SIZE, amt) for name, _, amt in OBJECTS}


def scene(shot: str) -> np.ndarray:
    img = np.full((H, W, 3), BACKGROUND[shot], np.float32)
    for (name, rgb, _), (x, y) in zip(OBJECTS, POS[shot]):
        lit = 2.4 if (shot == "B" and name == "decoy") else 1.0      # the decoy gets other light in B
        img[y:y + SIZE, x:x + SIZE] = np.array(rgb) * lit * TEX[name][..., None]
    return img


def render(shot: str, lin: np.ndarray) -> np.ndarray:
    """How each 'camera' turns the scene into a picture."""
    disp = lambda v: np.clip(v, 0, None) ** (1 / 2.4)  # noqa: E731
    if shot == "A":
        out = disp(lin)
    elif shot == "B":
        out = (disp(lin * 0.55) - 0.07) / 0.93                       # darker, crushed blacks
    elif shot == "C":
        out = 0.09 + 0.86 * disp(lin * 1.15 * np.array([0.9, 1.0, 1.12]))   # lifted blacks, cool
    else:
        out = disp(lin)
    return np.clip(out, 0, 1)


def encode(path: Path, shot: str) -> None:
    base = scene(shot) if shot in POS else unrelated()
    frames = []
    for i in range(N):
        noise = 1 + 0.01 * rng.standard_normal((H, W, 1))
        frames.append((render(shot, base * noise) * 255).round().astype(np.uint8))
    subprocess.run([FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", "25",
                    "-i", "-", *X264, str(path)], input=b"".join(f.tobytes() for f in frames), check=True)


def unrelated() -> np.ndarray:
    yy, xx = np.mgrid[0:H, 0:W]
    return np.stack([0.05 + 0.4 * xx / W, 0.3 * yy / H + 0.02, np.full((H, W), 0.2)], -1).astype(np.float32)


def run(cwd: Path, script: str, *args: str) -> str:
    p = subprocess.run(["uv", "run", str(SCRIPTS / script), *args], cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode != 0:
        print(p.stdout[-3000:], p.stderr[-3000:])
        raise SystemExit(f"{script} {' '.join(args)} failed")
    return p.stdout


def gray_axis(cube: Path) -> np.ndarray:
    rows = [list(map(float, ln.split())) for ln in cube.read_text().splitlines()
            if ln and ln[0] in "0123456789." and len(ln.split()) == 3]
    n = round(len(rows) ** (1 / 3))
    t = np.array(rows).reshape(n, n, n, 3)
    return np.array([t[i, i, i] @ [0.2126, 0.7152, 0.0722] for i in range(n)])


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-match-"))
    for s in ("A", "B", "C", "D"):
        encode(tmp / f"{s}.mp4", s)
    for s in ("A", "B", "C"):
        for (name, _, _), (x, y) in zip(OBJECTS, POS[s]):
            run(tmp, "match.py", "add", "--name", name, "--file", f"{s}.mp4", "--t", "1.0",
                "--at", f"{(x + SIZE / 2) / W:.3f},{(y + SIZE / 2) / H:.3f}")

    files = ["A.mp4", "B.mp4", "C.mp4", "D.mp4"]
    mdir = tmp / "02-work" / "color" / "match"
    checks = {}
    run(tmp, "match.py", "solve", *files)
    first = json.loads((mdir / "match.json").read_text(encoding="utf-8"))["files"]
    checks["no groups yet: every shot keeps its own look"] = all(
        r["status"] == "alone" and r["params"] is None for r in first.values())
    checks["sheet.jpg drawn before grouping (to decide the groups)"] = (mdir / "sheet.jpg").exists()

    run(tmp, "match.py", "group", "--name", "room", *files)
    run(tmp, "grade.py", "build", *files, "--level", "none")
    mj = json.loads((mdir / "match.json").read_text(encoding="utf-8"))
    before, after, T = mj["measured"]["before"], mj["measured"]["after"], mj["targets"]["room"]
    res = mj["files"]
    abc = ["A.mp4", "B.mp4", "C.mp4"]
    for r in ("dark", "mid", "light"):
        shots = [f for f in abc if r in res[f]["used"]]
        b = [before[f][r]["L"] for f in shots]
        a = [after[f][r]["L"] for f in shots]
        checks[f"{r}: spread {max(b) - min(b):.1f} → {max(a) - min(a):.1f} L* over {len(shots)} shots "
               "(started > 6, ends < 3)"] = len(shots) >= 2 and max(b) - min(b) > 6 and max(a) - min(a) < 3
    checks["references measured on the objects, not the backgrounds (warm mid in every shot)"] = all(
        before[f]["mid"]["b"] > 4 for f in abc)
    checks["mid and light matched in all three shots"] = all(r in res[f]["used"] for f in abc for r in ("mid", "light"))
    checks["B's crushed darks flagged as lost at the source, not forced"] = (
        "dark" not in res["B.mp4"]["used"] and any("dark: detail lost" in n for n in res["B.mp4"]["notes"]))
    deep_ok = min(before[f]["dark"]["L"] for f in ("A.mp4", "C.mp4"))
    checks[f"darks stay dark (target {T['dark']['L']} ≈ the deepest uncrushed shot {deep_ok:.1f}, "
           f"below the average {np.mean([before[f]['dark']['L'] for f in abc]):.1f})"] = (
        abs(T["dark"]["L"] - max(deep_ok, 8)) < 1.5 and T["dark"]["L"] < np.mean([before[f]["dark"]["L"] for f in abc]))
    keep = min(after[f][r]["tex"] / before[f][r]["tex"] for f in abc for r in ("mid", "light"))
    checks[f"texture kept in mids and lights (worst {keep:.0%} ≥ 75%)"] = keep >= 0.75
    dropped_b = [x["ref"] for x in res["B.mp4"]["dropped"]]
    checks[f"decoy lit differently in B is dropped there (dropped: {dropped_b})"] = "decoy" in dropped_b
    checks["decoy still used in A and C"] = all("decoy" in res[f]["used"] for f in ("A.mp4", "C.mp4"))
    checks[f"grouped shot with nothing shared flagged, not forced ({res['D.mp4']['status']})"] = (
        res["D.mp4"]["status"] == "not matched" and res["D.mp4"]["params"] is None)
    ramps = {f: gray_axis(tmp / "02-work" / "color" / f"{Path(f).stem}.cube") for f in abc}
    checks["brightness order never inverts (gray axis of every .cube)"] = all(
        np.all(np.diff(v) >= -1e-4) for v in ramps.values())
    checks["level none still matches (cube isn't identity)"] = abs(ramps["B.mp4"][8] - 8 / 32) > 0.02
    checks["sheet.jpg and match.md written"] = all((mdir / n).exists() for n in ("sheet.jpg", "match.md"))
    grade = json.loads((tmp / "02-work" / "color" / "grade.json").read_text(encoding="utf-8"))
    checks["grade.json records the match"] = (grade["B.mp4"]["params"]["match_status"] in ("ok", "partial")
                                              and grade["B.mp4"]["params"]["match"]["curve"])

    run(tmp, "grade.py", "build", "A.mp4", "B.mp4", "--level", "none", "--no-match", "--out", "nomatch")
    ident = gray_axis(tmp / "nomatch" / "B.cube")
    checks["--no-match at level none: untouched"] = np.allclose(ident[:30], np.linspace(0, 1, len(ident))[:30],
                                                                atol=2e-3)

    # skip: not camera footage → untouched, even with a look. set: a nudge, capped.
    run(tmp, "match.py", "skip", "D.mp4")
    out = run(tmp, "match.py", "set", "--file", "A.mp4", "exposure=+5")
    run(tmp, "grade.py", "build", *files, "--level", "basic", "--look", "cinematic", "--out", "looked")
    g2 = json.loads((tmp / "looked" / "grade.json").read_text(encoding="utf-8"))
    d_ramp = gray_axis(tmp / "looked" / "D.cube")
    checks["skip: untouched — no correction, no look"] = (
        g2["D.mp4"]["params"]["look"] is None and np.allclose(d_ramp[:30], np.linspace(0, 1, len(d_ramp))[:30],
                                                              atol=2e-3))
    a_after = json.loads((mdir / "match.json").read_text(encoding="utf-8"))["measured"]["after"]["A.mp4"]
    checks[f"set: exposure nudge capped at +2 and brightens A (mid {after['A.mp4']['mid']['L']} → "
           f"{a_after['mid']['L']})"] = "capped" in out and a_after["mid"]["L"] > after["A.mp4"]["mid"]["L"] + 5
    checks["set: still no inverted brightness"] = np.all(np.diff(gray_axis(tmp / "looked" / "A.cube")) >= -1e-4)

    checks = {k: bool(v) for k, v in checks.items()}
    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print((tmp / "02-work" / "color" / "match" / "match.md").read_text(encoding="utf-8"))
    print(f"{sum(checks.values())}/{len(checks)} pass · {tmp}")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
