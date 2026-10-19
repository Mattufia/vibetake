# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""The graphics layer: a cue sheet from the edit, and the Remotion project that animates
this video's design.

    uv run graphics.py cues        # words, cuts, face, safe zone → 02-work/cues.json
    uv run graphics.py init        # Remotion project with Vibetake's kit → 02-work/remotion/
    uv run graphics.py still 2.4   # one frame of the layer over the video → 02-work/graphics-stills/
    uv run graphics.py render      # the whole layer, with alpha → 02-work/graphics.mov

init copies the kit (motion pieces, no house style) and installs it once per project;
every still/render first syncs the project with 02-work: design.json (this video's
design), graphics.json (the scene plan), cues.json, the fonts from fonts.py and the
icons from icons.py. Components written for the video go in
02-work/remotion/src/custom/ and are kept.

Run it after the edit and the framing are final. It writes, in timeline seconds and
frames of the output:
    lines     every kept transcript line with its words (text, start, end, frame)
    cuts      where each cut lands
    hook      the cold open's span, if there is one
    face      where the head is on screen every 0.5 s (top/bottom, 0-1 of the height)
    safe      the safe zone for this format
    captions  where burned-in subtitles sit (so graphics take another band)
The graphics project (Remotion or canvas) reads it as data: every element enters on
the frame of the word that names it, and stays out of the face and caption bands.
Sound effects anchor to the same words (sfx.py place --line --word), so picture and
sound land together.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import die, load_json as load, work_paths  # noqa: E402
from _timeline import resolve_broll, signature, starts, total  # noqa: E402
from subtitles import SAFE_H, SAFE_V, SAFE_X_V, face_band, timeline_words  # noqa: E402


def build(edl: dict, tr: dict, faces: dict, material: dict) -> dict:
    W, H, fps = edl["output"]["width"], edl["output"]["height"], edl["output"]["fps"]
    segs = edl["segments"]
    t0 = starts(segs, fps)
    end = total(segs, fps)
    words = timeline_words(edl, tr)
    by_line: dict[int, dict] = {}
    for w in words:
        seg = segs[w["seg"]]
        sent = next((s for s in tr["sentences"] if s["file"] == seg["name"]
                     and s["start"] - 1e-3 <= w["src"] <= s["end"] + 1e-3), None)
        if sent is None:
            continue
        key = (sent["line"], w["seg"])                     # a line repeated (cold open) appears twice
        ln = by_line.setdefault(key, {"line": sent["line"], "role": seg.get("role", ""), "words": []})
        ln["words"].append({"text": w["text"], "start": w["t"], "end": w["end"], "frame": round(w["t"] * fps)})
    lines = sorted(by_line.values(), key=lambda x: x["words"][0]["start"])
    for ln in lines:
        ln["text"] = " ".join(w["text"] for w in ln["words"])
        ln["start"], ln["end"] = ln["words"][0]["start"], ln["words"][-1]["end"]
    broll, _ = resolve_broll(segs, fps, edl.get("broll", []))
    face = []
    t = 0.0
    while t < end:
        b = face_band(t, edl, t0, faces, material, broll)
        face.append({"t": round(t, 2), "top": round(b[0], 3), "bottom": round(b[1], 3)} if b
                    else {"t": round(t, 2), "top": None, "bottom": None})
        t += 0.5
    vertical = H > W
    sv = SAFE_V if vertical else SAFE_H
    sx = SAFE_X_V if vertical else (0.05, 0.95)
    hook = next(({"start": t0[k], "end": t0[k] + s["out"] - s["in"]} for k, s in enumerate(segs)
                 if s.get("role") == "hook"), None)
    return {"width": W, "height": H, "fps": fps, "duration": round(end, 3), "frames": round(end * fps),
            "safe": {"x0": sx[0], "y0": sv[0], "x1": sx[1], "y1": sv[1]},
            "captions": {"default_y": 0.70 if vertical else 0.86,
                         "note": "subtitles.py moves them below or above the face; keep graphics clear of both"},
            "hook": hook, "cuts": [round(x, 3) for x in t0[1:]], "lines": lines, "face": face}


KIT = Path(__file__).resolve().parent.parent / "kit" / "remotion"
PROJ = Path("02-work/remotion")


