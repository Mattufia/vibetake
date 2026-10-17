# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Check the delivery before handing it over.

    uv run qa.py [03-delivery/edit.mp4] [--platform reels --platform tiktok] [--loudness social] [--cleanup]

Writes 03-delivery/qa/qa.md (PASS / WARN / FAIL per check) and contact sheets of the
final file with the vertical safe zone drawn on them (03-delivery/qa/). Exit code 1
if anything FAILs. Checks:
    format      H.264 yuv420p, constant frame rate, BT.709 tags, AAC 48 kHz, faststart
    size        resolution = the edit's output; platform aspect, length and file size
    duration    picture = sound = the edit's timeline
    picture     unintended black or frozen stretches
    sound       loudness on target, true peak under the ceiling, long silences
    subtitles   .srt rules (≤2 lines of ≤42 chars, 1–7 s, no overlaps, inside the
                video); burned-in captions inside the safe zone
    delivery    .srt present; thumbnails present (2–3 designed covers are expected)
--cleanup deletes the heavy disposable intermediates (preview, render chunks,
normalized copies) — only when nothing failed. Look at the sheets anyway.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import ffprobe, filter_path, find_font, run, tc, tool  # noqa: E402
from _timeline import total as timeline_total  # noqa: E402
from audio import TARGETS, measure  # noqa: E402
from subtitles import CHAR_W  # noqa: E402

# Keep in sync with references/platforms.md (values change; verify periodically).
PLATFORMS = {
    "tiktok": {"aspect": 9 / 16, "max_s": 3600},
    "reels": {"aspect": 9 / 16, "max_s": 180},
    "shorts": {"aspect": 9 / 16, "max_s": 180},
    "youtube": {"aspect": 16 / 9, "max_s": 15 * 60, "note": "longer needs a verified account"},
    "x": {"aspect": None, "max_s": 140},
    "linkedin": {"aspect": None, "max_s": 600},
}
SAFE_V = (0.06, 0.13, 0.86, 0.77)          # x0, y0, x1, y1 of the 9:16 safe zone


class Report:
    def __init__(self):
        self.rows: list[tuple[str, str, str]] = []

    def add(self, status: str, check: str, detail: str) -> None:
        self.rows.append((status, check, detail))

    def ok(self, cond: bool, check: str, detail: str, bad: str = "FAIL") -> None:
        self.add("PASS" if cond else bad, check, detail)

    @property
    def failed(self) -> bool:
        return any(s == "FAIL" for s, _, _ in self.rows)


def frac(s: str | None) -> float:
    n, _, d = (s or "0/0").partition("/")
    return float(n) / float(d) if d and float(d) else 0.0


def faststart(path: Path) -> bool | None:
    """True if the moov box comes before mdat (playback starts while downloading)."""
    order = []
    with open(path, "rb") as f:
        while True:
            head = f.read(8)
            if len(head) < 8:
                break
            size, kind = struct.unpack(">I4s", head)
            if size == 1:
                size = struct.unpack(">Q", f.read(8))[0] - 8
            order.append(kind)
            if size < 8:
                break
            f.seek(size - 8, 1)
    if b"moov" not in order or b"mdat" not in order:
        return None
    return order.index(b"moov") < order.index(b"mdat")


def intervals(err: str, start_key: str, end_key: str) -> list[tuple[float, float]]:
    s = [float(x) for x in re.findall(rf"{start_key}[:=]\s*([\d.]+)", err)]
    e = [float(x) for x in re.findall(rf"{end_key}[:=]\s*([\d.]+)", err)]
    return list(zip(s, e + [float("inf")] * (len(s) - len(e))))


def check_srt(path: Path, dur: float, r: Report) -> None:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        r.add("FAIL", "subtitles", f"{path.name} is empty")
        return
    cues = []
    for block in text.split("\n\n"):
        lines = block.splitlines()
        a, b = lines[1].split(" --> ")
        sec = lambda s: sum(float(x) * m for x, m in zip(s.replace(",", ".").split(":"), (3600, 60, 1)))  # noqa: E731
        cues.append((sec(a), sec(b), lines[2:]))
    long_lines = [ln for _, _, ls in cues for ln in ls if len(ln) > 42]
    too_many = [ls for _, _, ls in cues if len(ls) > 2]
    bad_dur = [(a, b) for a, b, _ in cues if not 0.99 <= b - a <= 7.01]
    overlaps = [(x[0], y[0]) for x, y in zip(cues, cues[1:]) if y[0] < x[1] - 1e-3]
    outside = [a for a, b, _ in cues if b > dur + 0.05]
    r.ok(not (long_lines or too_many), "subtitles: line length",
         f"{len(cues)} cues; lines over 42: {len(long_lines)}, cues over 2 lines: {len(too_many)}", "WARN")
    r.ok(not bad_dur, "subtitles: cue duration", f"outside 1–7 s: {len(bad_dur)}", "WARN")
    r.ok(not overlaps, "subtitles: overlaps", f"{len(overlaps)} overlapping cues")
    r.ok(not outside, "subtitles: inside the video", f"{len(outside)} cue(s) end after the video")


