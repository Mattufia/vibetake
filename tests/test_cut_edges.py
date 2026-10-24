# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Edge cases of the cut: the order chosen in --keep, protections, variants, shorts.

    uv run tests/test_cut_edges.py

1. A hook kept in its place stays twice (shown first, and again in its place), with and
   without a protection touching it.
2. A silent stretch named in --keep plays where it's listed.
3. Effects on the in-place copy of a hook stay on it through a rebuild.
4. A renumbered transcript keeps a hook's in-place duplicate.
5. Montage shots never lend their mute to spoken segments (a short, a rebuild).
6. Protections reach a variant only next to its own lines.
7. A new variant starts from the main edit's settings; a new --format replaces a saved --size.
8. shorts.py: a hook equal to the first line isn't played twice, a hook outside the range is
   flagged, a range of only retakes is a clear error, versions sort by number.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import cutlist  # noqa: E402
import shorts  # noqa: E402

FF = shutil.which("ffmpeg")
# Four lines back to back (one take), a 4 s walk (S1), two more lines.
LINES = [(0.5, 2.0, "Hola a todos ustedes."), (2.25, 3.75, "Hoy les cuento algo."), (4.0, 5.5, "Es muy simple."),
         (5.75, 7.25, "Vale la pena."), (11.5, 13.0, "Y eso es todo."), (13.25, 14.75, "Nos vemos pronto.")]