def node_cmd(*a: str) -> list[str]:
    """npm/npx are .cmd scripts on Windows: run them through the shell there."""
    exe = shutil.which(a[0])
    if not exe:
        die(f"{a[0]} not found: Node.js is needed for the graphics layer (setup.py install --only node)")
    return (["cmd", "/c", exe, *a[1:]] if os.name == "nt" else [exe, *a[1:]])


def remotion(*a: str) -> list[str]:
    cli = PROJ / "node_modules" / "@remotion" / "cli" / "remotion-cli.js"
    if not cli.exists():
        die("the Remotion project isn't installed: graphics.py init")
    return [shutil.which("node") or "node", str(cli.resolve()), *a]


def cmd_init(args) -> None:
    PROJ.mkdir(parents=True, exist_ok=True)
    for f in KIT.rglob("*"):
        rel = f.relative_to(KIT)
        if f.is_dir() or rel.parts[0] == "node_modules":
            continue
        dst = PROJ / rel
        # The video's own components and data are never touched by the kit once they exist:
        # a folder that already has files is the video's, whole.
        own = rel.parts[:2] in (("src", "custom"), ("src", "data"), ("src", "covers"))
        if own and (dst.exists() or any((PROJ / rel.parts[0] / rel.parts[1]).glob("*"))):
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)
    stamp = PROJ / "node_modules" / ".vibetake-package.json"
    pkg = (PROJ / "package.json").read_text(encoding="utf-8")
    if not stamp.exists() or stamp.read_text(encoding="utf-8") != pkg:
        # First time, or the kit's dependencies changed since this project was installed.
        print("installing the Remotion project (first time per project: npm + a headless browser, a few minutes)…",
              flush=True)
        p = subprocess.run(node_cmd("npm", "install", "--no-audit", "--no-fund"), cwd=PROJ)
        if p.returncode:
            die("npm install failed (see above)")
        stamp.write_text(pkg, encoding="utf-8")
    print(f"Remotion project → {PROJ} (kit pieces in src/kit, your components in src/custom)")


def cmd_sync(args) -> None:
    if not (PROJ / "src").exists():
        die("no Remotion project yet: graphics.py init")
    data = PROJ / "src" / "data"
    data.mkdir(parents=True, exist_ok=True)
    work = Path("02-work")
    # Covers: their plan (covers.json) and the frames and cut-outs they use.
    cp = work / "covers.json"
    (data / "covers.json").write_text(cp.read_text(encoding="utf-8") if cp.exists()
                                      else '{"platforms": [], "concepts": []}', encoding="utf-8")
    pc = PROJ / "public" / "covers"
    pc.mkdir(parents=True, exist_ok=True)
    for f in (work / "covers").glob("*.png") if (work / "covers").exists() else []:
        if f.name.startswith(("cutout_", "frame_")):
            shutil.copy2(f, pc / f.name)
    # The user's brand assets, as provided (logo, marks): staticFile("brand/<name>").
    pb = PROJ / "public" / "brand"
    if Path("brand").is_dir():
        pb.mkdir(parents=True, exist_ok=True)
        for f in Path("brand").iterdir():
            if f.suffix.lower() in (".png", ".svg", ".jpg", ".jpeg", ".webp"):
                shutil.copy2(f, pb / f.name)
    if getattr(args, "covers_only", False) and not (work / "graphics.json").exists():
        for name in ("cues.json", "design.json"):
            if (work / name).exists():
                shutil.copy2(work / name, data / name)
        if not (work / "graphics.json").exists() and not (data / "graphics.json").exists():
            (data / "graphics.json").write_text('{"elements": []}', encoding="utf-8")
    paths = work_paths(getattr(args, "variant", None))
    plan = paths["plan"] if paths["plan"].exists() else work / "graphics.json"   # a variant may reuse the plan
    sources = {"cues.json": Path(getattr(args, "out", None) or paths["cues"]), "design.json": work / "design.json",
               "graphics.json": plan}
    for name, src in sources.items():
        if getattr(args, "covers_only", False) and not src.exists() and (data / name).exists():
            continue
        if not src.exists():
            die(f"02-work/{name} is missing" + (" (graphics.py cues)" if name == "cues.json" else
                " — write this video's design first (references/design.md)" if name == "design.json" else
                " — write the scene plan (references/graphics.md)"))
        shutil.copy2(src, data / name)
    # Fonts: every project font, loaded by family and weight.
    pub = PROJ / "public" / "fonts"
    pub.mkdir(parents=True, exist_ok=True)
    fonts = json.loads((work / "fonts.json").read_text(encoding="utf-8")) if (work / "fonts.json").exists() else []
    lines = ['// generated by graphics.py sync: the project fonts', 'import { loadFont } from "@remotion/fonts";',
             'import { staticFile } from "remotion";', "", "export const fontsReady = Promise.all(["]
    for f in fonts:
        shutil.copy2(f["file"], pub / Path(f["file"]).name)
        lines.append(f'  loadFont({{ family: {json.dumps(f["family"])}, url: staticFile("fonts/{Path(f["file"]).name}"), '
                     f'weight: "{f["weight"]}", style: "{"italic" if f.get("italic") else "normal"}" }}),')
    lines.append("]);")
    (data / "fonts.ts").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Icons: inline SVG strings, keyed by file stem (brand-whatsapp, ui-check, custom-…).
    icons = {p.stem: p.read_text(encoding="utf-8") for p in sorted((work / "icons").glob("*.svg"))} \
        if (work / "icons").exists() else {}
    (data / "icons.ts").write_text("export const ICONS: Record<string, string> = " + json.dumps(icons, ensure_ascii=False)
                                   + ";\n", encoding="utf-8")
    print(f"synced: design, scene plan ({len(json.loads((data / 'graphics.json').read_text(encoding='utf-8')).get('elements', []))} "
          f"elements), {len(fonts)} font file(s), {len(icons)} icon(s)")


