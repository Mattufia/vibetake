# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""Take picture tests: two takes of the same line are compared — softer, shakier, blown out —
and a look both takes share (soft on purpose) says nothing.

    uv run tests/test_take_quality.py

One synthetic 20 s clip, five pairs of takes of the same line (2 s each):
  0–4   sharp → blurred          the later take is softer
  4–8   shaky → steady           this (earlier) take is shakier
  8–12  blurred → blurred        the same look in both: no note
  12–16 normal → blown out       the later take is more blown out
  16–20 smooth pan → steady      a camera move on purpose isn't shake: no note
ingest.py measures the picture; a hand-made transcript.json stands in for Whisper.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
LINES = ["Hoy les voy a mostrar el error.", "Ahora vamos a ver la segunda parte.",
         "Esto es una prueba del desenfoque.", "Al final miramos la luz del cuarto.",
         "Y cerramos con un paneo bien suave."]


def transcript(name: str) -> dict:
    segs = []
    for k in range(10):
        t0 = 2 * k + 0.3
        toks = LINES[k // 2].split()
        step = 1.4 / len(toks)
        words = [{"word": " " + w, "start": round(t0 + i * step, 3), "end": round(t0 + (i + 1) * step - 0.02, 3),
                  "probability": 0.95} for i, w in enumerate(toks)]
        segs.append({"start": words[0]["start"], "end": words[-1]["end"], "avg_logprob": -0.2,
                     "compression_ratio": 1.2, "no_speech_prob": 0.01, "words": words})
    return {"files": {name: {"segments": segs, "duration": 20.0}}, "order": [name], "lang": "es"}


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-takes-"))
    raw = tmp / "00-raw"
    raw.mkdir()
    jitter = "if(between(t,4,6),40+30*sin(n*n*0.37),if(between(t,16,18),(t-16)*40,40))"
    vf = (f"crop=640:360:'{jitter}':'30+20*sin(n*n*0.53)*between(t,4,6)',"
          "gblur=sigma=3:enable='between(t,2,4)+between(t,8,12)',"
          "eq=brightness=0.45:enable='between(t,14,16)'")
    subprocess.run([shutil.which("ffmpeg"), "-hide_banner", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=s=720x420:r=30:d=20", "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", str(raw / "takes.mp4")], check=True)
    subprocess.run(["uv", "run", str(SCRIPTS / "ingest.py"), "--no-transcribe"], cwd=tmp, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ing = tmp / "01-ingest"
    faces = json.loads((ing / "faces.json").read_text(encoding="utf-8"))
    checks = {"ingest stores the picture signals per sample": all("q" in x for x in faces["files"]["takes.mp4"])}

    (ing / "transcript.json").write_text(json.dumps(transcript("takes.mp4")), encoding="utf-8")
    p = subprocess.run(["uv", "run", str(SCRIPTS / "transcribe.py"), "--rebuild-md", "--lang", "es"], cwd=tmp,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    md = (ing / "transcript.md").read_text(encoding="utf-8")
    lines = {ln.split()[0]: ln for ln in md.splitlines() if ln.startswith("L")}
    print(md)
    if p.returncode:
        print(p.stdout + p.stderr)
    checks["sharp → blurred: the later take is softer"] = "(L2 softer" in lines.get("L1", "")
    checks["shaky → steady: this take is shakier"] = lines.get("L3", "").endswith("→ L4 (this take shakier)")
    checks["blurred in both (the look): no note"] = lines.get("L5", "").rstrip().endswith("→ L6")
    checks["normal → blown: the later take is more blown out"] = "L8 more blown out" in lines.get("L7", "")
    checks["smooth pan → steady: no note"] = lines.get("L9", "").rstrip().endswith("→ L10")
    checks["the kept takes carry no note"] = all("↻" not in lines.get(f"L{n}", "") for n in (2, 4, 6, 8, 10))

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
