# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Delivery tests: versions, names, copy checks, upload size, cut edges on the audio,
and the listening check.

    uv run tests/test_delivery.py

1. Cut edges: when Whisper puts a gap in the wrong place, the cut goes to the silence
   the audio shows (a fragment of a dropped word isn't left at the cut).
2. render.py: each master is a new <name>_v<N>.mp4; --overwrite replaces the latest;
   the .srt is <name>.srt, one per project; --upload-height scales up, same framing.
3. qa.py: picks the latest version; copy.txt checked per platform (missing section,
   long title, hashtags); YouTube under 1440p warned.
4. Listening: the export transcribed again; a word cut out of the picture but still in
   the edit's word list is reported as missing (needs faster-whisper; slow on CPU).
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
FIX = ROOT / "tests" / "fixtures"
sys.path.insert(0, str(SCRIPTS))
import cutlist  # noqa: E402

FF = shutil.which("ffmpeg")


def run(cwd: Path, *a: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["uv", "run", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", check=check)


def status(out: str, check: str) -> str:
    rows = [ln.split()[0] for ln in out.splitlines() if check in ln and ln.split()[0] in ("PASS", "WARN", "FAIL")]
    return "WARN" if "WARN" in rows else (rows[0] if rows else "missing")


def main() -> None:
    checks = {}

    # 1. A word at 0.39–0.70, silence to 0.90, the next word from 0.92; Whisper says the first
    # ends at 0.60 and the second starts at 0.66.
    db = np.full(200, -60.0)
    db[39:70] = -28
    db[92:130] = -27
    v = cutlist.valley(db, 0.60, 0.66)
    checks[f"cut edges: the real silence is found ({v})"] = v is not None and abs(v[0] - 0.70) < 0.02 \
        and abs(v[1] - 0.92) < 0.02
    db2 = db.copy()
    db2[100:106] = -60                                        # a stop consonant inside the second word
    v2 = cutlist.valley(db2, 0.60, 0.66)
    checks["cut edges: a closure inside a word doesn't win"] = v2 is not None and abs(v2[1] - 0.92) < 0.02

    # Project from the retake fixture.
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-delivery-")) / "Mi Proyecto"
    ing = tmp / "01-ingest"
    ing.mkdir(parents=True)
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=31",
                    "-i", str(FIX / "retakes_es.wav"), "-shortest", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "talk.mp4"], cwd=tmp, check=True)
    tr = json.loads((FIX / "retakes_es.transcript.json").read_text(encoding="utf-8"))
    tr["files"] = {"talk.mp4": tr["files"]["retakes_es.wav"]}
    for s in tr["sentences"]:
        s["file"] = "talk.mp4"
    tr["lang"] = "es"
    (ing / "transcript.json").write_text(json.dumps(tr), encoding="utf-8")
    (ing / "material.json").write_text(json.dumps({"files": [
        {"name": "talk.mp4", "file": str(tmp / "talk.mp4"), "kind": "video", "duration": 31,
         "display_width": 1920, "display_height": 1080, "fps": 30}]}))
    run(tmp, str(SCRIPTS / "cutlist.py"), "--format", "horizontal")
    run(tmp, str(SCRIPTS / "subtitles.py"), "--no-burn")

    # 2. Versions and names.
    run(tmp, str(SCRIPTS / "render.py"), "--no-grade")
    run(tmp, str(SCRIPTS / "render.py"), "--no-grade")
    run(tmp, str(SCRIPTS / "render.py"), "--no-grade", "--overwrite")
    dl = tmp / "03-delivery"
    vids = sorted(p.name for p in dl.glob("*.mp4"))
    checks[f"each render is a new version, --overwrite replaces the latest ({vids})"] = \
        vids == ["mi-proyecto_v1.mp4", "mi-proyecto_v2.mp4"]
    shutil.copy(tmp / "02-work" / "edl.json", tmp / "02-work" / "edl_hookB.json")   # the variant's own cut
    run(tmp, str(SCRIPTS / "render.py"), "--no-grade", "--variant", "hookB")
    checks["a variant joins the latest version, no new one"] = (dl / "mi-proyecto_v2_hookB.mp4").exists() and         sorted(p.name for p in dl.glob("mi-proyecto_v*.mp4") if "_hookB" not in p.name) == vids
    (dl / "mi-proyecto_v2_hookB.mp4").unlink()
    checks["the .srt is one per project (<name>.srt)"] = sorted(p.name for p in dl.glob("*.srt")) == ["mi-proyecto.srt"]
    run(tmp, str(SCRIPTS / "render.py"), "--no-grade", "--upload-height", "1440", "--out", "up.mp4")
    p = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "json",
                                   str(tmp / "up.mp4")], capture_output=True, text=True).stdout)
    wh = next((s["width"], s["height"]) for s in p["streams"] if "width" in s)
    checks[f"--upload-height scales the master ({wh})"] = wh == (2560, 1440)

    # 3. QA picks the latest version; copy checks; YouTube resolution.
    (dl / "copy.txt").write_text(
        "## youtube\nTitle: " + "Un título larguísimo que pasa de largo los sesenta caracteres recomendados" +
        "\nDescripción.\n\n## reels\nUna línea que engancha.\n#uno #dos\n", encoding="utf-8")
    out = run(tmp, str(SCRIPTS / "qa.py"), "--platform", "youtube", "--platform", "reels", "--platform", "tiktok",
              "--no-listen", check=False).stdout
    checks["qa: checks the latest version"] = "QA: mi-proyecto_v2.mp4" in (dl / "qa" / "qa.md").read_text(
        encoding="utf-8")
    checks["qa: .srt found by project name"] = status(out, "delivery: .srt") != "WARN"
    checks["qa: long YouTube title warned"] = status(out, "youtube: title length") == "WARN"
    checks["qa: too few hashtags warned"] = status(out, "reels: hashtags") == "WARN"
    checks["qa: missing platform section warned"] = status(out, "tiktok: copy") == "WARN"
    checks["qa: 1080p for YouTube warned (upload at 1440p+)"] = status(out, "youtube: upload resolution") == "WARN"
    checks["qa: upload reminders"] = "Upload at highest quality" in out and "HD upload" in out

    # 4. Listening: remove a word from the picture, keep it in the edit's list.
    out = run(tmp, str(SCRIPTS / "qa.py"), "--platform", "youtube", check=False).stdout
    checks[f"listening: a clean export has no differences ({status(out, 'listening')})"] = \
        status(out, "listening") == "PASS"
    edl_p = tmp / "02-work" / "edl.json"
    edl = json.loads(edl_p.read_text(encoding="utf-8"))
    words = sorted((w for f in tr["files"].values() for s in f["segments"] for w in s["words"]),
                   key=lambda w: w["start"])
    seg = edl["segments"][1]
    a, b = seg["words"]
    victim = next(w for w in words[a:b] if w["end"] - w["start"] >= 0.3)
    seg["in"] = round(victim["end"] + 0.03, 3)                # the picture starts after it
    edl_p.write_text(json.dumps(edl, ensure_ascii=False), encoding="utf-8")
    run(tmp, str(SCRIPTS / "render.py"), "--no-grade", "--overwrite")
    out = run(tmp, str(SCRIPTS / "qa.py"), "--platform", "youtube", check=False).stdout
    line = next((ln for ln in out.splitlines() if "listening" in ln and ("WARN" in ln or "PASS" in ln)), "")
    checks[f"listening: a lost word is reported («{victim['word'].strip()}»)"] = \
        line.startswith("WARN") and "missing" in line

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(tmp.parent, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
