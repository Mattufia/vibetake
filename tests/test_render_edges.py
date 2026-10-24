# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Render and audio edge cases (from the review of render/audio/upscale).

    uv run tests/test_render_edges.py

Long span lists (ffmpeg's parser fails past ~100 '+' terms): bleeps, "behind" over many
b-rolls, speech leveling over 100+ spans; a static redaction during a transition's handles;
paths with an apostrophe, spaces and ñ (filters and the concat list of a chunked render, in its
own work folder); cleaned audio keeping a late audio start; music that keeps a montage's block;
a fade of 0; the master's gain cap; the upscale signature seeing every picture effect.
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
sys.path.insert(0, str(SCRIPTS))
import audio  # noqa: E402
import render  # noqa: E402
from _common import Log, concat_line, filter_path, spans_expr  # noqa: E402
from upscale import signature  # noqa: E402

FF = shutil.which("ffmpeg")
FPS = 30


def ff(cwd: Path, *a: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run([FF, "-hide_banner", "-y", *a], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=check)


def uv(cwd: Path, *a: str) -> subprocess.CompletedProcess:
    return subprocess.run(["uv", "run", str(SCRIPTS / a[0]), *a[1:]], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def gray_frames(path: Path, w: int = 320, h: int = 180) -> np.ndarray:
    raw = subprocess.run([FF, "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w).astype(float)


def pcm(path: Path, rate: int = 48000) -> np.ndarray:
    """The left channel (a downmix would add both)."""
    raw = subprocess.run([FF, "-v", "error", "-i", str(path), "-af", "pan=mono|c0=c0", "-ar", str(rate), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32)


def duration(path: Path, kind: str = "format") -> float:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", f"{kind}=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(p.stdout.split()[0])


def main() -> None:
    checks = {}
    # The project folder itself has an apostrophe, spaces and ñ ("John's vídeos").
    tmp = Path(tempfile.mkdtemp(prefix="vt-redges-")) / "John's vídeos ñ"
    tmp.mkdir()
    (tmp / "02-work").mkdir()

    # ---- spans_expr: 300 overlapping spans compile everywhere, and select exactly their union.
    spans = [(i * 0.1, i * 0.1 + 0.06) for i in range(300)] + [(i * 0.1 + 0.03, i * 0.1 + 0.08) for i in range(300)]
    e = spans_expr(spans)
    (tmp / "g.txt").write_text(f"aselect='{e}',asetpts=N/SR/TB", encoding="utf-8")
    # 1 ms audio frames: aselect keeps or drops whole frames.
    ff(tmp, "-f", "lavfi", "-i", "sine=d=31:r=48000:samples_per_frame=48", "-/af", "g.txt", "sel.wav", check=False)
    got = duration(tmp / "sel.wav") if (tmp / "sel.wav").exists() else 0
    t = np.arange(31000) / 1000                     # a frame is kept when its start is inside a span
    inside = np.zeros(len(t), bool)
    for a, b in spans:
        inside |= (t >= a - 1e-6) & (t <= b + 1e-6)
    want = inside.sum() / 1000
    # Within a frame per span edge (float time vs 4-decimal bounds).
    checks[f"600 overlapping spans: aselect keeps their union ({got:.3f} s, wants {want:.3f})"] = abs(got - want) < 0.1
    ok = True
    for src, opt, g in (("testsrc2=s=64x64:d=1", "-/vf", f"select='{e}'"), ("sine=d=1", "-/af", f"volume=0:enable='{e}'")):
        (tmp / "g.txt").write_text(g, encoding="utf-8")
        ok &= ff(tmp, "-f", "lavfi", "-i", src, opt, "g.txt", "-f", "null", "-", check=False).returncode == 0
    checks["600 spans: select and a timeline enable parse"] = ok

    # ---- filter paths and concat lists with an apostrophe, spaces and ñ.
    lut = tmp / "id.cube"
    lut.write_text("LUT_3D_SIZE 2\n" + "".join(f"{r} {g} {b}\n" for b in (0, 1) for g in (0, 1) for r in (0, 1)))
    cmd = tmp / "c.cmd"
    cmd.write_text("0.1 crop@t x 10;\n", encoding="utf-8")
    p = ff(tmp, "-f", "lavfi", "-i", "testsrc2=s=320x180:d=0.3", "-vf",
           f"lut3d=file='{filter_path(lut)}',sendcmd=f='{filter_path(cmd)}',crop@t=w=100:h=100", "-f", "null", "-",
           check=False)
    checks["filter_path: lut3d + sendcmd from a folder named John's vídeos ñ"] = p.returncode == 0
    for n in (1, 2):
        ff(tmp, "-f", "lavfi", "-i", f"testsrc2=s=64x64:r={FPS}:d=1", "-c:v", "libx264", "-preset", "ultrafast",
           f"p'{n}.mkv")
    (tmp / "l.txt").write_text(concat_line(tmp / "p'1.mkv") + concat_line(tmp / "p'2.mkv"), encoding="utf-8")
    p = ff(tmp, "-f", "concat", "-safe", "0", "-i", "l.txt", "-c", "copy", "cat.mkv", check=False)
    checks["concat list: files with an apostrophe join"] = p.returncode == 0 and abs(duration(tmp / "cat.mkv") - 2) < 0.1

    # ---- finish_graph: "behind" + graphics + 3 b-rolls, 150 bleeped words (overlapping spans).
    ff(tmp, "-f", "lavfi", "-i", f"testsrc2=s=320x180:r={FPS}:d=12", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
       "-t", "12", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "src.mkv")
    ff(tmp, "-f", "lavfi", "-i", f"color=red@0.5:s=320x180:r={FPS}:d=12,format=rgba", "-c:v", "png", "gfx.mov")
    ff(tmp, "-f", "lavfi", "-i", f"color=gray:s=320x180:r={FPS}:d=2", "-c:v", "libx264", "-preset", "ultrafast",
       "-pix_fmt", "yuv420p", "mask.mkv")
    out = {"width": 320, "height": 180, "fps": FPS}
    words = [(i * 0.07, i * 0.07 + 0.05) for i in range(150)]
    fin = {"overlays": [{"source": str(tmp / "src.mkv"), "clip": "src.mkv", "in": 1.0, "dur": 1.0, "at": a}
                        for a in (1.0, 4.0, 8.0)],
           "music": [], "total": 12.0, "subs": None, "graphics": tmp / "gfx.mov", "sfx": [],
           "behind": [(0.5, str(tmp / "src.mkv"), str(tmp / "mask.mkv"))],
           "bleep": {"spans": [(max(a - 0.04, 0), b + 0.04) for a, b in words], "sound": "beep"}}
    f_inputs, f_graphs = render.finish_graph(1, "0:v", "0:a", fin, out, {})
    (tmp / "fin.txt").write_text(";".join(f_graphs), encoding="utf-8")
    p = ff(tmp, "-i", "src.mkv", *f_inputs, "-/filter_complex", "fin.txt", "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "pcm_s16le", "fin.mkv", check=False)
    checks["behind + graphics + b-roll and 150 bleeps render"] = p.returncode == 0
    if p.returncode == 0:
        x = pcm(tmp / "fin.mkv")
        peak = float(np.abs(x[: int(10 * 48000)]).max())
        checks[f"overlapping bleeps don't stack (tone peak {peak:.3f}, one tone ≤ 0.2)"] = 0.05 < peak <= 0.21
    else:
        print(p.stderr[-800:])

    # ---- a static redact box covers the transition's handles too.
    ff(tmp, "-f", "lavfi", "-i", f"color=gray:s=320x180:r={FPS}:d=4", "-f", "lavfi", "-i", "sine=d=4:r=48000",
       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "flat.mkv")
    edl = {"version": 1, "output": out, "segments": [
        {"source": str(tmp / "flat.mkv"), "name": "flat.mkv", "in": 0.5, "out": 2.0},
        {"source": str(tmp / "src.mkv"), "name": "src.mkv", "in": 2.0, "out": 3.5,
         "transition": {"type": "dissolve", "dur": 0.6}, "redact": [{"box": [0, 0, 1, 1], "mode": "blur"}]}]}
    (tmp / "02-work" / "edl.json").write_text(json.dumps(edl), encoding="utf-8")
    r = uv(tmp, "render.py", "--out", "redact.mp4", "--no-grade")
    if r.returncode == 0:
        fr = gray_frames(tmp / "redact.mp4")
        detail = [float(np.abs(np.diff(f, axis=1)).mean()) for f in fr]
        sharp = float(np.abs(np.diff(gray_frames(tmp / "src.mkv")[60], axis=1)).mean())
        checks[f"static redaction holds through the dissolve (most detail {max(detail):.2f}, "
               f"the face unblurred {sharp:.2f})"] = max(detail) < sharp / 4
    else:
        checks["static redaction render"] = False
        print(r.stderr[-1500:])
    seg = {"in": 2.0, "out": 3.5, "_probe": render.ffprobe(tmp / "src.mkv")}
    work = tmp / "02-work"
    edges_box = render.redact(0, {**seg, "redact": [{"box": [0.1, 0.1, 0.2, 0.2], "from": 2.0, "to": 3.5}]},
                              0.3, FPS, work)
    mid_box = render.redact(0, {**seg, "redact": [{"box": [0.1, 0.1, 0.2, 0.2], "from": 2.5, "to": 3.0}]}, 0.3, FPS, work)
    checks["a from/to box at the segment's edges covers the handles; one inside keeps its span"] = (
        "enable" not in edges_box[0] and "between(t,0.8000,1.3000)" in mid_box[0])

    # ---- a chunked render (41 segments) in the apostrophe folder: concat list, graph file, own work folder.
    edl = {"version": 1, "output": out, "broll": [],
           "segments": [{"source": str(tmp / "src.mkv"), "name": "src.mkv", "in": i * 0.25, "out": i * 0.25 + 0.2,
                         **({"fade_in": {"dur": 0}} if i == 0 else {})} for i in range(41)]}
    (tmp / "02-work" / "edl.json").write_text(json.dumps(edl), encoding="utf-8")
    r = uv(tmp, "render.py", "--out", "chunks.mp4", "--no-grade")
    checks["chunked render with an apostrophe in the project path"] = r.returncode == 0 and \
        abs(duration(tmp / "chunks.mp4") - 41 * 0.2) < 0.1
    checks["chunk parts in a per-output folder, cleaned up"] = not (tmp / "02-work/render/part_000.mkv").exists() \
        and not (tmp / "02-work/render/chunks").exists()
    if r.returncode:
        print(r.stderr[-1500:])
    if r.returncode == 0:
        ys = gray_frames(tmp / "chunks.mp4")
        checks["fade_in dur 0: no fade (the first frame isn't black)"] = ys[0].mean() > 40

    # ---- cleaned audio keeps a late start; speech is leveled over 100+ spans.
    D = 150.0
    ff(tmp, "-f", "lavfi", "-i", f"color=black:s=160x120:r=10:d={D}", "-f", "lavfi",
       "-i", f"sine=f=300:d={D - 0.48}:r=48000,volume=0.05", "-c:v", "libx264", "-preset", "ultrafast",
       "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", "v.mkv")
    ff(tmp, "-i", "v.mkv", "-itsoffset", "0.48", "-i", "v.mkv", "-map", "0:v", "-map", "1:a", "-c", "copy", "late.mkv")
    ing = tmp / "01-ingest"
    ing.mkdir()
    (ing / "material.json").write_text(json.dumps({"files": [
        {"name": "late.mkv", "file": str(tmp / "late.mkv"), "kind": "video", "duration": D,
         "audio_tracks": [{"index": 0, "channels": 1}], "audio": [{"index": 0}]}]}), encoding="utf-8")
    ws = [{"word": "a", "start": 1 + i * 1.3, "end": 1 + i * 1.3 + 0.25} for i in range(int((D - 3) / 1.3))]
    (ing / "transcript.json").write_text(json.dumps({"files": {"late.mkv": {"track": 0, "segments": [
        {"words": ws}]}}}), encoding="utf-8")
    r = uv(tmp, "audio.py", "clean", "--nr", "off")
    n_spans = len(audio.speech_spans(ws))
    idx = json.loads((ing / "audio" / "clean.json").read_text()) if r.returncode == 0 else {}
    c = idx.get("late.mkv", {})
    checks[f"speech leveled over {n_spans} spans (measured {c.get('measured_lufs')} LUFS)"] = (
        n_spans > 100 and c.get("measured_lufs") is not None and abs(c.get("gain_db", 0)) > 1)
    wav = Path(tmp / c["file"]) if c else None
    if wav and wav.exists():
        x = pcm(wav)
        onset = int(np.argmax(np.abs(x) > 0.01)) / 48000
        checks[f"clean audio keeps the late start ({duration(wav):.2f} s for {D:.0f} s, sound from {onset:.2f} s)"] = (
            abs(duration(wav) - D) < 0.06 and abs(onset - 0.48) < 0.03)
    else:
        checks["clean audio made"] = False
        print(r.stdout[-800:], r.stderr[-800:])

    # ---- music: a montage block survives a new bed and --clear; a fade of 0 is none.
    block = {"source": "drums.wav", "in": 0, "at": 0, "gain_db": 0, "fade_in": 0, "fade_out": 0, "block": "montage",
             "duration": 5, "until": 5}
    ff(tmp, "-f", "lavfi", "-i", "sine=f=200:d=5:r=48000", "bed.wav")
    (tmp / "02-work" / "edl.json").write_text(json.dumps({"segments": [], "music": [block]}), encoding="utf-8")
    uv(tmp, "audio.py", "music", "--file", "bed.wav", "--fade-in", "0")
    mus = json.loads((tmp / "02-work" / "edl.json").read_text())["music"]
    checks["music without --append keeps the montage's block"] = len(mus) == 2 and mus[0].get("block") == "montage"
    uv(tmp, "audio.py", "music", "--clear")
    mus = json.loads((tmp / "02-work" / "edl.json").read_text()).get("music", [])
    checks["music --clear keeps the montage's block"] = len(mus) == 1 and mus[0].get("block") == "montage"
    _, g, _ = audio.music_graph(1, "voice", [{**block, "block": None}], 5.0)
    checks["music fade 0: no afade (its d=0 means 1 s)"] = "afade" not in ";".join(g)

    # ---- master: near-silent content raised at most 20 dB, with a warning.
    ff(tmp, "-f", "lavfi", "-i", f"color=black:s=160x120:r={FPS}:d=5", "-f", "lavfi",
       "-i", "sine=f=1000:d=5:r=48000,volume=-40dB", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "pcm_s16le",
       "-shortest", "quiet.mkv")
    before = audio.measure(tmp / "quiet.mkv")["I"]
    after = audio.master(tmp / "quiet.mkv", tmp / "quiet.mp4", "social", Log(tmp / "m.log"))["I"]
    checks[f"master caps the gain on near-silence ({before} → {after} LUFS) and warns"] = (
        before is not None and after is not None and after - before < 21
        and "nearly silent" in (tmp / "m.log").read_text(encoding="utf-8"))

    # ---- the upscale signature sees every picture effect.
    e0 = {"output": out, "segments": []}
    s0 = {"source": "a.mp4", "name": "a.mp4", "in": 1.0, "out": 2.0}
    base = signature(s0, {}, e0, {})
    variants = {"redact": {**s0, "redact": [{"box": [0, 0, 0.1, 0.1]}]}, "stabilize": {**s0, "stabilize": True},
                "rotate": {**s0, "rotate": 90}, "fit": {**s0, "fit": "blur"}}
    diff = [k for k, s in variants.items() if signature(s, {}, e0, {}) != base]
    diff += ["hdr"] * (signature(s0, {}, e0, {"a.mp4": {"hdr": True}}) != base)
    diff += ["zooms"] * (signature(s0, {}, {**e0, "screen_zooms": [{"name": "a.mp4", "from": 1.2, "to": 1.5,
                                                                      "box": [0, 0, 0.5, 0.5]}]}, {}) != base)
    checks[f"upscale signature changes with {diff}"] = len(diff) == 6

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(tmp.parent, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