def run(cwd: Path, *a: str, ok: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(["uv", "run", *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if ok and p.returncode:
        raise SystemExit(f"{a[0]} failed:\n{p.stdout}\n{p.stderr}")
    return p


def main() -> None:
    checks = {}
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-edges-"))
    ing = tmp / "01-ingest"
    ing.mkdir()
    (tmp / "02-work").mkdir()
    expr = "+".join(f"between(t,{a},{b})" for a, b, _ in LINES)
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", "color=gray:s=320x180:r=30:d=16",
                    "-f", "lavfi", "-i", f"aevalsrc='0.3*sin(2*PI*200*t)*({expr})':s=48000:d=16",
                    "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "talk.mp4"], cwd=tmp, check=True)

    def words(a: float, b: float, text: str) -> list[dict]:
        toks = text.split()
        step = (b - a) / len(toks)
        return [{"word": " " + t, "start": round(a + i * step, 3), "end": round(a + (i + 1) * step - 0.02, 3),
                 "probability": 1.0} for i, t in enumerate(toks)]
    segs = [words(*x) for x in LINES]
    tr = {"files": {"talk.mp4": {"duration": 16.0, "track": 0, "segments": [
        {"start": s[0]["start"], "end": s[-1]["end"], "words": s, "avg_logprob": -0.1, "compression_ratio": 1.2,
         "no_speech_prob": 0.0} for s in segs]}}, "order": ["talk.mp4"], "lang": "es"}
    (ing / "transcript.json").write_text(json.dumps(tr, ensure_ascii=False), encoding="utf-8")
    (ing / "material.json").write_text(json.dumps({"files": [
        {"name": "talk.mp4", "file": str(tmp / "talk.mp4"), "kind": "video", "duration": 16,
         "display_width": 320, "display_height": 180, "fps": 30}]}))
    run(tmp, str(SCRIPTS / "transcribe.py"), "--rebuild-md", "--lang", "es")
    t2 = json.loads((ing / "transcript.json").read_text(encoding="utf-8"))
    sents = {s["line"]: s for s in t2["sentences"]}
    s1 = next((x for x in t2.get("silences", []) if 7.0 < x["start"] < 8.0), None)
    if len(sents) != 6 or not s1:
        raise SystemExit(f"fixture: expected 6 lines and the walk, got {len(sents)} lines, silences "
                         f"{t2.get('silences')}")
    S1 = s1["id"]

    def edl(name: str = "edl") -> dict:
        return json.loads((tmp / "02-work" / f"{name}.json").read_text(encoding="utf-8"))

    def cut(*a: str) -> str:
        return run(tmp, str(SCRIPTS / "cutlist.py"), *a).stdout

    def covers(sg: dict, n: int) -> bool:
        s = sents[n]
        return sg["in"] <= s["start"] + 0.05 and sg["out"] >= s["end"] - 0.05

    # 1. Hook kept in its place.
    cut("--keep", "L4,L3-L4", "--pace", "normal", "--format", "source")
    sg = edl()["segments"]
    checks[f"hook L4 then L3-L4: two segments, the hook first and alone ({[s['lines'] for s in sg]})"] = (
        len(sg) == 2 and sg[0].get("role") == "hook" and not covers(sg[0], 3) and covers(sg[0], 4)
        and covers(sg[1], 3) and covers(sg[1], 4))
    cut("--keep", "L3,L1-L4", "--protect", "L3")
    sg = edl()["segments"]
    checks[f"--protect on a hook line keeps the hook and its place apart ({[s['lines'] for s in sg]})"] = (
        len(sg) == 2 and sg[0].get("role") == "hook" and covers(sg[0], 3) and not covers(sg[0], 1)
        and covers(sg[1], 1) and covers(sg[1], 4))
    cut("--unprotect", "all")

    # 2. A named silence plays where it's listed.
    cut("--keep", f"L5,{S1},L1")
    sg = edl()["segments"]
    checks[f"--keep L5,{S1},L1: L5, then the walk, then L1 ({[(s['in'], s['out']) for s in sg]})"] = (
        len(sg) == 3 and covers(sg[0], 5) and sg[0]["in"] > s1["end"] - 0.3
        and sg[1]["in"] <= s1["start"] + 0.05 and sg[1]["out"] >= s1["end"] - 0.05 and covers(sg[2], 1))
    cut("--keep", f"L1-L4,{S1},L5-L6")
    sg = edl()["segments"]
    checks["a silence listed between its neighbors joins them (one segment)"] = len(sg) == 1

    # 3. Effects on the in-place copy of a hook stay on it.
    cut("--keep", "L5,L4,L5")
    e = edl()
    if len(e["segments"]) == 3:
        e["segments"][2]["fade_out"] = 0.5
        (tmp / "02-work" / "edl.json").write_text(json.dumps(e), encoding="utf-8")
    cut()
    sg = edl()["segments"]
    checks[f"a fade on the in-place copy stays there ({[s.get('fade_out') for s in sg]})"] = (
        len(sg) == 3 and sg[2].get("fade_out") == 0.5 and "fade_out" not in sg[0])

    # 4. Renumbered transcript: the hook's duplicate stays; two old lines now one count once.
    span = lambda n: {"id": f"L{n}", "file": "talk.mp4", "start": sents[n]["start"], "end": sents[n]["end"]}  # noqa: E731
    checks["remap keeps a hook's in-place duplicate"] = cutlist.remap_keep(
        [span(4), span(3), span(4)], t2) == "L4,L3,L4"
    halves = [{"id": "L7", "file": "talk.mp4", "start": 0.5, "end": 1.2},
              {"id": "L8", "file": "talk.mp4", "start": 1.2, "end": 2.0}]
    checks["remap: two old lines now one line → once"] = cutlist.remap_keep(halves, t2) == "L1"

    # 5. Montage shots never lend their mute to spoken segments.
    e = edl()
    shot = {"source": str(tmp / "talk.mp4"), "name": "talk.mp4", "in": 0.4, "out": 7.0, "role": "montage",
            "audio": "mute"}
    e["segments"] = [dict(shot)] + e["segments"]
    e["montage"] = {"where": "start", "segments": [dict(shot)]}
    (tmp / "02-work" / "edl.json").write_text(json.dumps(e), encoding="utf-8")
    cut("--keep", "L1-L4")
    sg = edl()["segments"]
    spoken = [s for s in sg if s.get("role") != "montage"]
    checks["a rebuild after a montage: the block is back, spoken segments not muted"] = (
        sg[0].get("role") == "montage" and spoken and not any(s.get("audio") == "mute" for s in spoken))
    (tmp / "02-work" / "shorts.json").write_text(json.dumps({"shorts": [{"name": "uno", "keep": "L1-L4",
                                                                          "lines": [1, 2, 3, 4]}]}))
    cut("--variant", "uno", "--keep", "L1-L4")
    sg = edl("edl_uno")["segments"]
    checks["a short: no montage shots and not muted"] = sg and not any(
        s.get("role") == "montage" or s.get("audio") == "mute" for s in sg)

    # 6. Protections reach a variant only next to its own lines.
    (tmp / "02-work" / "edl.json").unlink()
    cut("--keep", "auto", "--protect", S1, "--why", "the walk")
    cut("--variant", "precio", "--keep", "L1")
    sg = edl("edl_precio")["segments"]
    checks[f"protected walk not added to a variant that doesn't touch it ({[(s['in'], s['out']) for s in sg]})"] = (
        all(s["out"] < s1["start"] for s in sg))
    cut("--variant", "cola", "--keep", "L5")
    sg = edl("edl_cola")["segments"]
    checks["…and kept in one that does (the line right after it)"] = any(
        s["in"] <= s1["start"] + 0.05 and s["out"] >= s1["end"] - 0.05 for s in sg)
    cut("--unprotect", "all")

    # 7. A new variant starts from the main edit's settings.
    cut("--keep", "L1-L4", "--format", "vertical", "--pace", "tight")
    cut("--variant", "hookB", "--keep", "L4,L1-L4")
    v = edl("edl_hookB")
    checks[f"a new variant inherits format and pace ({v['output']['width']}×{v['output']['height']}, "
           f"{v['params']['pace']})"] = (v["output"]["width"], v["output"]["height"]) == (1080, 1920) \
        and v["params"]["pace"] == "tight"
    cut("--variant", "4x5", "--size", "1080x1350")
    v = edl("edl_4x5")
    lines = sorted({n for s in v["segments"] for n in s["lines"]})
    checks[f"a new variant without --keep keeps the main edit's lines ({lines})"] = lines == [1, 2, 3, 4] \
        and (v["output"]["width"], v["output"]["height"]) == (1080, 1350)
    cut("--size", "1080x1350")
    cut("--format", "square")
    checks["--format after a saved --size wins"] = (edl()["output"]["width"], edl()["output"]["height"]) == (1080, 1080)

    # 8. shorts.py.
    cwd = os.getcwd()
    os.chdir(tmp)
    try:
        sh = {"name": "a", "lines": [1, 2, 3, 4], "hook": 1}
        _, warn = shorts.checks(sh, t2, sents, [])
        checks["a hook that's already the first line is flagged"] = any("already the first line" in w for w in warn)
        _, warn = shorts.checks({"name": "b", "lines": [1, 2], "hook": 5}, t2, sents, [])
        checks["a hook outside the range is flagged"] = any("isn't in the short's lines" in w for w in warn)
        (tmp / "02-work" / "shorts.json").write_text(json.dumps({"shorts": [
            {"name": "a", "keep": "L1-L4", "lines": [1, 2, 3, 4], "hook": 1}]}))
        calls = []
        real_run = shorts.run
        shorts.run = lambda script, *a: calls.append((script, a))
        try:
            shorts.cmd_make(argparse.Namespace(only="a", format="vertical", pace="tight", preview=True))
        finally:
            shorts.run = real_run
        keep = next((a[a.index("--keep") + 1] for s, a in calls if s == "cutlist.py"), "")
        checks[f"make: a hook equal to the first line isn't played twice ({keep})"] = keep == "L1,L2,L3,L4"
        t3 = json.loads((ing / "transcript.json").read_text(encoding="utf-8"))
        for s in t3["sentences"]:
            if s["line"] in (5, 6):
                s["retake_of"] = 4
        (ing / "transcript.json").write_text(json.dumps(t3, ensure_ascii=False), encoding="utf-8")
        p = run(tmp, str(SCRIPTS / "shorts.py"), "add", "--name", "r", "--keep", "L5-L6", ok=False)
        checks["a range of only retakes: a clear error"] = p.returncode != 0 and "retake" in (p.stdout + p.stderr) \
            and "Traceback" not in p.stderr
        dl = tmp / "03-delivery"
        dl.mkdir(exist_ok=True)
        for v in (9, 10):
            (dl / f"charla_v{v}_a.mp4").write_bytes(b"")
        checks["made(): v10 is newer than v9"] = shorts.made("a")["video"] == "charla_v10_a.mp4"
    finally:
        os.chdir(cwd)

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    n = sum(bool(v) for v in checks.values())
    print(f"{n}/{len(checks)} pass · {tmp}")
    if n == len(checks):
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if n == len(checks) else 1)


if __name__ == "__main__":
    main()
