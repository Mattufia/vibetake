# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""Translated subtitles (translate.py): same timing, the target's line and reading-speed rules.

    uv run tests/test_translate.py
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
from translate import read_srt  # noqa: E402

SRT = """1
00:00:00,500 --> 00:00:02,400
Hoy les voy a mostrar

2
00:00:02,400 --> 00:00:04,800
el error que todos cometen.

3
00:00:05,000 --> 00:00:06,000
Y es simple.
"""


def uv(cwd: Path, *a: str) -> subprocess.CompletedProcess:
    return subprocess.run(["uv", "run", str(SCRIPTS / "translate.py"), *a], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def main() -> None:
    checks = {}
    proj = Path(tempfile.mkdtemp(prefix="vt-translate-"))
    (proj / "02-work").mkdir()
    (proj / "03-delivery").mkdir()
    (proj / "02-work" / "edl.json").write_text(json.dumps({"name": "charla", "output": {"fps": 30}, "segments": []}))
    (proj / "03-delivery" / "charla.srt").write_text(SRT, encoding="utf-8")

    r = uv(proj, "export", "--to", "en")
    work = proj / "02-work" / "translate_en.json"
    data = json.loads(work.read_text(encoding="utf-8")) if work.exists() else {"cues": []}
    checks["export: every cue with its time, and the whole sentence when it's half of one"] = (
        r.returncode == 0 and len(data["cues"]) == 3
        and data["cues"][0].get("sentence") == "Hoy les voy a mostrar el error que todos cometen."
        and "sentence" not in data["cues"][2])
    r = uv(proj, "apply", "--to", "en")
    checks["apply: refuses while cues are untranslated"] = r.returncode != 0 and "without a translation" in r.stdout + r.stderr

    data["cues"][0]["to"] = "Today I'll show you"
    data["cues"][1]["to"] = "the mistake everyone makes."
    data["cues"][2]["to"] = "And it's really very simple, honestly, much much simpler than anyone watching this would ever think it is."
    work.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    r = uv(proj, "apply", "--to", "en")
    out = proj / "03-delivery" / "charla.en.srt"
    got = read_srt(out) if out.exists() else []
    src = read_srt(proj / "03-delivery" / "charla.srt")
    checks["apply: <video>.en.srt with the same timing"] = (
        r.returncode == 0 and [(c["start"], c["end"]) for c in got] == [(c["start"], c["end"]) for c in src])
    checks["apply: a cue too fast to read and too long for two lines is flagged"] = (
        "cues [3] read faster" in r.stdout and "cues [3] don't fit" in r.stdout)

    # A re-export keeps what was translated; Japanese wraps without spaces at its own width.
    r = uv(proj, "export", "--to", "en")
    again = json.loads(work.read_text(encoding="utf-8"))
    checks["re-export: translations of unchanged cues are kept"] = again["cues"][0]["to"] == "Today I'll show you"
    uv(proj, "export", "--to", "ja")
    ja = json.loads((proj / "02-work" / "translate_ja.json").read_text(encoding="utf-8"))
    for c, t in zip(ja["cues"], ("今日はお見せします", "誰もがするミスを。", "簡単です。")):
        c["to"] = t
    (proj / "02-work" / "translate_ja.json").write_text(json.dumps(ja, ensure_ascii=False), encoding="utf-8")
    r = uv(proj, "apply", "--to", "ja")
    checks["japanese: written, no spaces added"] = r.returncode == 0 and " " not in "".join(
        c["text"] for c in read_srt(proj / "03-delivery" / "charla.ja.srt"))

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    n = sum(bool(v) for v in checks.values())
    print(f"{n}/{len(checks)} pass")
    shutil.rmtree(proj, ignore_errors=True)
    sys.exit(0 if n == len(checks) else 1)


if __name__ == "__main__":
    main()