def sheets(video: Path, out_dir: Path, w: int, h: int, dur: float) -> list[str]:
    """Contact sheets of the final file, with the safe zone drawn on vertical videos."""
    for old in out_dir.glob("sheet_*.jpg"):
        old.unlink()
    n = int(min(48, max(16, dur / 1.5)))
    interval = max(dur / n, 0.04)
    font = find_font()
    f = f"fontfile='{filter_path(font)}':" if font else ""
    draw = (f"drawtext={f}text='%{{pts\\:hms}}':x=8:y=8:fontsize=26:fontcolor=white:box=1:boxcolor=black@0.65"
            ":boxborderw=6")
    safe = ""
    if h > w:
        x0, y0, x1, y1 = SAFE_V
        safe = (f",drawbox=x={int(x0 * 480)}:y={int(y0 * 480 * h / w)}:w={int((x1 - x0) * 480)}:"
                f"h={int((y1 - y0) * 480 * h / w)}:color=lime@0.9:t=3")
    run([tool("ffmpeg"), "-v", "error", "-y", "-i", str(video), "-vf",
         f"fps=1/{interval:.4f},scale=480:-2{safe},{draw},tile=4x2:padding=4:margin=4", "-fps_mode", "vfr",
         "-q:v", "3", str(out_dir / "sheet_%02d.jpg")])
    return sorted(p.name for p in out_dir.glob("sheet_*.jpg"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", nargs="?", default="03-delivery/edit.mp4")
    ap.add_argument("--edl", default="02-work/edl.json")
    ap.add_argument("--platform", action="append", choices=tuple(PLATFORMS), default=[])
    ap.add_argument("--loudness", choices=tuple(TARGETS), default="social")
    ap.add_argument("--srt", help="default: the video's name with .srt")
    ap.add_argument("--subs", default="02-work/subs.ass")
    ap.add_argument("--cleanup", action="store_true", help="delete disposable intermediates if nothing failed")
    args = ap.parse_args()

    video = Path(args.video)
    if not video.exists():
        sys.exit(f"ERROR: {video} not found")
    r = Report()
    info = ffprobe(video)
    v = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    a = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
    if v is None:
        sys.exit("ERROR: no video stream")
    W, H = int(v["width"]), int(v["height"])
    vd = float(v.get("duration") or info["format"]["duration"])
    ad = float((a or {}).get("duration") or 0)
    fps, rfps = frac(v.get("avg_frame_rate")), frac(v.get("r_frame_rate"))

    # Format
    r.ok(v.get("codec_name") == "h264", "format: video codec", v.get("codec_name"), "WARN")
    r.ok(v.get("pix_fmt") == "yuv420p", "format: pixel format", f"{v.get('pix_fmt')} (yuv420p plays everywhere)")
    r.ok(abs(fps - rfps) < 0.01, "format: constant frame rate", f"{fps:.3f} fps (r {rfps:.3f})", "WARN")
    tags = (v.get("color_primaries"), v.get("color_transfer"), v.get("color_space"))
    r.ok(tags == ("bt709", "bt709", "bt709"), "format: BT.709 tags", "/".join(map(str, tags)), "WARN")
    r.ok(a is not None and a.get("codec_name") == "aac" and a.get("sample_rate") == "48000",
         "format: audio", f"{(a or {}).get('codec_name')} {(a or {}).get('sample_rate')} Hz "
         f"{(a or {}).get('channels')} ch", "WARN")
    fs = faststart(video) if video.suffix.lower() in (".mp4", ".m4v", ".mov") else None
    r.ok(fs is not False, "format: faststart", "index at the start" if fs else "index at the end — re-mux with "
         "-movflags +faststart" if fs is False else "n/a", "WARN")

    # Size and duration against the edit
    edl = json.loads(Path(args.edl).read_text(encoding="utf-8")) if Path(args.edl).exists() else None
    if edl:
        o = edl["output"]
        r.ok((W, H) == (o["width"], o["height"]), "size: matches the edit", f"{W}×{H} (edit {o['width']}×{o['height']})")
        exp = timeline_total(edl["segments"], o["fps"])
        r.ok(abs(vd - exp) <= 2 / o["fps"], "duration: matches the edit", f"{tc(vd)} (edit {tc(exp)})")
    r.ok(a is not None and abs(vd - ad) <= 1.5 / max(fps, 1), "duration: picture = sound",
         f"video {vd:.3f} s · audio {ad:.3f} s")

    # Picture: black and frozen stretches (one pass)
    err = run([tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(video), "-an", "-vf",
               "blackdetect=d=0.25:pic_th=0.95,freezedetect=n=0.002:d=1.5", "-f", "null", "-"], check=False).stderr
    black = intervals(err, "black_start", "black_end")
    inner_black = [(s, e) for s, e in black if s > 0.3 and min(e, vd) < vd - 0.3]
    r.ok(not inner_black, "picture: black frames",
         ", ".join(f"{tc(s)}–{tc(min(e, vd))}" for s, e in black) or "none",
         "FAIL" if any(min(e, vd) - s > 1 for s, e in inner_black) else "WARN")
    frozen = intervals(err, "lavfi.freezedetect.freeze_start", "lavfi.freezedetect.freeze_end")
    r.ok(not frozen, "picture: frozen stretches",
         (", ".join(f"{tc(s)}–{tc(min(e, vd))}" for s, e in frozen) or "none") +
         (" — fine if it's a still or a static shot; check" if frozen else ""), "WARN")

    # Sound
    if a is not None:
        target, ceiling = TARGETS[args.loudness]
        m = measure(video)
        r.ok(m["I"] is not None and abs(m["I"] - target) <= 1.0, f"sound: loudness ({args.loudness})",
             f"{m['I']} LUFS (target {target})")
        r.ok(m["TP"] is not None and m["TP"] <= ceiling + 0.1, "sound: true peak",
             f"{m['TP']} dBTP (ceiling {ceiling})")
        err = run([tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(video), "-vn", "-af",
                   "silencedetect=noise=-50dB:d=1.5", "-f", "null", "-"], check=False).stderr
        sil = [(s, e) for s, e in intervals(err, "silence_start", "silence_end") if s > 1 and min(e, vd) < vd - 1]
        r.ok(not sil, "sound: long silences", ", ".join(f"{tc(s)}–{tc(min(e, vd))}" for s, e in sil) or "none",
             "WARN")
    else:
        r.add("FAIL", "sound", "no audio stream")

    # Subtitles
    srt = Path(args.srt) if args.srt else video.with_suffix(".srt")
    if srt.exists():
        check_srt(srt, vd, r)
    else:
        r.add("WARN", "delivery: .srt", f"{srt.name} missing (platforms use it for accessibility and search)")
    ass = Path(args.subs)
    if ass.exists() and H > W:
        text = ass.read_text(encoding="utf-8")
        base = int(re.search(r"^Style: Main,[^,]*,(\d+),", text, re.M).group(1))
        out = 0
        events = [ln.split(",", 9)[9] for ln in text.splitlines() if ln.startswith("Dialogue:")]
        for body in events:
            m = re.search(r"\\pos\((\d+),(\d+)\)", body)
            fs = int(f.group(1)) if (f := re.search(r"\\fs(\d+)", body)) else base
            visible = max(re.sub(r"\{[^}]*\}", "", body).split("\\N"), key=len)
            half_w = len(visible) * CHAR_W * fs / 2 / W
            x, y = int(m.group(1)) / W, int(m.group(2)) / H
            if not (SAFE_V[1] <= y <= SAFE_V[3] and x - half_w >= SAFE_V[0] - 0.01 and x + half_w <= SAFE_V[2] + 0.01):
                out += 1
        r.ok(not out, "subtitles: burned-in inside the safe zone",
             f"{len(events)} caption events, {out} outside (height or width)", "WARN")

    # Platforms
    size_mb = video.stat().st_size / 1e6
    for p in args.platform:
        spec = PLATFORMS[p]
        if spec["aspect"]:
            r.ok(abs(W / H - spec["aspect"]) < 0.01, f"{p}: aspect", f"{W}×{H}")
        r.ok(vd <= spec["max_s"], f"{p}: length", f"{tc(vd)} (max {tc(spec['max_s'])}"
             + (f"; {spec['note']}" if spec.get("note") else "") + ")")
        r.ok(min(W, H) >= 1080, f"{p}: resolution", f"{min(W, H)} px short side (1080 recommended)", "WARN")
    thumbs = [p for p in video.parent.glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png")
              and re.search(r"cover|thumb|portada", p.stem, re.I)]
    r.ok(bool(thumbs), "delivery: thumbnails", f"{len(thumbs)} found (2–3 designed covers expected)", "WARN")

    qa_dir = video.parent / "qa"
    qa_dir.mkdir(exist_ok=True)
    shots = sheets(video, qa_dir, W, H, vd)
    icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}
    lines = [f"# QA: {video.name}", "", f"{W}×{H} · {fps:.2f} fps · {tc(vd)} · {size_mb:.1f} MB", "",
             "| | Check | Detail |", "|---|---|---|"]
    lines += [f"| {icon[s]} | {c} | {d} |" for s, c, d in r.rows]
    lines += ["", "Contact sheets (safe zone in green on vertical): " + ", ".join(f"qa/{s}" for s in shots) +
              " — look at them: framing, captions readable, nothing cut off."]
    (qa_dir / "qa.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for s, c, d in r.rows:
        print(f"{s:4}  {c}: {d}")
    print(f"report → {qa_dir / 'qa.md'}")

    if args.cleanup:
        if r.failed:
            print("cleanup skipped: something failed")
        else:
            freed = 0
            for target in (Path("02-work/preview.mp4"), Path("02-work/render"), Path("01-ingest/normalized")):
                if target.exists():
                    freed += sum(f.stat().st_size for f in ([target] if target.is_file() else target.rglob("*"))
                                 if f.is_file())
                    shutil.rmtree(target) if target.is_dir() else target.unlink()
            print(f"cleanup: {freed / 1e9:.2f} GB of disposable intermediates removed "
                  "(cleaned audio and grades kept: re-renders need them)")
    sys.exit(1 if r.failed else 0)


if __name__ == "__main__":
    main()
