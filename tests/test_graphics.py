# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Graphics and sound-design tests: sfx.py, graphics.py, the graphics layer in render.py,
and subtitles --off.

    uv run tests/test_graphics.py

1. Synthesized sounds: every kind is audible, the whoosh peaks in its middle and the
   riser at its end (their event points), and leveling hits the target.
2. On the retake fixture cut by cutlist: a sound placed on a word lands on that
   word's timeline frame in the render (its event point, not its file start).
3. cues.json: every word's timeline time matches subtitles' mapping, cuts are listed.
4. A graphics layer with alpha shows where it's opaque and leaves the rest untouched.
5. subtitles --off drops burned-in captions in the range and keeps the .srt whole.
6. subtitles (default engine remotion) writes the groups as captions.json (for the layer), no .ass.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
FIX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(SCRIPTS))
import graphics  # noqa: E402
import sfx  # noqa: E402

FF = shutil.which("ffmpeg")


def run(cwd: Path, *a: str) -> str:
    return subprocess.run(["uv", "run", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=True).stdout


def pcm(path: Path) -> np.ndarray:
    raw = subprocess.run([FF, "-v", "error", "-i", str(path), "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def frame(video: Path, t: float, w: int, h: int) -> np.ndarray:
    raw = subprocess.run([FF, "-v", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", "-vf",
                          f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3).astype(int)


def main() -> None:
    checks = {}

    # 1. Synthesis.
    env = {}
    for k in sfx.KINDS:
        y, ev = sfx.synth(k)
        m = np.abs(y[:, 0]) + np.abs(y[:, 1])
        win = 480
        e = m[: len(m) // win * win].reshape(-1, win).mean(1)
        env[k] = (e, ev, len(y) / sfx.SR)
    checks["every sound is audible"] = all(e.max() > 0.05 for e, _, _ in env.values())
    e, ev, d = env["whoosh"]
    checks["whoosh peaks at its event point (middle)"] = abs(np.argmax(e) * 0.01 - ev) < 0.12
    e, ev, d = env["riser"]
    checks["riser peaks at its event point (end)"] = np.argmax(e) * 0.01 > d - 0.2 and abs(ev - d) < 0.01
    y, _ = sfx.synth("pop")
    g = sfx.level_db(y[:, 0])
    checks["leveling hits the target"] = abs(sfx.level_db(y[:, 0] * 10 ** (g / 20))) < 0.5

    # Project from the retake fixture.
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-gfx-"))
    ing = tmp / "01-ingest"
    ing.mkdir()
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "color=0x405060:s=1920x1080:r=30:d=31",
                    "-i", str(FIX / "retakes_es.wav"), "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "talk.mp4"], cwd=tmp, check=True)
    tr = json.loads((FIX / "retakes_es.transcript.json").read_text(encoding="utf-8"))
    tr["files"] = {"talk.mp4": tr["files"]["retakes_es.wav"]}
    for s in tr["sentences"]:
        s["file"] = "talk.mp4"
    (ing / "transcript.json").write_text(json.dumps(tr), encoding="utf-8")
    (ing / "material.json").write_text(json.dumps({"files": [
        {"name": "talk.mp4", "file": str(tmp / "talk.mp4"), "kind": "video", "duration": 31,
         "display_width": 1920, "display_height": 1080, "fps": 30}]}))
    run(tmp, str(SCRIPTS / "cutlist.py"), "--format", "vertical")

    # 3. Cues.
    run(tmp, str(SCRIPTS / "graphics.py"), "cues")
    cues = json.loads((tmp / "02-work" / "cues.json").read_text(encoding="utf-8"))
    from subtitles import timeline_words
    edl = json.loads((tmp / "02-work" / "edl.json").read_text(encoding="utf-8"))
    tw = timeline_words(edl, tr)
    cw = [w for ln in cues["lines"] for w in ln["words"]]
    checks[f"cues: every word on its timeline time ({len(cw)} words)"] = (
        len(cw) == len(tw) and all(abs(a["start"] - b["t"]) < 1e-6 for a, b in zip(cw, tw)))
    checks["cues: cuts listed, frames consistent"] = (len(cues["cuts"]) == len(edl["segments"]) - 1
                                                     and cues["frames"] == round(cues["duration"] * cues["fps"]))

    # 2. A pop on a word lands on that word.
    ln = cues["lines"][len(cues["lines"]) // 2]
    word = ln["words"][1]
    target = word["start"]
    run(tmp, str(SCRIPTS / "sfx.py"), "place", "--kind", "hit", "--line", f"L{ln['line']}",
        "--word", word["text"], "--gain", "12")
    run(tmp, str(SCRIPTS / "render.py"), "--preview", "--out", "with.mp4", "--no-grade", "--raw-audio")
    run(tmp, str(SCRIPTS / "render.py"), "--preview", "--out", "without.mp4", "--no-grade", "--raw-audio",
        "--no-sfx")
    a, b = pcm(tmp / "with.mp4"), pcm(tmp / "without.mp4")
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    # The loudness pass sets a slightly different gain once the sound is in: match it first.
    alpha = float(np.dot(a, b) / np.dot(b, b))
    diff = np.abs(a - alpha * b)
    win = 480
    env_d = diff[: n // win * win].reshape(-1, win).mean(1)
    onset = np.argmax(env_d > env_d.max() * 0.3) * 0.01
    checks[f"hit lands on its word ({onset:.2f} s vs {target:.2f} s)"] = abs(onset - target) < 0.04
    before = (tmp / "02-work" / "edl.json").read_text(encoding="utf-8")
    for w in ("main", "minor"):
        run(tmp, str(SCRIPTS / "sfx.py"), "place", "--kind", "pop", "--line", f"L{ln['line']}", "--weight", w)
    g = [s["gain_db"] for s in json.loads((tmp / "02-work" / "edl.json").read_text(encoding="utf-8"))["sfx"][-2:]]
    checks[f"--weight minor sits 8 dB under main ({g[0] - g[1]:.1f})"] = abs(g[0] - g[1] - 8) < 0.05
    (tmp / "02-work" / "edl.json").write_text(before, encoding="utf-8")
    # Music beats on the timeline: shifted by at/in, repeated per loop, none before the music starts.
    tb = graphics.timeline_beats([{"source": "m", "at": 2.0, "in": 1.0, "duration": 4.0}],
                                 lambda _: [0.5, 1.5, 3.0], 9.0)
    checks[f"music beats placed on the timeline ({tb})"] = tb == [2.5, 4.0, 5.5, 6.5, 8.0]
    placed, _ = sfx.resolve(edl["segments"], edl["output"]["fps"],
                            [{"sound": "riser", "event": 1.8, "anchor": {"name": "talk.mp4", "time": 10.0}}])
    checks["a riser starts its length before the anchor (peak on it)"] = (
        not placed or abs(placed[0]["at"] - placed[0]["start"] - 1.8) < 1e-6)

    # 4. Graphics layer.
    W, H, d = 540, 960, cues["duration"]
    subprocess.run([FF, "-v", "error", "-y", "-filter_complex",
                    f"color=black@0:s=1080x1920:r=30:d={d},format=rgba[bg];"
                    f"color=red:s=600x200:r=30:d={d},format=rgba[b];"
                    "[bg][b]overlay=240:400:format=auto,format=yuva444p10le[o]", "-map", "[o]",
                    "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
                    str(tmp / "02-work" / "graphics.mov")], check=True)
    run(tmp, str(SCRIPTS / "render.py"), "--preview", "--out", "g.mp4", "--no-grade", "--raw-audio", "--no-sfx")
    img = frame(tmp / "g.mp4", 1.0, W, H)
    box, rest = img[int(H * 0.25), W // 2], img[int(H * 0.8), W // 2]
    checks[f"graphics: opaque part shows ({box.tolist()})"] = box[0] > 180 and box[1] < 80
    checks[f"graphics: transparent part leaves the video ({rest.tolist()})"] = (
        abs(rest[0] - 0x40) < 20 and abs(rest[2] - 0x60) < 20)

    # 5. subtitles --off.
    a0, a1 = cues["lines"][0]["start"], cues["lines"][0]["end"]
    run(tmp, str(SCRIPTS / "subtitles.py"), "--engine", "ass", "--off", f"{a0}-{a1}", "--font", "Arial Black")
    ass = (tmp / "02-work" / "subs.ass").read_text(encoding="utf-8")

    def secs(s: str) -> float:
        h, m, x = s.split(":")
        return int(h) * 3600 + int(m) * 60 + float(x)
    starts = [secs(l.split(",")[1]) for l in ass.splitlines() if l.startswith("Dialogue:")]
    checks["subs --off: nothing burned in the range"] = starts and not any(a0 + 0.05 < t < a1 - 0.3 for t in starts)
    srt = next((tmp / "03-delivery").glob("*.srt")).read_text(encoding="utf-8")
    first = cues["lines"][0]["words"][0]["text"]
    checks["subs --off: the .srt keeps it"] = re.search(re.escape(first), srt) is not None

    # 6. --engine remotion: the same groups as JSON for the layer, no .ass; back to ass removes it.
    run(tmp, str(SCRIPTS / "subtitles.py"), "--font", "Arial Black")   # remotion is the default
    cj_path = tmp / "02-work" / "captions.json"
    cj = json.loads(cj_path.read_text(encoding="utf-8")) if cj_path.exists() else {"groups": []}
    g = cj["groups"]
    checks[f"captions.json: {len(g)} groups for this cut, no subs.ass"] = (
        g and cj["timeline"] == cues["timeline"] and not (tmp / "02-work" / "subs.ass").exists()
        and all(a["end"] <= b["start"] + 1e-3 for a, b in zip(g, g[1:]))
        and all(x["start"] <= x["words"][0]["t"] + 1e-3 and 0 < x["y"] < 1 and 0 < x["size"] < 0.2 for x in g))
    run(tmp, str(SCRIPTS / "subtitles.py"), "--engine", "ass", "--font", "Arial Black")
    checks["--engine ass again: subs.ass back, captions.json gone"] = (
        (tmp / "02-work" / "subs.ass").exists() and not cj_path.exists())

    # 7. --engine auto: a long video with no graphics plan → ass; with a plan → the layer.
    long = dict(edl, segments=edl["segments"] + [dict(edl["segments"][-1], out=edl["segments"][-1]["in"] + 200)])
    (tmp / "02-work" / "edl_long.json").write_text(json.dumps(long), encoding="utf-8")
    run(tmp, str(SCRIPTS / "subtitles.py"), "--variant", "long", "--font", "Arial Black")
    no_plan = (tmp / "02-work" / "subs_long.ass").exists() and not (tmp / "02-work" / "captions_long.json").exists()
    (tmp / "02-work" / "graphics.json").write_text('{"elements": []}', encoding="utf-8")
    run(tmp, str(SCRIPTS / "subtitles.py"), "--variant", "long", "--font", "Arial Black")
    checks["auto engine: long without graphics → ass; with a plan → the layer"] = (
        no_plan and (tmp / "02-work" / "captions_long.json").exists()
        and not (tmp / "02-work" / "subs_long.ass").exists())
    (tmp / "02-work" / "graphics.json").unlink()

    # 8. graphics.py init: Root.tsx edited in Studio is kept; untouched, it follows the kit.
    proj = tmp / "02-work" / "remotion"
    kit_root = (SCRIPTS.parent / "kit" / "remotion" / "src" / "Root.tsx").read_text(encoding="utf-8")
    (proj / "node_modules").mkdir(parents=True, exist_ok=True)     # skip npm: the stamp says it's installed
    (proj / "node_modules" / ".vibetake-package.json").write_text(
        (SCRIPTS.parent / "kit" / "remotion" / "package.json").read_text(encoding="utf-8"), encoding="utf-8")
    run(tmp, str(SCRIPTS / "graphics.py"), "init")
    root = proj / "src" / "Root.tsx"
    fresh = root.read_text(encoding="utf-8") == kit_root
    root.write_text(kit_root.replace("yShift: 0", "yShift: 0.1"), encoding="utf-8")       # a Studio edit
    run(tmp, str(SCRIPTS / "graphics.py"), "init")
    kept = "yShift: 0.1" in root.read_text(encoding="utf-8")
    for p in (root, proj / ".vibetake-root.tsx"):                                          # an older kit, untouched
        p.write_text("// old kit, captionStyle\n", encoding="utf-8")
    run(tmp, str(SCRIPTS / "graphics.py"), "init")
    checks["init: Studio edits to Root.tsx kept, an untouched one updated"] = (
        fresh and kept and root.read_text(encoding="utf-8") == kit_root)

    # A graphic that shows the words being said takes the caption's place (only there, only those words).
    g = lambda a, b, txt: {"start": a, "end": b, "words": [{"text": w} for w in txt.split()]}
    cue = {"lines": [{"line": 3, "words": [{"text": "Ventas", "start": 4.0}]}]}
    cap = {"groups": [g(0, 1, "tu compañero"), g(1, 2, "no te responde"), g(2, 3, "y eso cansa"),
                      g(4, 5, "las ventas subieron"), g(6, 7, "no te responde"), g(8, 9, "otra cosa")]}
    plan = {"elements": [
        {"type": "headline", "from": {"t": 0}, "until": {"t": 2.9}, "lines": ["POV:", "tu compañero", "no te responde"]},
        {"type": "label", "from": {"word": "ventas"}, "until": {"t": 5.5}, "title": "Ventas", "subtitle": "subieron"},
        {"type": "custom", "from": {"t": 8}, "until": {"t": 9}, "component": "X", "captions": "hide"},
        {"type": "headline", "from": {"t": 6}, "until": {"t": 7}, "lines": ["no te responde"], "captions": "show"}]}
    out, hidden = graphics.shown_by_graphics(cap, plan, cue)
    checks["captions: hidden only where a graphic shows the same words, show/hide override it"] = (
        [x["start"] for x in out["groups"]] == [2, 6] and hidden == [(0, 2), (4, 5), (8, 9)])

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