def background_frame(t: float, w: int, h: int) -> Path | None:
    """The edit's frame at t without graphics or subtitles (a small clean preview, redone when
    the edit changes), so a still shows the layer over the real image and nothing else."""
    clean = Path("02-work/preview_clean.mp4")
    edl = Path("02-work/edl.json")
    if not clean.exists() or (edl.exists() and edl.stat().st_mtime > clean.stat().st_mtime):
        print("rendering a clean preview for the stills (no graphics, no subtitles)…", flush=True)
        subprocess.run(["uv", "run", str(Path(__file__).parent / "render.py"), "--preview", "--no-graphics",
                        "--no-subs", "--no-sfx", "--out", str(clean)], capture_output=True)
    if not clean.exists():
        return None
    out = Path("02-work/graphics-stills") / f"_bg_{t:06.2f}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(clean),
                    "-frames:v", "1", "-vf", f"scale={w}:{h}", "-update", "1", str(out)], check=False)
    return out if out.exists() else None


def layer_problems(layer: Path, t: float, cues: dict) -> list[str]:
    """What a frame of the layer gets wrong that a still shows: outside the safe zone, over the face."""
    import numpy as np
    W, H = cues["width"], cues["height"]
    raw = subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-i", str(layer), "-f", "rawvideo",
                          "-pix_fmt", "rgba", "-"], capture_output=True).stdout
    if len(raw) != W * H * 4:
        return []
    a = np.frombuffer(raw, np.uint8).reshape(H, W, 4)[:, :, 3] > 90      # the shapes, not their soft shadows
    if not a.any():
        return []
    ys, xs = np.nonzero(a)
    x0, x1, y0, y1 = xs.min() / W, xs.max() / W, ys.min() / H, ys.max() / H
    sf = cues["safe"]
    out = []
    for side, bad in (("left", x0 < sf["x0"] - 0.005), ("right", x1 > sf["x1"] + 0.005),
                      ("top", y0 < sf["y0"] - 0.005), ("bottom", y1 > sf["y1"] + 0.005)):
        if bad:
            out.append(f"crosses the safe zone on the {side} (graphics span x {x0:.2f}–{x1:.2f}, y {y0:.2f}–{y1:.2f}; "
                       f"safe x {sf['x0']:.2f}–{sf['x1']:.2f}, y {sf['y0']:.2f}–{sf['y1']:.2f})")
    face = min(cues.get("face", []), key=lambda f: abs(f["t"] - t), default=None)
    if face and face.get("top") is not None:
        fy0, fy1 = int(face["top"] * H), int(face["bottom"] * H)
        band = a[max(fy0, 0):max(fy1, 0)]
        if band.size and band.mean() > 0.02:
            out.append("covers part of the face")
    return out


