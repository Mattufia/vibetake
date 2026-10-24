# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26", "opencv-python-headless>=4.9"]
# ///
"""Montage to music: shots chosen by looking at them, cut on the beat — no speech needed.

    uv run montage.py moments [--files a.mp4,b.mp4] [--max 6]      # → 02-work/montage/ (sheets, moments.md)
    uv run montage.py build --order "M3,M12,M7-M9" --music music/track.mp3 [--in 12.0]
                            [--pace auto|fast|slow] [--where only|start|end] [--sound mute|keep]
                            [--format source|vertical|horizontal|square]
    uv run montage.py clear                                         # the montage block out of edl.json

moments: every video is split into moments — at its shot changes (from ingest), and a long shot
at its calmest points so no moment runs past --max seconds — each with its action peak (where
the picture changes most). The sheets (02-work/montage/sheet_NN.jpg) show each moment's start,
peak and end with its id; moments.md lists them with notes that only compare a moment with the
rest of its own clip ("softer", "shakier", "darker", "brighter" — never a verdict: it can be the
intent). Look at the sheets and choose: the order is the edit.

build: the music sets the time. Beats and bars from audio.py beats; the track's energy where each
shot falls sets its length — 2 beats where it's loud, a bar where it's medium, two bars where it's
quiet (--pace fast halves it, slow doubles it) — never longer than the moment. Every cut lands on
a beat (on a bar for shots of a bar or more), frame-exact, and each shot shows the stretch around
its action peak. --where only: the whole edit is the montage; start / end: a block before or after
the spoken edit (cutlist.py keeps it through re-cuts), its music coming in with it and fading
under the first words. The shots' own sound is muted (room tone under the music) unless --sound keep.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _common import die, load_json, material_index, sdr_source, tc, tool  # noqa: E402

OUT = Path("02-work/montage")
ACT_FPS = 10          # activity samples per second
MIN_S = 0.6           # shorter moments (a flash, a shot-change artifact) are left out
CELL = 240            # sheet thumbnail size
FORMATS = {"vertical": (1080, 1920), "horizontal": (1920, 1080), "square": (1080, 1080)}
BEATS = {"high": 2, "mid": 4, "low": 8}     # beats per shot by the track's energy


# ------------------------------------------------------------------- moments

def activity(m: dict) -> np.ndarray:
    """How much the picture changes, ACT_FPS times a second (mean absolute difference, 0-255)."""
    src, filt = sdr_source(m)
    vf = ",".join(x for x in (filt, f"fps={ACT_FPS}", "scale=96:54:flags=area", "format=gray") if x)
    raw = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", src, "-vf", vf, "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    f = np.frombuffer(raw, np.uint8)[: len(raw) // (96 * 54) * 96 * 54].reshape(-1, 54, 96).astype(np.float32)
    if len(f) < 2:
        return np.zeros(max(len(f), 1), np.float32)
    return np.r_[0.0, np.abs(np.diff(f, axis=0)).mean((1, 2))].astype(np.float32)


def smooth(x: np.ndarray, s: float = 0.5) -> np.ndarray:
    n = max(1, int(s * ACT_FPS))
    return np.convolve(x, np.ones(n) / n, "same") if len(x) > n else x


def split(a: float, b: float, act: np.ndarray, max_s: float) -> list[tuple[float, float]]:
    """A shot in pieces of at most max_s, each cut at the calmest point near its even split."""
    k = math.ceil((b - a) / max_s - 1e-9)
    if k <= 1:
        return [(a, b)]
    cuts, step = [a], (b - a) / k
    sm = smooth(act)
    for i in range(1, k):
        # Near the even split, and never leaving a piece (this one or what's left) over max_s.
        lo_t = max(a + (i - 0.3) * step, b - (k - i) * max_s)
        hi_t = min(a + (i + 0.3) * step, cuts[-1] + max_s)
        lo, hi = int(math.ceil(lo_t * ACT_FPS)), int(hi_t * ACT_FPS) + 1
        seg = sm[lo:hi]
        cuts.append(min(max((lo + int(np.argmin(seg))) / ACT_FPS, lo_t), hi_t) if len(seg) else a + i * step)
    return list(zip(cuts, cuts[1:] + [b]))


def signals(samples: list[dict], a: float, b: float) -> dict | None:
    """From ingest's faces.json (every 0.5 s): sharpness, camera shake, blown/crushed %, faces."""
    q = [s for s in samples if a <= s["t"] <= b and s.get("q")]
    if not q:
        return None
    moves = [math.hypot(s["q"][2], s["q"][3]) for s in q]
    return {"sharp": float(np.median([s["q"][1] for s in q])), "shake": float(np.std(moves) + np.mean(moves)),
            "blown": float(np.mean([s["q"][4] for s in q])), "crushed": float(np.mean([s["q"][5] for s in q])),
            "face": any(f[3] > 0.08 for s in q for f in s.get("faces", []))}


