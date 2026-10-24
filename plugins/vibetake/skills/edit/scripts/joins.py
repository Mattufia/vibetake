# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26", "opencv-python-headless>=4.9"]
# ///
"""Every join of the edit, to check at a glance: render.py --preview runs it on its own.

    uv run joins.py [02-work/preview.mp4] [--edl 02-work/edl.json]

Writes 02-work/joins/:
    joins.md          one line per join: where, what was cut, and ⚠ for a clear mistake
    sheet_NN.jpg      the last frame before and the first after each join, side by side
                      (red frame = flagged)
    joins.mp4         ±1 s around every join, back to back, labeled J1, J2… — every cut
                      seen and heard in a fraction of the video's length
Flags, only for clear mistakes (a join under b-roll is hidden: picture flags skipped):
    small jump        same file, same zoom, the face moved: reads as a glitch, not a new shot
    light jump        same file, the picture got clearly brighter or darker across the join
    level jump        speech on both sides, one clearly louder
    flash             a piece too short to read (a flicker)
Eyes closed or a mouth mid-word at the cut frame aren't measured: look at the sheets.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _common import die, ffprobe, filter_path, find_font, frac, spans_expr, tc, tool  # noqa: E402
from _timeline import resolve_broll, starts  # noqa: E402

WINDOW_S = 1.0          # joins.mp4: this much before and after each join
FLASH_S = 0.2           # a piece shorter than this is a flicker
SAME_ZOOM = 1.04        # face sizes within 4%: the zoom didn't change across the join
JUMP_FACE_H = 0.08      # …and the face moved more than 8% of its height
LIGHT_JUMP = 0.07       # mean brightness change (0–1) across a join in the same file
LEVEL_JUMP_DB = 6.0     # speech level change across a join
SPEECH_DB = -45.0       # both sides louder than this count as speech
SHEET_W = 1920          # sheets: about this wide, 4 rows of pairs


def on_grid(segs: list[dict]) -> None:
    """The same frame-grid rounding render.py does, so the join frames match the render."""
    fps_of: dict[str, float] = {}
    for s in segs:
        if s["source"] not in fps_of:
            try:
                v = next(st for st in ffprobe(Path(s["source"]))["streams"] if st.get("codec_type") == "video")
                fps_of[s["source"]] = frac(v.get("avg_frame_rate"))
            except Exception:
                fps_of[s["source"]] = 0.0
        f = fps_of[s["source"]]
        if f:
            s["in"], s["out"] = round(s["in"] * f) / f, round(s["out"] * f) / f


def removed_words(transcript: dict, name: str, a: float, b: float) -> str:
    f = transcript.get("files", {}).get(name)
    if not f or b <= a:
        return ""
    ws = [w["word"].strip() for s in f["segments"] for w in s.get("words", [])
          if w["start"] >= a - 0.05 and w["end"] <= b + 0.05]
    return " ".join(ws)


def what_was_cut(x: dict, y: dict, transcript: dict) -> str:
    if x.get("name") != y.get("name"):
        return f"{x.get('name')} → {y.get('name')}"
    gap = y["in"] - x["out"]
    if gap < -0.05:
        return f"jumps back {-gap:.1f} s in {y['name']} (reordered)"
    if gap < 0.05:
        return f"{y['name']}, continuous"
    said = removed_words(transcript, y["name"], x["out"], y["in"])
    return f"cut {gap:.1f} s" + (f': "{said[:80]}"' if said else " (no words)")


def frames(video: Path, idx: list[int], w: int, h: int) -> dict[int, np.ndarray]:
    """The given frame numbers in one decoding pass."""
    want = sorted(set(i for i in idx if i >= 0))
    if not want:
        return {}
    sel = spans_expr([(i, i) for i in want], "n", "\\,")      # a '+' chain fails past ~100 frames
    p = subprocess.run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(video),
                        "-vf", f"select='{sel}'", "-fps_mode", "passthrough", "-f", "rawvideo",
                        "-pix_fmt", "bgr24", "-"], capture_output=True)
    size = w * h * 3
    got = [np.frombuffer(p.stdout[k:k + size], np.uint8).reshape(h, w, 3)
           for k in range(0, len(p.stdout) - size + 1, size)]
    return dict(zip(want, got))


def audio(video: Path, rate: int = 16000) -> np.ndarray:
    p = subprocess.run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-i", str(video), "-vn",
                        "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"], capture_output=True)
    return np.frombuffer(p.stdout, np.float32)


def speech_db(x: np.ndarray, rate: int = 16000) -> float:
    """The level of the loud part (speech peaks), from 50 ms windows."""
    win = rate // 20
    if len(x) < win:
        return -99.0
    rms = np.sqrt(np.mean(x[:len(x) // win * win].reshape(-1, win) ** 2, axis=1))
    return float(20 * np.log10(np.percentile(rms, 90) + 1e-9))


def biggest_face(det, img: np.ndarray) -> tuple[float, float, float] | None:
    _, found = det.detect(img)
    if found is None or not len(found):
        return None
    f = max(found, key=lambda r: r[3])
    return float(f[0] + f[2] / 2), float(f[1] + f[3] / 2), float(f[3])


def jump_flag(fa: tuple | None, fb: tuple | None) -> str | None:
    """Faces (center x, center y, height in px) on both sides of a join in the same file."""
    if not fa or not fb:
        return None
    ratio = max(fa[2], fb[2]) / max(1.0, min(fa[2], fb[2]))
    moved = ((fa[0] - fb[0]) ** 2 + (fa[1] - fb[1]) ** 2) ** .5 / max(1.0, fa[2])
    if ratio < SAME_ZOOM and moved > JUMP_FACE_H:
        return (f"small jump (same zoom, the face moved {moved * 100:.0f}% of its height): "
                "change the zoom on one side, cover with b-roll, or cut elsewhere")
    return None


def sheets(pairs: list[dict], out_dir: Path) -> list[Path]:
    import cv2
    if not pairs:
        return []
    h0, w0 = pairs[0]["before"].shape[:2]
    tile_h = 320 if h0 > w0 else 200            # vertical: 5 pairs a row; horizontal: 2
    tw = int(round(tile_h * w0 / h0))
    pair_w = 2 * tw + 4
    cols = max(1, SHEET_W // (pair_w + 16))
    rows = 4
    files = []
    per = cols * rows
    for n in range(0, len(pairs), per):
        chunk = pairs[n:n + per]
        r = (len(chunk) + cols - 1) // cols
        sheet = np.full((r * (tile_h + 40) + 8, cols * (pair_w + 16) + 8, 3), 24, np.uint8)
        for k, p in enumerate(chunk):
            x0, y0 = 8 + (k % cols) * (pair_w + 16), 8 + (k // cols) * (tile_h + 40)
            for j, img in enumerate((p["before"], p["after"])):
                sheet[y0 + 32:y0 + 32 + tile_h, x0 + j * (tw + 4):x0 + j * (tw + 4) + tw] = cv2.resize(img, (tw, tile_h))
            color = (60, 60, 230) if p["flags"] else (200, 200, 200)
            if p["flags"]:
                cv2.rectangle(sheet, (x0 - 4, y0 + 28), (x0 + pair_w + 3, y0 + 35 + tile_h), color, 3)
            label = f"J{p['n']} {tc(p['t'])}" + ("  (b-roll)" if p["covered"] else "") + ("  !" if p["flags"] else "")
            cv2.putText(sheet, label, (x0, y0 + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 1, cv2.LINE_AA)
        path = out_dir / f"sheet_{n // per + 1:02d}.jpg"
        cv2.imwrite(str(path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
        files.append(path)
    return files


def reel(video: Path, joins: list[dict], total: float, dst: Path) -> bool:
    """±WINDOW_S around each join, back to back (overlapping windows merged), labeled."""
    spans: list[list] = []
    for j in joins:
        a, b = max(0.0, j["t"] - WINDOW_S), min(total, j["t"] + WINDOW_S)
        if spans and a <= spans[-1][1]:
            spans[-1][1] = b
            spans[-1][2].append(j["n"])
        else:
            spans.append([a, b, [j["n"]]])
    sel = spans_expr([(a, b - 0.001) for a, b, _ in spans], "t", "\\,")   # a '+' chain fails past ~100 joins
    font = find_font()
    ffont = f"fontfile='{filter_path(font)}':" if font else ""
    labels = ",".join(
        f"drawtext={ffont}text='J{'/'.join(map(str, ns[:4]))}':x=12:y=12:fontsize=28:fontcolor=white:box=1:"
        f"boxcolor=black@0.6:boxborderw=6:enable='between(t\\,{a:.3f}\\,{b:.3f})'" for a, b, ns in spans)
    p = subprocess.run([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
                        "-vf", f"{labels},select='{sel}',setpts=N/FRAME_RATE/TB",
                        "-af", f"aselect='{sel}',asetpts=N/SR/TB", "-c:v", "libx264", "-preset", "veryfast",
                        "-crf", "26", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", str(dst)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode:
        print(f"WARNING: joins.mp4 failed ({(p.stderr.strip().splitlines() or ['?'])[-1][:160]}): "
              "the sheets and joins.md are there")
    return p.returncode == 0


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", nargs="?", default="02-work/preview.mp4")
    ap.add_argument("--edl", default="02-work/edl.json")
    ap.add_argument("--transcript", default="01-ingest/transcript.json")
    ap.add_argument("--out", default="02-work/joins")
    args = ap.parse_args()
    video = Path(args.video)
    if not video.exists():
        die(f"{video} doesn't exist: render.py --preview first")
    edl = json.loads(Path(args.edl).read_text(encoding="utf-8"))
    segs = edl["segments"]
    on_grid(segs)
    fps = float(edl["output"]["fps"])
    try:
        transcript = json.loads(Path(args.transcript).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        transcript = {}
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("sheet_*.jpg"):
        old.unlink()

    t0 = starts(segs, fps)
    total = t0[-1] + max(1, round((segs[-1]["out"] - segs[-1]["in"]) * fps)) / fps
    broll, _ = resolve_broll(segs, fps, edl.get("broll", []))
    joins = []
    for k in range(1, len(segs)):
        t = t0[k]
        joins.append({"n": k, "t": t, "frame": round(t * fps), "x": segs[k - 1], "y": segs[k],
                      "covered": any(b["at"] < t < b["at"] + b["dur"] for b in broll),
                      "cut": what_was_cut(segs[k - 1], segs[k], transcript)})
    lines = [f"# Joins — {video.as_posix()}"]
    if not joins:
        lines += ["", "A single piece: no joins."]
        (out_dir / "joins.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("no joins (a single piece)")
        return

    v = next(st for st in ffprobe(video)["streams"] if st.get("codec_type") == "video")
    W, H = int(v["width"]), int(v["height"])
    imgs = frames(video, [i for j in joins for i in (j["frame"] - 1, j["frame"])], W, H)
    sound = audio(video)
    import os
    os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
    from ingest import yunet
    det = yunet(W, H)

    pairs = []
    for j in joins:
        x, y = j["x"], j["y"]
        flags = []
        before, after = imgs.get(j["frame"] - 1), imgs.get(j["frame"])
        same_file = x.get("name") == y.get("name")
        if before is not None and after is not None and not j["covered"]:
            if same_file:
                jump = jump_flag(biggest_face(det, before), biggest_face(det, after))
                if jump:
                    flags.append(jump)
                la, lb = (float(before.mean()) / 255, float(after.mean()) / 255)
                if abs(la - lb) >= LIGHT_JUMP:
                    flags.append(f"light jump ({'brighter' if lb > la else 'darker'} by {abs(la - lb) * 100:.0f}%)")
        rate = 16000
        a0 = max(0.0, max(j["t"] - WINDOW_S, j["t"] - (x["out"] - x["in"])))
        b1 = min(total, min(j["t"] + WINDOW_S, j["t"] + (y["out"] - y["in"])))
        da = speech_db(sound[int(a0 * rate):int(j["t"] * rate)])
        db = speech_db(sound[int(j["t"] * rate):int(b1 * rate)])
        if da > SPEECH_DB and db > SPEECH_DB and abs(da - db) >= LEVEL_JUMP_DB:
            flags.append(f"level jump ({'louder' if db > da else 'quieter'} by {abs(da - db):.0f} dB after)")
        for side, seg in (("before", x), ("after", y)):
            if seg["out"] - seg["in"] < FLASH_S and (side == "after" or j["n"] == 1):
                flags.append(f"flash: the piece {side} lasts {(seg['out'] - seg['in']) * 1000:.0f} ms")
        line = f"J{j['n']} {tc(j['t'])} · {j['cut']}" + (" · under b-roll" if j["covered"] else "")
        lines.append(line + "".join(f"\n   ⚠ {f}" for f in flags))
        if before is not None and after is not None:
            pairs.append({"n": j["n"], "t": j["t"], "before": before, "after": after, "flags": flags,
                          "covered": j["covered"]})
        j["flags"] = flags

    flagged = [j for j in joins if j["flags"]]
    files = sheets(pairs, out_dir)
    (out_dir / "joins.mp4").unlink(missing_ok=True)
    got_reel = reel(video, joins, total, out_dir / "joins.mp4")
    lines[1:1] = ["", f"{len(joins)} joins · {len(flagged)} flagged · sheets: "
                  f"{', '.join(p.name for p in files)}"
                  + (" · every join seen and heard: joins.mp4" if got_reel else ""), ""]
    (out_dir / "joins.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(joins)} joins, {len(flagged)} flagged → {out_dir / 'joins.md'}")
    for j in flagged:
        for f in j["flags"]:
            print(f"  J{j['n']} {tc(j['t'])}: {f}")


if __name__ == "__main__":
    main()
