# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26", "opencv-python-headless>=4.9"]
# ///
"""Ingest edge cases from real card dumps: nothing in them may stop the whole ingest.

    uv run tests/test_ingest_edges.py [--keep]

A folder with: the same file name from two days (day1/C0001.MP4, day2/C0001.MP4), clip.mp4
and clip.mov (sheets of one mustn't delete the other's), a stereo track that's all silence,
a clip without audio, a truncated copy (moov atom not found), macOS leftovers (._C0001.MP4,
__MACOSX/), a DJI .LRV proxy beside its full-res file and a streamed WebM that stores no
duration. Then transcribe.py without Whisper (a clip without audio needs no model): names
from ingest, merge with what's on disk, prune, the lock; and the retake rule.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import ingest  # noqa: E402
import transcribe  # noqa: E402

FF = shutil.which("ffmpeg")
D = 4
H264 = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]


def ff(*a: str, **kw) -> None:
    subprocess.run([FF, "-hide_banner", "-v", "error", "-y", *a], check=True, **kw)


def clip(dst: Path, audio: str | None = "sine=f=440:d=4", color: str = "testsrc2") -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    a = ["-f", "lavfi", "-i", audio] if audio else []
    ff("-f", "lavfi", "-i", f"{color}=s=320x180:r=25:d={D}", *a, "-shortest", *H264,
       *(["-c:a", "aac"] if audio else []), str(dst))


def make(raw: Path) -> None:
    clip(raw / "day1" / "C0001.MP4")
    clip(raw / "day2" / "C0001.MP4", "sine=f=660:d=4")
    clip(raw / "clip.mp4")
    clip(raw / "clip.mov")
    clip(raw / "silent.mp4", "anullsrc=r=48000:cl=stereo:d=4")
    clip(raw / "drone.mp4", None)
    good = (raw / "clip.mp4").read_bytes()
    (raw / "truncated.mp4").write_bytes(good[:len(good) // 3])          # moov is at the end: lost
    (raw / "day1" / "._C0001.MP4").write_bytes(b"\x00\x05\x16\x07" + b"\x00" * 200)   # AppleDouble
    (raw / "__MACOSX").mkdir()
    (raw / "__MACOSX" / "clip.mp4").write_bytes(b"\x00\x05\x16\x07" + b"\x00" * 200)
    clip(raw / "dji" / "DJI_0001.MP4")
    shutil.copy(raw / "dji" / "DJI_0001.MP4", raw / "dji" / "DJI_0001.LRV")
    # A browser/stream recording: WebM written to a pipe stores no duration.
    with open(raw / "stream.webm", "wb") as f:
        ff("-f", "lavfi", "-i", f"testsrc2=s=320x180:r=25:d={D}", "-f", "lavfi", "-i", "sine=d=4",
           "-c:v", "libvpx", "-deadline", "realtime", "-b:v", "300k", "-c:a", "libopus", "-f", "webm", "pipe:1",
           stdout=f)


def run_tx(out: Path, *a: str, timeout: float | None = None) -> subprocess.CompletedProcess:
    env = {**os.environ, "VIBETAKE_CUDA_REEXEC": "1"}     # no CUDA wheels: no model is loaded here
    return subprocess.run(["uv", "run", str(SCRIPTS / "transcribe.py"), *a, "--lang", "es", "--out", str(out)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
                          timeout=timeout)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-edges-"))
    raw = tmp / "00-raw"
    make(raw)
    checks: dict[str, bool] = {}

    p = subprocess.run(["uv", "run", str(SCRIPTS / "ingest.py"), "00-raw", "--no-transcribe"], cwd=tmp,
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode:
        print(p.stdout[-3000:], p.stderr[-3000:])
    checks["ingest finishes despite all of it"] = p.returncode == 0
    ing = tmp / "01-ingest"
    res = json.loads((ing / "material.json").read_text(encoding="utf-8")) if p.returncode == 0 else {"files": []}
    by = {m["name"]: m for m in res["files"]}
    md = (ing / "material.md").read_text(encoding="utf-8") if p.returncode == 0 else ""
    names = sorted(by)
    print("names:", names)
    checks["same name in two folders → day1-C0001.MP4 / day2-C0001.MP4"] = \
        {"day1-C0001.MP4", "day2-C0001.MP4"} <= set(by) and "C0001.MP4" not in by
    checks["a unique name stays as is"] = "drone.mp4" in by and "silent.mp4" in by
    checks["names are unique"] = len(names) == len(res["files"])
    checks["stems are unique (case-insensitive)"] = len({m["stem"].lower() for m in res["files"]}) == len(names)
    checks["truncated file: skipped and listed in material.md"] = \
        "truncated.mp4" not in by and "truncated.mp4" in md and "unreadable" in md
    checks["macOS ._ files and __MACOSX/ ignored"] = \
        not any("._" in m["file"] or "__MACOSX" in m["file"] for m in res["files"]) and "._C0001" not in md
    checks["DJI .LRV proxy skipped beside its full-res file"] = \
        "DJI_0001.MP4" in by and not any(n.lower().endswith(".lrv") for n in by)
    sil = by.get("silent.mp4", {})
    checks["all-silent stereo track: no crash, 'silent track' warning"] = \
        any("silent track" in w for w in sil.get("warnings", [])) and sil.get("audio", [{}])[0].get(
            "channels", {}).get("layout") == "silent"
    checks["clip without audio: analyzed, 'no audio'"] = "no audio" in by.get("drone.mp4", {}).get("warnings", [])
    sheets = {n: by[n].get("shots", {}).get("sheets", []) for n in ("clip.mp4", "clip.mov") if n in by}
    checks["clip / clip_2: each keeps its own contact sheets"] = len(sheets) == 2 and all(
        s and all((ing / "contact" / x).exists() for x in s) for s in sheets.values()) and \
        not set(sheets["clip.mp4"]) & set(sheets["clip.mov"])
    web = by.get("stream.webm", {})
    checks[f"WebM without duration: measured ({web.get('duration')} s), few sheets, warned"] = \
        abs(web.get("duration", 0) - D) < 0.5 and 0 < len(web.get("shots", {}).get("sheets", [])) <= 4 and \
        any("length" in w for w in web.get("warnings", []))
    checks["faces.json keyed by the unique names"] = {"day1-C0001.MP4", "day2-C0001.MP4"} <= set(
        json.loads((ing / "faces.json").read_text(encoding="utf-8"))["files"]) if p.returncode == 0 else False

    # Units.
    lay = ingest.channel_layout(raw / "silent.mp4", 0, 2)
    checks["channel_layout: every channel silent → 'silent'"] = bool(lay) and lay["layout"] == "silent"
    sd = tmp / "sheets"
    sd.mkdir()
    for n in ("clip_grid_01.jpg", "clip_shots_01.jpg", "clip_2_grid_01.jpg", "clip_b_shots_01.jpg", "clip_x.jpg"):
        (sd / n).write_bytes(b"")
    checks["own_sheets: only clip_(grid|shots)_NN.jpg"] = sorted(p.name for p in ingest.own_sheets(sd, "clip")) \
        == ["clip_grid_01.jpg", "clip_shots_01.jpg"]
    gp = tmp / "gopro"
    gp.mkdir()
    for n in ("GX010001.MP4", "GL010001.LRV", "GL010002.LRV"):
        (gp / n).write_bytes(b"x")
    checks["GoPro GL….LRV skipped beside GX….MP4, kept alone"] = \
        bool(ingest.proxy_of(gp / "GL010001.LRV")) and not ingest.proxy_of(gp / "GL010002.LRV")
    checks["unique_names: plain when unique, folder-prefixed on collision"] = ingest.unique_names(
        [Path("a/x.mp4"), Path("b/y.mp4"), Path("c/y.mp4")]) == ["x.mp4", "b-y.mp4", "c-y.mp4"]

    # Ingest hands transcribe.py only files with sound, under their unique names.
    seen = {}

    class FakePopen:
        def __init__(self, cmd, **kw):
            seen["cmd"] = cmd
    real, ingest.subprocess.Popen = ingest.subprocess.Popen, FakePopen
    try:
        a = argparse.Namespace(no_transcribe=False, lang="es", model=None, glossary=None)
        (tmp / "txout").mkdir()
        ingest.start_transcription([m for m in res["files"] if m.get("audio_tracks")], tmp / "txout", a,
                                   lambda *_: None)
    finally:
        ingest.subprocess.Popen = real
    cmd = seen.get("cmd", [])
    nm = cmd[cmd.index("--names") + 1].split("/") if "--names" in cmd else []
    checks["transcription gets no clip without audio, and the unique names"] = \
        bool(cmd) and not any(str(x).endswith("drone.mp4") for x in cmd) and "day2-C0001.MP4" in nm

    # transcribe.py without a model: a clip without audio is recorded as such, under its name.
    tx = tmp / "tx"
    tx.mkdir()
    (tx / "transcript.json").write_text(json.dumps({"files": {"other.mp4": {"segments": [], "duration": 1}},
                                                    "order": ["other.mp4"]}), encoding="utf-8")
    r = run_tx(tx, str(raw / "drone.mp4"), "--names", "day9-drone.mp4")
    t = json.loads((tx / "transcript.json").read_text(encoding="utf-8"))
    if r.returncode:
        print(r.stdout, r.stderr)
    checks["transcribe: no audio stream → empty entry, not a failure"] = \
        r.returncode == 0 and t["files"].get("day9-drone.mp4", {}).get("no_audio") is True and "loading" not in r.stdout
    checks["transcribe: entries on disk from another run are kept"] = "other.mp4" in t["files"]
    run_tx(tx, str(raw / "drone.mp4"), "--names", "day9-drone.mp4", "--prune")
    t = json.loads((tx / "transcript.json").read_text(encoding="utf-8"))
    checks["transcribe --prune: files no longer in the material leave"] = \
        list(t["files"]) == ["day9-drone.mp4"] and t["order"] == ["day9-drone.mp4"]
    r = run_tx(tx, str(raw / "truncated.mp4"), str(raw / "drone.mp4"), "--names", "bad.mp4/day9-drone.mp4")
    t = json.loads((tx / "transcript.json").read_text(encoding="utf-8"))
    checks["transcribe: an unreadable file is recorded as failed, the next still done"] = \
        "FAILED" in r.stdout and t["files"].get("bad.mp4", {}).get("error") and "day9-drone.mp4" in t["files"]

    # The lock: a live holder makes a second run wait; a dead one is ignored.
    holder = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (tx / transcribe.LOCK).write_text(str(holder.pid), encoding="utf-8")
        t0 = time.time()
        try:
            run_tx(tx, str(raw / "drone.mp4"), "--names", "lock.mp4", timeout=8)
            waited = False
        except subprocess.TimeoutExpired:
            waited = True
        checks["lock held by a live process → a second transcription waits"] = waited and time.time() - t0 >= 7
    finally:
        holder.kill()
        holder.wait()
    r = run_tx(tx, str(raw / "drone.mp4"), "--names", "lock.mp4", timeout=60)
    checks["lock from a dead process → ignored, the run goes ahead"] = r.returncode == 0 and \
        "lock.mp4" in json.loads((tx / "transcript.json").read_text(encoding="utf-8"))["files"]
    checks["lock released at the end"] = not (tx / transcribe.LOCK).exists()

    # Retakes: a shared start isn't a retake; a fragment said after a full take doesn't replace it.
    tk = lambda s: transcribe.norm(s).split()  # noqa: E731
    checks["'Si no tenés plata…' / 'Si no tenés tiempo…' → not a retake"] = not transcribe.is_retake(
        tk("Si no tenés plata, no podés invertir."), tk("Si no tenés tiempo, no podés entrenar."), False, True)
    checks["same start, different lines → not a retake"] = not transcribe.is_retake(
        tk("Lo primero que hago es abrir la cámara."), tk("Lo primero que hago después es revisar el audio."),
        False, True)
    checks["full take, then a fragment of it → the full take is not the retake"] = not transcribe.is_retake(
        tk("Hoy les voy a mostrar el error."), tk("Hoy les voy a"), False, True)
    checks["abandoned start → retake"] = transcribe.is_retake(
        tk("Hoy les voy a"), tk("Hoy les voy a mostrar el error."), False, False)
    checks["abandoned one word in → retake"] = transcribe.is_retake(
        tk("Hoy les voy a explicar"), tk("Hoy les voy a mostrar el error que todos cometen."), False, False)
    checks["the same line said again with a word changed → retake"] = transcribe.is_retake(
        tk("Este es el error que todos cometen al editar."), tk("Este es el error que todos hacen al editar."),
        False, True)

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    print(f"\nfiles in {tmp}" if args.keep else "")
    if not args.keep:
        shutil.rmtree(tmp, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