def notes(mine: dict | None, rest: list[dict]) -> list[str]:
    """Only against the other moments of the same clip, and only when clearly apart."""
    if not mine or len(rest) < 3:
        return []
    med = {k: float(np.median([r[k] for r in rest])) for k in ("sharp", "shake", "blown", "crushed")}
    out = []
    if mine["sharp"] < 0.55 * med["sharp"]:
        out.append("softer")
    if mine["shake"] > 2 * med["shake"] and mine["shake"] > 0.004:
        out.append("shakier")
    if mine["crushed"] > med["crushed"] + 15:
        out.append("darker")
    if mine["blown"] > med["blown"] + 10:
        out.append("brighter")
    return out


def thumb(m: dict, t: float) -> np.ndarray:
    src, filt = sdr_source(m)
    vf = ",".join(x for x in (filt, f"scale={CELL}:{CELL}:force_original_aspect_ratio=decrease") if x)
    png = subprocess.run([tool("ffmpeg"), "-v", "error", "-ss", f"{max(t, 0):.3f}", "-i", src, "-frames:v", "1",
                          "-vf", vf, "-f", "image2pipe", "-c:v", "png", "-"], capture_output=True).stdout
    cell = np.zeros((CELL, CELL, 3), np.uint8)
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR) if png else None
    if img is not None:
        h, w = img.shape[:2]
        y, x = (CELL - h) // 2, (CELL - w) // 2
        cell[y:y + h, x:x + w] = img
    return cell


