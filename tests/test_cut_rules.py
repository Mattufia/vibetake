# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Editorial rules for cutting: reactions vs fillers, silent stretches, natural pace,
protected ranges.

    uv run tests/test_cut_rules.py

1. "Ah" as hesitation is a filler; "Ah" answering a question after a pause, "¡Ah!",
   or an "Ah" on its own are content.
2. Stretches without speech become S1, S2… in transcript.md / transcript.json.
3. --pace natural keeps a silent stretch between kept lines whole; normal shortens it
   and says so; --keep can name it.
4. --protect keeps a range whole (even a dropped filler or a retake inside it), every
   rebuild honors it and reuses the last settings; --unprotect removes it.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import transcribe  # noqa: E402

FF = shutil.which("ffmpeg")


def W(word: str, a: float, b: float) -> dict:
    return {"word": " " + word, "start": a, "end": b}


def run(cwd: Path, *a: str) -> str:
    p = subprocess.run(["uv", "run", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if p.returncode:
        raise SystemExit(f"{a[0]} failed:\n{p.stdout}\n{p.stderr}")
    return p.stdout


def main() -> None:
    checks = {}
    ld = transcribe.lang_data("es")

    # 1. Reactions vs fillers.
    question = W("haciendo?", 7.3, 7.6)
    answer = [W("Ah,", 8.5, 8.55), W("me", 8.6, 8.7), W("agarraste", 8.7, 9.0)]
    checks["'Ah' answering a question after a pause → content"] = transcribe.mark_fillers(answer, ld, question) == []
    hesit = [W("bueno", 1.0, 1.2), W("ah,", 1.25, 1.3), W("la", 1.6, 1.7), W("idea", 1.7, 2.0)]
    checks["'ah' mid-phrase → filler"] = (1, 2) in transcribe.mark_fillers(hesit, ld, None)
    statement = W("algo.", 3.0, 3.3)
    checks["'Ah' after a statement (not a question) → still a filler"] = \
        transcribe.mark_fillers(answer, ld, statement) == [(0, 1)]
    checks["'¡Ah!' → content"] = transcribe.mark_fillers([W("¡Ah!", 2.0, 2.2), W("sí", 2.3, 2.5)], ld, None) == []
    checks["a lone 'Eh.' → still a filler"] = transcribe.mark_fillers([W("Eh.", 2.0, 2.2)], ld, statement) == [(0, 1)]
    checks["'eh' hesitating → still a filler"] = transcribe.mark_fillers(
        [W("Eh,", 0.5, 0.6), W("hoy", 0.7, 0.9), W("les", 0.9, 1.0)], ld, statement) == [(0, 1)]

    # Project: a talk with a 3.6 s silent stretch (the walk) between two lines.
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-rules-"))
    ing = tmp / "01-ingest"
    ing.mkdir()
    # Speech-like tone where the words are, silence elsewhere.
    spans = [(0.8, 3.4), (7.0, 7.6), (8.5, 9.3), (9.5, 12.0)]
    expr = "+".join(f"between(t,{a},{b})" for a, b in spans)
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "color=gray:s=320x180:r=30:d=13",
                    "-f", "lavfi", "-i", f"aevalsrc='0.3*sin(2*PI*200*t)*({expr})':s=48000:d=13",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "scene.mp4"], cwd=tmp, check=True)

    def words(a: float, b: float, text: str) -> list[dict]:
        toks = text.split()
        step = (b - a) / len(toks)
        return [{"word": " " + t, "start": round(a + i * step, 3), "end": round(a + (i + 1) * step - 0.02, 3),
                 "probability": 1.0} for i, t in enumerate(toks)]
    segs = [words(0.8, 3.4, "A ver qué está haciendo este."), words(7.0, 7.6, "¿Qué haces?"),
            words(8.5, 9.3, "Ah, me agarraste,"), words(9.5, 12.0, "estoy arreglando la compu.")]
    tr = {"files": {"scene.mp4": {"duration": 13.0, "track": 0, "segments": [
        {"start": s[0]["start"], "end": s[-1]["end"], "words": s, "avg_logprob": -0.1, "compression_ratio": 1.2,
         "no_speech_prob": 0.0} for s in segs]}}, "order": ["scene.mp4"], "lang": "es"}
    (ing / "transcript.json").write_text(json.dumps(tr, ensure_ascii=False), encoding="utf-8")
    (ing / "material.json").write_text(json.dumps({"files": [
        {"name": "scene.mp4", "file": str(tmp / "scene.mp4"), "kind": "video", "duration": 13,
         "display_width": 320, "display_height": 180, "fps": 30}]}))
    run(tmp, str(SCRIPTS / "transcribe.py"), "--rebuild-md", "--lang", "es")

    # 2. Silent stretches listed.
    t2 = json.loads((ing / "transcript.json").read_text(encoding="utf-8"))
    md = (ing / "transcript.md").read_text(encoding="utf-8")
    s1 = next((x for x in t2.get("silences", []) if abs(x["start"] - 3.38) < 0.1), None)
    checks["the walk becomes S… in transcript.json and .md"] = s1 is not None and f"{s1['id']} [" in md
    checks["the reaction isn't marked as a filler"] = "⟨Ah,⟩" not in md

    def segs_of() -> list[dict]:
        return json.loads((tmp / "02-work" / "edl.json").read_text(encoding="utf-8"))["segments"]

    def covered(a: float, b: float) -> bool:
        return any(s["in"] <= a + 0.05 and s["out"] >= b - 0.05 for s in segs_of())

    # 3. Pace.
    out = run(tmp, str(SCRIPTS / "cutlist.py"), "--pace", "normal", "--format", "vertical")
    checks["normal pace shortens the walk and says so"] = not covered(3.6, 6.8) and "shortened" in out
    run(tmp, str(SCRIPTS / "cutlist.py"), "--pace", "natural")
    checks["natural pace keeps the walk whole"] = covered(3.4, 7.0)
    checks["natural pace keeps the pause before the reaction"] = covered(7.6, 8.5)
    last = max(x["line"] for x in t2["sentences"])
    run(tmp, str(SCRIPTS / "cutlist.py"), "--pace", "tight", "--keep", f"L1,{s1['id']},L2-L{last}")
    checks["--keep names a silent stretch"] = covered(3.4, 7.0)

    # 4. Protection.
    run(tmp, str(SCRIPTS / "cutlist.py"), "--keep", "auto", "--pace", "tight", "--protect", s1["id"],
        "--why", "suspense walk")
    checks["protected walk kept with tight pace"] = covered(3.4, 7.0)
    out = run(tmp, str(SCRIPTS / "cutlist.py"))
    edl = json.loads((tmp / "02-work" / "edl.json").read_text(encoding="utf-8"))
    checks["a rebuild reuses the settings (tight, vertical) and keeps the protection"] = (
        covered(3.4, 7.0) and edl["params"]["pace"] == "tight" and edl["output"]["width"] == 1080)
    prot = json.loads((tmp / "02-work" / "protected.json").read_text(encoding="utf-8"))["ranges"]
    checks["the reason is stored"] = prot and prot[0]["why"] == "suspense walk"
    t_sub = run(tmp, str(SCRIPTS / "subtitles.py"), "--no-burn")
    checks["subtitles still work with a no-speech segment"] = "captions" in t_sub
    run(tmp, str(SCRIPTS / "cutlist.py"), "--unprotect", "all")
    checks["--unprotect removes it"] = not covered(3.6, 6.8)

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