def cmd_still(args) -> None:
    cmd_sync(args)
    cues = load(Path(args.out))
    outdir = Path("02-work/graphics-stills")
    outdir.mkdir(parents=True, exist_ok=True)
    for t in args.times:
        frame = min(int(round(t * cues["fps"])), cues["frames"] - 1)
        layer = (outdir / f"layer_{t:06.2f}.png").resolve()
        p = subprocess.run(remotion("still", "Graphics", str(layer), f"--frame={frame}", "--image-format=png",
                                    "--log=error"), cwd=PROJ)
        if p.returncode:
            die("remotion still failed (see above)")
        bg = background_frame(t, cues["width"], cues["height"])
        dst = outdir / f"over_{t:06.2f}.png"
        if bg:
            subprocess.run([shutil.which("ffmpeg") or "ffmpeg", "-v", "error", "-y", "-i", str(bg), "-i", str(layer),
                            "-filter_complex", "[0][1]overlay=format=auto", "-update", "1", str(dst)], check=True)
            bg.unlink(missing_ok=True)
        print(dst if bg else layer)
        for prob in layer_problems(layer, t, cues):
            print(f"WARNING: at {t:.2f} s the graphics {prob}")


def cmd_render(args) -> None:
    cmd_sync(args)
    out = work_paths(args.variant)["layer"].resolve()
    p = subprocess.run(remotion("render", "Graphics", str(out), "--codec=prores", "--prores-profile=4444",
                                "--pixel-format=yuva444p10le", "--image-format=png"), cwd=PROJ)
    if p.returncode:
        die("remotion render failed (see above)")
    cues = load(Path(args.out))
    out.with_suffix(".meta.json").write_text(json.dumps(
        {"timeline": cues.get("timeline"), "width": cues["width"], "height": cues["height"]}), encoding="utf-8")
    print(f"graphics layer → {out} (render.py lays it over the edit)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("cues", "init", "sync", "still", "render"))
    ap.add_argument("times", nargs="*", type=float, help="still: seconds on the edit's timeline")
    ap.add_argument("--edl")
    ap.add_argument("--ingest", default="01-ingest")
    ap.add_argument("--out", help="the cue sheet (default: 02-work/cues.json; a variant: cues_<variant>.json)")
    ap.add_argument("--variant", help="a variant of the video (hookB, 4x5…): its own cues and layer "
                                      "(graphics_<variant>.mov); its own plan if graphics_<variant>.json exists")
    args = ap.parse_args()
    paths = work_paths(args.variant)
    args.edl, args.out = args.edl or str(paths["edl"]), args.out or str(paths["cues"])
    if args.cmd != "cues":
        if args.cmd == "still" and not args.times:
            die("still needs one or more times, e.g. graphics.py still 0.5 2.4")
        {"init": cmd_init, "sync": cmd_sync, "still": cmd_still, "render": cmd_render}[args.cmd](args)
        return
    ing = Path(args.ingest)
    edl = load(Path(args.edl))
    tr = load(ing / "transcript.json")
    faces = load(ing / "faces.json")["files"] if (ing / "faces.json").exists() else {}
    material = {m["name"]: m for m in load(ing / "material.json")["files"]} if (ing / "material.json").exists() \
        else {}
    cues = build(edl, tr, faces, material)
    cues["timeline"] = signature(edl)          # render.py checks the layer belongs to this cut
    if not cues["lines"]:
        die("no transcript words inside the edit")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(cues, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{len(cues['lines'])} lines, {len(cues['cuts'])} cuts, {cues['duration']:.1f} s "
          f"({cues['frames']} frames @ {cues['fps']}) → {out}")
    for ln in cues["lines"]:
        print(f"  [{ln['start']:6.2f}] L{ln['line']}{' (hook)' if ln['role'] == 'hook' else ''}: {ln['text']}")


if __name__ == "__main__":
    main()