def sheets(moms: list[dict], material: dict) -> list[Path]:
    """Six moments per sheet, a row each: start · action peak · end, with the id and times."""
    paths = []
    for n, i in enumerate(range(0, len(moms), 6)):
        rows = []
        for mo in moms[i:i + 6]:
            m = material[mo["name"]]
            cells = [thumb(m, t) for t in (mo["in"] + 0.1, mo["peak"], mo["out"] - 0.1)]
            bar = np.full((26, CELL * 3 + 8, 3), 32, np.uint8)
            name = mo["name"].encode("ascii", "replace").decode()[:28]      # cv2 draws ASCII only
            cv2.putText(bar, f"{mo['id']}  {name}  {tc(mo['in'])}-{tc(mo['out'])}  {mo['out'] - mo['in']:.1f}s  "
                             f"peak {tc(mo['peak'])}", (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
            gap = np.zeros((CELL, 4, 3), np.uint8)
            rows += [bar, np.hstack([cells[0], gap, cells[1], gap, cells[2]]), np.zeros((6, CELL * 3 + 8, 3), np.uint8)]
        p = OUT / f"sheet_{n + 1:02d}.jpg"
        p.write_bytes(cv2.imencode(".jpg", np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes())
        paths.append(p)
    return paths


def cmd_moments(args) -> None:
    material = material_index(args.ingest)
    if not material:
        die(f"no {args.ingest}/material.json: run ingest first")
    want = set(args.files.split(",")) if args.files else None
    vids = [m for m in material.values() if m.get("kind", "video") == "video" and (want is None or m["name"] in want)]
    if not vids:
        die("no video files to take moments from")
    faces_p = Path(args.ingest) / "faces.json"
    faces = load_json(faces_p)["files"] if faces_p.exists() else {}
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("sheet_*.jpg"):
        old.unlink()
    moms = []
    for m in vids:
        act = activity(m)
        dur = m["duration"]
        bounds = [0.0, *[c for c in (m.get("shots") or {}).get("shot_changes", []) if 0 < c < dur], dur]
        mine = []
        for a, b in zip(bounds, bounds[1:]):
            for x, y in split(a, b, act, args.max):
                if y - x < MIN_S:
                    continue
                sm = smooth(act)
                lo, hi = int((x + 0.15) * ACT_FPS), max(int((y - 0.15) * ACT_FPS), int((x + 0.15) * ACT_FPS) + 1)
                seg = sm[lo:hi]
                peak = (lo + int(np.argmax(seg))) / ACT_FPS if len(seg) else (x + y) / 2
                mine.append({"name": m["name"], "file": m["file"], "in": round(x, 3), "out": round(y, 3),
                             "peak": round(min(max(peak, x), y), 3),
                             "action": round(float(seg.mean()) if len(seg) else 0.0, 2),
                             "_sig": signals(faces.get(m["name"], []), x, y)})
        sig = [mo["_sig"] for mo in mine if mo["_sig"]]
        for mo in mine:
            mo["notes"] = notes(mo["_sig"], sig)
            if mo["_sig"] and mo["_sig"]["face"]:
                mo["notes"].append("face")
        moms += mine
    for i, mo in enumerate(moms):
        mo["id"] = f"M{i + 1}"
        mo.pop("_sig", None)
    (OUT / "moments.json").write_text(json.dumps({"moments": moms}, indent=1, ensure_ascii=False), encoding="utf-8")
    paths = sheets(moms, material)
    lines = ["# Moments", "", "Look at the sheets, then `montage.py build --order \"M3,M1,...\"`. Notes compare a "
             "moment only with the rest of its clip; action = how much the picture changes (higher = busier).", ""]
    for name in dict.fromkeys(mo["name"] for mo in moms):
        lines += [f"## {name}", ""]
        lines += [f"- **{mo['id']}** {tc(mo['in'])}–{tc(mo['out'])} ({mo['out'] - mo['in']:.1f} s) · peak "
                  f"{tc(mo['peak'])} · action {mo['action']:.1f}" + (f" · {', '.join(mo['notes'])}" if mo["notes"] else "")
                  for mo in moms if mo["name"] == name]
        lines.append("")
    (OUT / "moments.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"{len(moms)} moments from {len(vids)} clip(s) → {OUT / 'moments.md'}")
    for p in paths:
        print(f"    {p}")


# --------------------------------------------------------------------- build

def parse_order(order: str, ids: dict) -> list[str]:
    out = []
    for part in (p.strip() for p in order.split(",") if p.strip()):
        if "-" in part:
            a, b = (int(x.strip().lstrip("Mm")) for x in part.split("-", 1))
            out += [f"M{i}" for i in (range(a, b + 1) if a <= b else range(a, b - 1, -1))]
        else:
            out.append("M" + part.lstrip("Mm"))
    missing = [x for x in out if x not in ids]
    if missing:
        die(f"not in moments.json: {', '.join(missing)} (montage.py moments lists them)")
    return out


def beats_of(track: Path) -> dict:
    p = track.with_suffix(".beats.json")
    if not p.exists():
        subprocess.run([tool("uv"), "run", str(Path(__file__).with_name("audio.py")), "beats", "--file", str(track)],
                       check=True)
    return load_json(p)


def beat_energy(track: Path, beats: list[float]) -> np.ndarray:
    """Loudness of the track between each beat and the next, relative to its median (1 = typical)."""
    sr = 11025
    raw = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", str(track), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
                         capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32)
    e = np.array([math.sqrt(float(np.mean(x[int(a * sr):int(b * sr)] ** 2)) + 1e-12) if int(b * sr) > int(a * sr) else 0.0
                  for a, b in zip(beats, beats[1:])])
    return e / (np.median(e) or 1)


def plan(order: list[str], moms: dict, beats: list[float], bars: list[float], energy: np.ndarray,
         music_in: float, fps: float, pace: str) -> tuple[list[dict], list[str]]:
    """Each moment in a slot of beats: its length from the energy there, cut on a beat (a bar for
    long shots), never longer than the moment; the slot's window centered on the action peak.
    Timeline 0 = music_in. Returns segments and warnings."""
    bar_set = {round(b, 3) for b in bars}
    scale = {"fast": 0.5, "slow": 2.0}.get(pace, 1.0)
    segs, warn, cur = [], [], music_in
    for mid in order:
        mo = moms[mid]
        i = next((k for k, b in enumerate(beats) if b > cur + 0.05), None)
        if i is None:
            warn.append(f"the track ends before {mid}: {len(order) - len(segs)} moment(s) left out "
                        "(a longer track, or a shorter list)")
            break
        e = float(np.mean(energy[i:i + 4])) if i < len(energy) else 1.0
        want = BEATS["high" if e >= 1.2 else "mid" if e >= 0.8 else "low"]
        want = int(min(16, max(1, round(want * scale))))
        room = mo["out"] - mo["in"]
        j = min(i + want - 1, len(beats) - 1)
        while j > i and beats[j] - cur > room + 1e-6:
            j -= 1
        if beats[j] - cur > room + 1e-6:
            warn.append(f"{mid} ({room:.1f} s) is shorter than the time to the next beat — left out")
            continue
        if want >= 4:
            on_bar = [k for k in range(i + max(1, want // 2) - 1, j + 1) if round(beats[k], 3) in bar_set]
            if on_bar:
                j = on_bar[-1]
        f0, f1 = round((cur - music_in) * fps), round((beats[j] - music_in) * fps)
        d = (f1 - f0) / fps
        if f1 <= f0:
            cur = beats[j]
            continue
        a = min(max(mo["peak"] - d / 2, mo["in"]), mo["out"] - d)
        segs.append({"source": mo["file"], "name": mo["name"], "in": round(a, 4), "out": round(a + d, 4),
                     "role": "montage", "moment": mid, "beat": round(beats[j], 3)})
        cur = beats[j]
    return segs, warn


def cmd_build(args) -> None:
    mp = OUT / "moments.json"
    if not mp.exists():
        die("no moments yet: montage.py moments, look at the sheets, then build")
    moms = {mo["id"]: mo for mo in load_json(mp)["moments"]}
    order = parse_order(args.order, moms)
    track = Path(args.music)
    if not track.exists():
        die(f"{track} doesn't exist")
    bt = beats_of(track)
    near = min(bt["beats"], key=lambda b: abs(b - args.in_)) if bt["beats"] else args.in_
    if abs(near - args.in_) > 1e-3:
        print(f"the montage starts on the beat at {near:.3f} s of the track (asked {args.in_:.3f})")
        args.in_ = near
    beats = [b for b in bt["beats"] if b >= args.in_ - 0.02]
    if len(beats) < 4:
        die(f"too few beats in {track.name} after {args.in_} s")
    energy = beat_energy(track, beats)
    edl_p = Path(args.edl)
    edl = load_json(edl_p) if edl_p.exists() else None
    if args.where != "only" and not (edl and edl.get("segments")):
        die("--where start/end puts the montage around a spoken edit: there's no edl.json yet (cutlist.py first), "
            "or use --where only")
    material = material_index(args.ingest)
    first = material.get(moms[order[0]]["name"], {})
    if args.where == "only":
        w, h = (first.get("display_width", 1920), first.get("display_height", 1080)) if args.format == "source" \
            else FORMATS[args.format]
        output = {"width": w, "height": h, "fps": first.get("fps", 30)}
    else:
        output = edl["output"]
    segs, warn = plan(order, moms, beats, bt.get("bars", []), energy, args.in_, output["fps"], args.pace)
    if not segs:
        die("no moment fits: " + "; ".join(warn))
    for s in segs:
        # Muted: room tone under the music. Kept: the place's sound as an accent, well under the music.
        s.update({"audio": "mute"} if args.sound == "mute" else {"gain_db": -12.0})
    from audio import SPEECH_LEVEL, measure
    level = measure(track)["I"] or -20.0
    music = {"source": str(track), "in": args.in_, "at": 0.0,
             # The montage's music is its main sound: at the voice's level, not a bed under it.
             "gain_db": round(SPEECH_LEVEL - level, 2), "fade_in": 0.02, "fade_out": 1.5 if args.where == "start" else 2.0,
             "duration": float(json.loads(subprocess.run([tool("ffprobe"), "-v", "error", "-show_entries",
                                                          "format=duration", "-of", "json", str(track)],
                                                         capture_output=True, text=True).stdout)["format"]["duration"]),
             "block": "montage"}
    info = {"order": args.order, "music": str(track), "in": args.in_, "pace": args.pace, "where": args.where,
            "sound": args.sound, "segments": segs}
    if args.where == "only":
        if edl and edl.get("segments") and edl.get("made_by") != "montage":
            backup = edl_p.with_name("edl.before-montage.json")
            backup.write_text(json.dumps(edl, indent=1, ensure_ascii=False), encoding="utf-8")
            print(f"the spoken edit was kept in {backup}")
        edl = {"version": 1, "output": output, "made_by": "montage", "segments": segs, "music": [music],
               "montage": info}
    else:
        rest = [s for s in edl["segments"] if s.get("role") != "montage"]
        edl["segments"] = segs + rest if args.where == "start" else rest + segs
        edl["music"] = [mu for mu in edl.get("music", []) if mu.get("block") != "montage"] + [music]
        edl["montage"] = info
    edl_p.parent.mkdir(parents=True, exist_ok=True)
    edl_p.write_text(json.dumps(edl, indent=1, ensure_ascii=False), encoding="utf-8")
    fps = output["fps"]
    total = sum(round((s["out"] - s["in"]) * fps) for s in segs) / fps
    lens = [s["out"] - s["in"] for s in segs]
    print(f"montage: {len(segs)} shots on the beat ({bt.get('bpm')} BPM), {total:.1f} s, shots "
          f"{min(lens):.2f}–{max(lens):.2f} s · {args.where} · sound {args.sound}")
    for w_ in warn:
        print(f"WARNING: {w_}")
    print("next: reframe.py (framing), then render.py --preview")


def cmd_clear(args) -> None:
    edl_p = Path(args.edl)
    edl = load_json(edl_p)
    edl["segments"] = [s for s in edl["segments"] if s.get("role") != "montage"]
    edl["music"] = [mu for mu in edl.get("music", []) if mu.get("block") != "montage"]
    edl.pop("montage", None)
    edl_p.write_text(json.dumps(edl, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"montage removed · {len(edl['segments'])} segment(s) left")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ingest", default="01-ingest")
    ap.add_argument("--edl", default="02-work/edl.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("moments")
    m.add_argument("--files", help="comma-separated file names (default: every video)")
    m.add_argument("--max", type=float, default=6.0, help="longest moment, seconds")
    b = sub.add_parser("build")
    b.add_argument("--order", required=True)
    b.add_argument("--music", required=True)
    b.add_argument("--in", dest="in_", type=float, default=0.0, help="where the montage starts in the track")
    b.add_argument("--pace", choices=("auto", "fast", "slow"), default="auto")
    b.add_argument("--where", choices=("only", "start", "end"), default="only")
    b.add_argument("--sound", choices=("mute", "keep"), default="mute")
    b.add_argument("--format", choices=("source", *FORMATS), default="source")
    sub.add_parser("clear")
    args = ap.parse_args()
    {"moments": cmd_moments, "build": cmd_build, "clear": cmd_clear}[args.cmd](args)


if __name__ == "__main__":
    main()
