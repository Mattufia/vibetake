# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Who speaks, and framing that follows them (speaker.py + reframe.py), on synthetic video.

    uv run tests/test_speaker.py

Two "people" far apart in a 16:9 shot edited to 9:16 (they can't both fit the crop). Each
sentence is said by one of them: their mouth area moves while they talk. Each also has
their own mic (two audio tracks).

1. People are found and numbered from left to right.
2. Mouth movement decides who says each sentence.
3. Which mic is whose is learned (track 1 → person 1, track 2 → person 2).
4. Where both mouths move (crosstalk on screen), the mics decide.
5. The vertical framing follows the speaker; it changes where a sentence starts.
6. An override (--set) wins, and going back to auto restores the automatic choice.
7. Two people close together fit the crop: the framing keeps the group.
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
FF = shutil.which("ffmpeg")
W, H, FPS, D, SR = 640, 360, 25, 12.0, 48000
P1, P2 = 0.22, 0.78                                  # face centers (x)
FACE = (0.10, 0.22)                                  # face box w, h
# (start, end, who): L3 is crosstalk on screen — both mouths move, only person 1's mic is loud.
SENTENCES = [(0.5, 3.0, 1), (3.6, 6.4, 2), (7.0, 9.6, 1), (10.0, 11.6, 2)]
BOTH = {3}


def mouth_box(cx):
    x0 = int((cx - FACE[0] / 4) * W)
    y0 = int((0.40 + FACE[1] * 0.1) * H)
    return x0, y0, int(FACE[0] / 2 * W), int(FACE[1] * 0.35 * H)


def build(tmp: Path) -> None:
    rng = np.random.default_rng(3)
    frames = []
    for i in range(int(D * FPS)):
        t = i / FPS
        img = np.full((H, W), 60, np.uint8)
        for cx in (P1, P2):
            x0, y0 = int((cx - FACE[0] / 2) * W), int((0.40 - FACE[1] / 2) * H)
            img[y0:y0 + int(FACE[1] * H), x0:x0 + int(FACE[0] * W)] = 170
        for k, (a, b, who) in enumerate(SENTENCES, 1):
            if a <= t <= b:
                for p, cx in ((1, P1), (2, P2)):
                    if p == who or k in BOTH:
                        x0, y0, w, h = mouth_box(cx)
                        img[y0:y0 + h, x0:x0 + w] = rng.integers(40, 250, (h, w), dtype=np.uint8)
        frames.append(np.repeat(img[:, :, None], 3, 2))
    raw = b"".join(f.tobytes() for f in frames)
    tracks = []
    for p in (1, 2):
        x = np.zeros(int(D * SR), np.float32)
        for a, b, who in SENTENCES:
            if who == p:
                n = int((b - a) * SR)
                x[int(a * SR):int(a * SR) + n] = rng.standard_normal(n).astype(np.float32) * 0.2
        x += rng.standard_normal(len(x)).astype(np.float32) * 0.002
        path = tmp / f"mic{p}.wav"
        subprocess.run([FF, "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "1", "-i", "-", str(path)],
                       input=x.tobytes(), check=True)
        tracks.append(path)
    subprocess.run([FF, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS),
                    "-i", "-", "-i", str(tracks[0]), "-i", str(tracks[1]), "-map", "0:v", "-map", "1:a", "-map", "2:a",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(tmp / "talk.mp4")], input=raw, check=True)


def project(tmp: Path, p1: float, p2: float) -> None:
    ing = tmp / "01-ingest"
    ing.mkdir(exist_ok=True)
    samples = [{"t": round(i * 0.5, 2), "faces": [[p - FACE[0] / 2, 0.40 - FACE[1] / 2, FACE[0], FACE[1], 0.9]
                                                   for p in (p1, p2)]} for i in range(int(D * 2))]
    (ing / "faces.json").write_text(json.dumps({"fps": 2, "files": {"talk.mp4": samples}}))
    (ing / "material.json").write_text(json.dumps({"files": [{
        "name": "talk.mp4", "file": "talk.mp4", "kind": "video", "duration": D, "display_width": W,
        "display_height": H, "audio": [{"index": 0, "integrated_lufs": -20}, {"index": 1, "integrated_lufs": -20}]}]}))
    (ing / "transcript.json").write_text(json.dumps({"lang": "es", "files": {}, "sentences": [
        {"file": "talk.mp4", "line": k, "start": a, "end": b} for k, (a, b, _) in enumerate(SENTENCES, 1)]}))
    (tmp / "02-work").mkdir(exist_ok=True)
    (tmp / "02-work" / "edl.json").write_text(json.dumps({"version": 1, "output": {"width": 1080, "height": 1920,
                                                                                   "fps": FPS},
                                                          "segments": [{"source": "talk.mp4", "name": "talk.mp4",
                                                                        "in": 0.0, "out": D}]}))


def run(tmp: Path, script: str, *a: str) -> str:
    p = subprocess.run(["uv", "run", str(SCRIPTS / script), *a], cwd=tmp, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if p.returncode:
        print(p.stdout[-2000:], p.stderr[-2000:])
    return p.stdout


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-speaker-"))
    build(tmp)
    project(tmp, P1, P2)
    checks = {}
    run(tmp, "speaker.py")
    sp = json.loads((tmp / "02-work" / "speakers.json").read_text(encoding="utf-8"))["talk.mp4"]
    got = [s["person"] for s in sp["sentences"]]
    want = [who for _, _, who in SENTENCES]
    checks[f"two people found ({sp['people']})"] = sp["people"] == 2
    checks[f"each sentence → its speaker ({got} vs {want})"] = got == want
    checks[f"mics mapped to people ({sp['mics']})"] = sp["mics"] == {"track 1": 1, "track 2": 2}
    l3 = sp["sentences"][2]
    checks[f"both mouths move in L3 → the mic decides ({l3['how']})"] = l3["person"] == 1 and "track 1" in l3["how"]
    checks["people sheet drawn"] = (tmp / "02-work" / "people_talk.jpg").exists()

    run(tmp, "reframe.py", "--punch", "off")
    keys = json.loads((tmp / "02-work" / "edl.json").read_text(encoding="utf-8"))["segments"][0]["frame"]["keys"]
    at = lambda t: min(keys, key=lambda k: abs(k[0] - t))[1]  # noqa: E731
    xs = [at(t) for t in (2.0, 5.0, 8.5, 11.0)]
    checks[f"vertical framing follows the speaker ({[round(x, 2) for x in xs]})"] = (
        abs(xs[0] - P1) < 0.08 and abs(xs[1] - P2) < 0.08 and abs(xs[2] - P1) < 0.08 and abs(xs[3] - P2) < 0.08)
    checks["it moves to the next speaker where their sentence starts"] = abs(at(3.3) - P1) < 0.08 and \
        abs(at(3.9) - P2) < 0.08

    run(tmp, "speaker.py", "--set", "L2=1")
    sp2 = json.loads((tmp / "02-work" / "speakers.json").read_text(encoding="utf-8"))["talk.mp4"]["sentences"]
    run(tmp, "speaker.py", "--set", "L2=auto")
    sp3 = json.loads((tmp / "02-work" / "speakers.json").read_text(encoding="utf-8"))["talk.mp4"]["sentences"]
    checks["override wins; auto restores"] = sp2[1]["person"] == 1 and sp2[1]["how"] == "override" \
        and sp3[1]["person"] == 2

    # One shared mic: the mouths alone decide (L3, both moving, stays unsure — no guessing).
    mat = json.loads((tmp / "01-ingest" / "material.json").read_text())
    mat["files"][0]["audio"] = [{"index": 0, "integrated_lufs": -20}]
    (tmp / "01-ingest" / "material.json").write_text(json.dumps(mat))
    (tmp / "02-work" / "speakers.json").unlink()
    run(tmp, "speaker.py")
    one = json.loads((tmp / "02-work" / "speakers.json").read_text(encoding="utf-8"))["talk.mp4"]["sentences"]
    checks[f"one shared mic: mouths decide, crosstalk stays unsure ({[s['person'] for s in one]})"] = (
        [s["person"] for s in one] == [1, 2, None, 2] and all(s["how"] == "mouth" for k, s in enumerate(one) if k != 2))

    # Close together: both fit a vertical crop → the group, not one of them.
    from reframe import who_track
    import reframe
    samples = json.loads((tmp / "01-ingest" / "faces.json").read_text())["files"]["talk.mp4"]
    close = [{"t": s["t"], "faces": [[0.42, f[1], 0.06, f[3], f[4]] if k == 0 else [0.52, f[1], 0.06, f[3], f[4]]
                                     for k, f in enumerate(s["faces"])]} for s in samples]
    cw = (H * 1080 / 1920) / W
    spk = {"talk.mp4": {"sentences": sp["sentences"]}}
    t, x, y, h, grp, cur = who_track("talk.mp4", close, D, spk, cw)
    checks[f"close together, both fit the crop → the group ({grp.mean():.0%} of the time)"] = (
        grp.mean() > 0.9 and abs(np.nanmedian(x) - 0.48) < 0.03)
    _ = reframe

    checks = {k: bool(v) for k, v in checks.items()}
    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print(f"{sum(checks.values())}/{len(checks)} pass · {tmp}")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
