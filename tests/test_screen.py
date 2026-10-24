# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Screen zooms (screen.py + render.py): where the action is, never the cursor or a page change.

    uv run tests/test_screen.py

A synthetic screen recording: a cursor always moving, a menu that opens at 2 s (open until 4 s),
a field filled from 6 to 7.5 s, the whole page changing at 9 s. Checks the proposals, the sheet,
keep/set/add/drop, and the render: the menu bigger while zoomed, the picture untouched outside,
the zoom easing in, a vertical output following the action.
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
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
FF = shutil.which("ffmpeg")
W, H, FPS = 1280, 720, 30


def uv(cwd: Path, *a: str) -> subprocess.CompletedProcess:
    return subprocess.run(["uv", "run", str(SCRIPTS / a[0]), *a[1:]], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def frame(path: Path, t: float, w: int, h: int) -> np.ndarray:
    raw = subprocess.run([FF, "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1", "-f", "rawvideo",
                          "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(int)


def menu_share(img: np.ndarray) -> float:
    """Share of the picture in the menu's color (pure blue)."""
    return float(((img[..., 2] > 180) & (img[..., 0] < 60) & (img[..., 1] < 60)).mean())


def main() -> None:
    checks = {}
    proj = Path(tempfile.mkdtemp(prefix="vt-screen-"))
    for d in ("00-raw", "01-ingest", "02-work"):
        (proj / d).mkdir()
    ui = ",".join(f"drawbox=x={x}:y={y}:w=180:h=14:c=0x909090:t=fill" for x in (100, 420, 740) for y in (200, 260, 320, 380))
    vf = (f"{ui},drawbox=x=40:y=40:w=260:h=230:c=0x0000ff:t=fill:enable='between(t,2,4)',"
          "drawbox=x=880:y=560:w='min(1,(t-6)/1.5)*300':h=28:c=0x202020:t=fill:enable='between(t,6,11)',"
          "drawbox=x=0:y=0:w=1280:h=720:c=0xe0f0e0:t=fill:enable='gte(t,9)'")
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=0xf4f4f4:s={W}x{H}:r={FPS}:d=11",
                    "-f", "lavfi", "-i", f"color=black:s=12x18:r={FPS}:d=11", "-filter_complex",
                    f"[0]{vf}[bg];[bg][1]overlay=x='600+300*sin(t*1.3)':y='360+200*cos(t*0.9)',format=yuv420p",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10", str(proj / "00-raw" / "rec.mp4")], check=True)
    (proj / "01-ingest" / "material.json").write_text(json.dumps({"files": [
        {"name": "rec.mp4", "file": str(proj / "00-raw" / "rec.mp4"), "kind": "video", "duration": 11.0, "fps": FPS,
         "display_width": W, "display_height": H, "faces": {"pct_with_face": 0}}]}))
    edl = {"version": 1, "output": {"width": W, "height": H, "fps": FPS},
           "segments": [{"source": str(proj / "00-raw" / "rec.mp4"), "name": "rec.mp4", "in": 0.0, "out": 11.0}]}
    (proj / "02-work" / "edl.json").write_text(json.dumps(edl))

    r = uv(proj, "screen.py", "find")
    if r.returncode:
        print(r.stdout, r.stderr)
    props = json.loads((proj / "02-work/screen/proposals.json").read_text(encoding="utf-8"))["zooms"]
    menu = [z for z in props if z["from"] < 2.2 and z["to"] > 3.0]
    field = [z for z in props if 5.5 < z["from"] < 7.0]
    checks[f"find: the menu opening ({[(z['from'], z['to'], z['box']) for z in menu]})"] = (
        len(menu) == 1 and abs(menu[0]["box"][0] - 40 / W) < 0.03 and abs(menu[0]["box"][1] - 40 / H) < 0.04
        and menu[0]["box"][2] < 0.35)
    checks[f"find: the field being filled ({[(z['from'], z['to']) for z in field]})"] = (
        len(field) == 1 and field[0]["box"][0] > 0.6 and field[0]["box"][1] > 0.7)
    checks[f"find: not the cursor, not the page change ({len(props)} proposals)"] = (
        len(props) == 2 and not any(8.8 < z["from"] < 10 for z in props))
    checks["find: sheet and list"] = (proj / "02-work/screen/sheet_01.jpg").exists() and "Z1" in (
        proj / "02-work/screen/proposals.md").read_text(encoding="utf-8")

    mid = menu[0]["id"]
    uv(proj, "screen.py", "keep", f"{mid},{field[0]['id']}")
    uv(proj, "screen.py", "drop", field[0]["id"])
    r = uv(proj, "screen.py", "add", "--file", "rec.mp4", "--from", "6.0", "--to", "8.5", "--box", "0.68,0.77,0.25,0.06")
    uv(proj, "screen.py", "set", mid, "--max", "2")
    zs = json.loads((proj / "02-work/edl.json").read_text(encoding="utf-8"))["screen_zooms"]
    checks[f"keep/drop/add/set: {[(z['id'], z['from'], z.get('max')) for z in zs]}"] = (
        len(zs) == 2 and next(z for z in zs if z["id"] == mid)["max"] == 2 and any(z["from"] == 6.0 for z in zs))

    r = uv(proj, "render.py", "--out", "z.mp4", "--no-grade")
    ok = r.returncode == 0
    if not ok:
        print(r.stdout[-2000:], r.stderr[-2000:])
    src = proj / "00-raw" / "rec.mp4"
    out = proj / "z.mp4"
    if ok:
        a, b = menu_share(frame(src, 3.0, W, H)), menu_share(frame(out, 3.0, W, H))
        checks[f"render: the menu bigger while zoomed ({100 * a:.1f}% → {100 * b:.1f}% of the picture)"] = b > 2.5 * a
        before, after = frame(src, 1.0, W, H), frame(out, 1.0, W, H)
        checks["render: untouched before the zoom"] = np.abs(before - after).mean() < 3
        e1, e2 = menu_share(frame(out, 2.1, W, H)), menu_share(frame(out, 2.6, W, H))
        checks[f"render: eases in ({100 * e1:.1f}% at 2.1 s, {100 * e2:.1f}% at 2.6 s)"] = a < e1 < e2
        # From the menu's zoom to the field's (they overlap): a glide, not a jump.
        sys.path.insert(0, str(SCRIPTS))
        import render
        bw, bh = W, H
        xs = [render.screen_zoom(6.0 + i / FPS - 0.2, zs, bw, bh, W, H)[2] for i in range(30)]
        step = max(abs(x - y) for x, y in zip(xs, xs[1:]))
        checks[f"render: one zoom glides into the next (center moves ≤{step:.3f} a frame, from {xs[0]:.2f} to {xs[-1]:.2f})"] = (
            step < 0.12 and xs[-1] - xs[0] > 0.5)
        back = frame(out, 10.5, W, H)
        checks["render: back to the full screen after it"] = np.abs(back - frame(src, 10.5, W, H)).mean() < 3

    # Vertical: the narrow crop goes to the action.
    edl["output"] = {"width": 720, "height": 1280, "fps": FPS}
    edl["screen_zooms"] = zs
    (proj / "02-work" / "edl.json").write_text(json.dumps(edl))
    r = uv(proj, "render.py", "--out", "v.mp4", "--no-grade")
    if r.returncode == 0:
        vb = menu_share(frame(proj / "v.mp4", 3.0, 720, 1280))
        v0 = menu_share(frame(proj / "v.mp4", 1.0, 720, 1280))
        checks[f"vertical: the crop goes to the menu ({100 * vb:.1f}% of the picture is menu)"] = vb > 0.2 and v0 == 0
    else:
        print(r.stdout[-2000:], r.stderr[-2000:])
        checks["vertical: renders"] = False

    for name, ok_ in checks.items():
        print(f"{'PASS' if ok_ else 'FAIL'}  {name}")
    n = sum(bool(v) for v in checks.values())
    print(f"{n}/{len(checks)} pass · {proj}")
    if n == len(checks):
        shutil.rmtree(proj, ignore_errors=True)
    sys.exit(0 if n == len(checks) else 1)


if __name__ == "__main__":
    main()
