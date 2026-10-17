# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Design tests: project fonts and icons, palette, the design reaching subtitles and covers,
and a Remotion graphics layer built from a design and a scene plan. Needs the network
(Google Fonts, jsDelivr) and Node (Remotion); the Remotion part installs a project
(a few minutes the first time).

    uv run tests/test_design.py
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


def run(cwd: Path, *a: str) -> subprocess.CompletedProcess:
    return subprocess.run(["uv", "run", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


def rgba(path: Path, w: int, h: int) -> np.ndarray:
    raw = subprocess.run([FF, "-v", "error", "-i", str(path), "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt",
                          "rgba", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 4)


def main() -> None:
    checks = {}
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-design-"))
    S = lambda n: str(SCRIPTS / n)  # noqa: E731

    # Fonts.
    p = run(tmp, S("fonts.py"), "get", "Bricolage Grotesque", "--weights", "500,800", "--lang", "es")
    idx = json.loads((tmp / "02-work" / "fonts.json").read_text(encoding="utf-8")) if p.returncode == 0 else []
    checks["fonts: downloaded per project, full names recorded"] = len(idx) == 2 and all(
        Path(tmp / e["file"]).exists() for e in idx) and any("ExtraBold" in e["name"] for e in idx)
    p = run(tmp, S("fonts.py"), "get", "Bricolage", "--weights", "400")
    checks["fonts: an unknown family is refused, with suggestions"] = p.returncode != 0 and "Bricolage Grotesque" in p.stderr

    # Icons.
    run(tmp, S("icons.py"), "brand", "whatsapp")
    run(tmp, S("icons.py"), "ui", "check")
    svg = (tmp / "02-work" / "icons" / "brand-whatsapp.svg").read_text(encoding="utf-8")
    checks["icons: official brand mark in its color"] = 'fill="#25D366"' in svg
    run(tmp, S("icons.py"), "png", "02-work/icons/ui-check.svg", "--size", "256", "--color", "#FF0000")
    png = rgba(tmp / "02-work" / "icons" / "ui-check.png", 256, 256)
    ys, xs = np.nonzero(png[:, :, 3] > 100)
    checks["icons: SVG → PNG at the asked size, in the asked color"] = (
        len(xs) and xs.max() - xs.min() > 150 and png[ys[0], xs[0], 0] > 200 and png[ys[0], xs[0], 1] < 60)

    # Palette from the footage, contrast.
    (tmp / "00-raw").mkdir()
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "color=0xC03020:s=320x180:d=6,"
                    "drawbox=x=160:y=0:w=160:h=180:c=0x2050C0:t=fill", "-pix_fmt", "yuv420p", "00-raw/clip.mp4"],
                   cwd=tmp, check=True)
    out = run(tmp, S("design.py"), "palette", "--n", "3").stdout
    hexes = [ln.split()[0] for ln in out.splitlines() if ln.startswith("#")]
    rgbs = [tuple(int(h[i:i + 2], 16) for i in (1, 3, 5)) for h in hexes]
    checks[f"palette: the footage's colors come out ({hexes})"] = (
        any(r > 150 and r > g + 60 and r > b + 60 for r, g, b in rgbs) and any(b > 150 and b > r + 60 for r, g, b in rgbs))
    out = run(tmp, S("design.py"), "contrast", "#FFFFFF", "#000000").stdout
    checks["contrast: WCAG ratio"] = "21.00:1" in out

    # Design reaches subtitles (font + accent) and covers (font + colors).
    design = {"concept": "test", "fonts": {
        "display": {"family": "Bricolage Grotesque", "weight": 800, "file": idx[-1]["file"] if idx else "",
                    "name": next((e["name"] for e in idx if e["weight"] == 800), "")},
        "captions": {"family": "Bricolage Grotesque", "weight": 800, "file": idx[-1]["file"] if idx else "",
                     "name": next((e["name"] for e in idx if e["weight"] == 800), "")}},
        "colors": {"ink": "#FFFFFF", "paper": "#0B0B0C", "accent": "#C6FF3D", "muted": "#A9ABA4"},
        "surface": {"style": "solid", "fill": "paper", "radius": 30, "padding": 0.5, "shadow": "soft"},
        "highlight": {"style": "marker", "color": "accent", "text": "paper"},
        "motion": {"enter": "rise", "stagger": 0.05, "stiffness": 200, "damping": 18, "distance": 0.5, "exitFrames": 6}}
    (tmp / "02-work" / "design.json").write_text(json.dumps(design), encoding="utf-8")
    ing = tmp / "01-ingest"
    ing.mkdir()
    words = [{"word": " " + w, "start": 0.5 + i * 0.4, "end": 0.85 + i * 0.4, "probability": 1.0}
             for i, w in enumerate("Hola esto es una prueba del diseño".split())]
    tr = {"files": {"clip.mp4": {"duration": 6.0, "track": 0, "segments": [
        {"start": 0.5, "end": 3.2, "words": words, "avg_logprob": -0.1, "compression_ratio": 1.2, "no_speech_prob": 0}]}},
        "order": ["clip.mp4"], "lang": "es"}
    (ing / "transcript.json").write_text(json.dumps(tr, ensure_ascii=False), encoding="utf-8")
    (ing / "material.json").write_text(json.dumps({"files": [{"name": "clip.mp4", "file": str(tmp / "00-raw/clip.mp4"),
        "kind": "video", "duration": 6, "display_width": 320, "display_height": 180, "fps": 25}]}))
    run(tmp, S("transcribe.py"), "--rebuild-md", "--lang", "es")
    run(tmp, S("cutlist.py"), "--format", "vertical", "--pace", "natural")
    p = run(tmp, S("subtitles.py"))
    ass = (tmp / "02-work" / "subs.ass").read_text(encoding="utf-8") if p.returncode == 0 else ""
    checks["subtitles: the project's caption font and the design's accent"] = (
        "Style: Main,Bricolage Grotesque ExtraBold," in ass and "&H003DFFC6&" in ass)
    (tmp / "02-work" / "fonts.json").rename(tmp / "02-work" / "fonts.bak")
    (tmp / "02-work" / "design.json").rename(tmp / "02-work" / "design.bak")
    p = run(tmp, S("subtitles.py"))
    checks["subtitles: no project font → refuses (no system font fallback)"] = p.returncode != 0 and "fonts.py" in p.stderr
    (tmp / "02-work" / "fonts.bak").rename(tmp / "02-work" / "fonts.json")
    (tmp / "02-work" / "design.bak").rename(tmp / "02-work" / "design.json")
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "color=gray:s=640x360", "-frames:v", "1", "f.png"],
                   cwd=tmp, check=True)
    p = run(tmp, S("cover.py"), "make", "--image", "f.png", "--no-cutout", "--title", "Hola mundo", "--platform", "youtube")
    checks["covers: project font and design colors by default"] = p.returncode == 0 and \
        (tmp / "03-delivery" / "cover_youtube_1.jpg").exists()

    # Remotion layer.
    if not shutil.which("node"):
        print("SKIP  Remotion part: no Node")
    else:
        run(tmp, S("graphics.py"), "cues")
        plan = {"elements": [
            {"type": "headline", "from": {"t": 0.2}, "until": {"t": 2.5}, "x": 0.46, "y": 0.35, "width": 0.7,
             "size": 0.05, "lines": ["Hola", "esto es una prueba"], "highlight": ["prueba"], "surface": True},
            {"type": "cta", "from": {"word": "diseño"}, "x": 0.46, "y": 0.6, "width": 0.7, "size": 0.045,
             "icon": "brand-whatsapp", "lines": ["Escribinos"], "surface": True}]}
        (tmp / "02-work" / "graphics.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        p = run(tmp, S("graphics.py"), "init")
        checks["remotion: project installed from the kit"] = p.returncode == 0 and \
            (tmp / "02-work" / "remotion" / "node_modules").exists()
        p = run(tmp, S("graphics.py"), "still", "1.5")
        layer = tmp / "02-work" / "graphics-stills" / "layer_001.50.png"
        ok = layer.exists()
        if ok:
            a = rgba(layer, 1080, 1920)[:, :, 3]
            ok = (a > 200).mean() > 0.01 and (a == 0).mean() > 0.5
        checks["remotion: a frame of the layer has the graphics, transparent elsewhere"] = ok
        checks["remotion: inside the safe zone → no warning"] = "WARNING" not in p.stdout
        if "WARNING" in p.stdout:
            print("DEBUG", p.stdout[-600:])
        plan["elements"][0].update({"size": 0.09, "width": 1.0})
        (tmp / "02-work" / "graphics.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        p = run(tmp, S("graphics.py"), "still", "1.5")
        checks["remotion: too big → safe-zone warning"] = "crosses the safe zone" in p.stdout
        plan["elements"][0].update({"size": 0.05, "width": 0.7})
        (tmp / "02-work" / "graphics.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        p = run(tmp, S("graphics.py"), "render")
        mov = tmp / "02-work" / "graphics.mov"
        info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries", "stream=pix_fmt",
                               "-of", "csv=p=0", str(mov)], capture_output=True, text=True).stdout.strip() if mov.exists() else ""
        checks[f"remotion: full layer rendered with alpha ({info})"] = info.startswith("yuva")

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
