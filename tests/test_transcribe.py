# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""Retake, filler and word-timing tests for transcribe.py on a synthetic voice.

    uv run tests/test_transcribe.py [--model large-v3]

fixtures/retakes_es.wav (Windows TTS, Spanish) contains, in order: a truncated
start ("Hoy les voy a mos…" then the full sentence), a similar but different
sentence (must NOT be a retake), a correction ("…la forma de, no, perdón" then
the right one), fillers ("Bueno, eh") and a repeat with an added ending.
Needs the Whisper model (downloaded on first run); a GPU makes it take seconds.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
FIXTURE = ROOT / "tests" / "fixtures" / "retakes_es.wav"
SECOND_HOY_S = 2.18     # where the full sentence really starts (measured with silencedetect)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="large-v3")
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-tx-"))
    cmd = ["uv", "run"]
    if shutil.which("nvidia-smi"):
        cmd += ["--with", "nvidia-cublas-cu12", "--with", "nvidia-cudnn-cu12>=9,<10"]
    subprocess.run(cmd + [str(SCRIPTS / "transcribe.py"), str(FIXTURE), "--lang", "es", "--model", args.model,
                          "--out", str(tmp)], check=True)
    res = json.loads((tmp / "transcript.json").read_text(encoding="utf-8"))
    sents = res["sentences"]
    by_line = {s["line"]: s for s in sents}

    def find(fragment: str) -> dict | None:
        words_text = lambda s: " ".join(f[2] for f in s["fillers"])  # noqa: E731
        return next((s for s in sents if fragment in _text(res, s) and fragment not in words_text(s)), None)

    def retake_to(fragment: str, target: str) -> bool:
        s = find(fragment)
        return bool(s and s.get("retake_of") and target in _text(res, by_line[s["retake_of"]]))

    fillers = " ".join(f[2].lower() for s in sents for f in s["fillers"])
    full = find("mostrar el error")
    checks = {
        "truncated start → full sentence": retake_to("voy a mos", "mostrar el error"),
        "correction ('no, perdón') → right sentence": retake_to("forma de, no", "transcripción"),
        "repeat with added ending → longer take": retake_to("revisar el audio.", "antes de exportar"),
        "similar sentence is NOT a retake": not (find("primer paso") or {}).get("retake_of"),
        "final takes are not marked": all(not (find(t) or {"retake_of": 1}).get("retake_of")
                                          for t in ("mostrar el error", "sobre la transcripción", "antes de exportar")),
        "filler 'bueno' marked": "bueno" in fillers,
        "filler 'eh' marked": "eh" in fillers,
        "word start after a pause is real (±0.15 s)": bool(full) and abs(full["start"] - SECOND_HOY_S) < 0.15,
    }
    # Same audio split in two files right after "mos…": the retake spans the file boundary.
    split = tmp / "split"
    split.mkdir()
    ff = shutil.which("ffmpeg")
    subprocess.run([ff, "-v", "error", "-i", str(FIXTURE), "-t", "1.5", str(split / "take1.wav")], check=True)
    subprocess.run([ff, "-v", "error", "-ss", "1.5", "-i", str(FIXTURE), str(split / "take2.wav")], check=True)
    subprocess.run(cmd + [str(SCRIPTS / "transcribe.py"), str(split / "take1.wav"), str(split / "take2.wav"),
                          "--lang", "es", "--model", args.model, "--out", str(split / "out")], check=True,
                   stdout=subprocess.DEVNULL)
    res2 = json.loads((split / "out" / "transcript.json").read_text(encoding="utf-8"))
    first = res2["sentences"][0]
    target = next((s for s in res2["sentences"] if s["line"] == first.get("retake_of")), None)
    checks["retake across two files"] = bool(first["file"] == "take1.wav" and target
                                              and target["file"] == "take2.wav")

    # Kept transcripts: running again with the same settings transcribes nothing; redoing
    # one file keeps the recording order (line numbers don't move around).
    again = subprocess.run(cmd + [str(SCRIPTS / "transcribe.py"), str(split / "take1.wav"), str(split / "take2.wav"),
                                  "--lang", "es", "--model", args.model, "--out", str(split / "out")],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    checks["same settings again → nothing transcribed"] = (again.stdout.count("transcript up to date") == 2
                                                           and "loading" not in again.stdout)
    subprocess.run(cmd + [str(SCRIPTS / "transcribe.py"), str(split / "take2.wav"), "--force", "--lang", "es",
                          "--model", args.model, "--out", str(split / "out")], check=True, stdout=subprocess.DEVNULL)
    res3 = json.loads((split / "out" / "transcript.json").read_text(encoding="utf-8"))
    checks["one file redone → recording order kept"] = res3["order"] == ["take1.wav", "take2.wav"]

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print((tmp / "transcript.md").read_text(encoding="utf-8"))
    shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


def _text(res: dict, s: dict) -> str:
    seg_words = [w for seg in res["files"][s["file"]]["segments"] for w in seg["words"]]
    return "".join(w["word"] for w in seg_words if s["start"] <= w["start"] and w["end"] <= s["end"] + 0.01).strip()


if __name__ == "__main__":
    main()
