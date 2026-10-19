# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Check the delivery before handing it over.

    uv run qa.py [video] [--platform reels --platform tiktok] [--loudness social] [--no-listen] [--cleanup]

video: default, the latest version in 03-delivery/ (<name>_v<N>.mp4).

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
    delivery    .srt present; per platform, 2–3 covers (cover_<platform>_<n>.jpg) at the
                right size, YouTube thumbnails under 2 MB; copy.txt with a section per
                platform within its limits (title, caption, hashtags, chapters)
    listening   the export transcribed again and compared with the edit's words: words
                missing, doubled or extra (a cut mid-word, a lost phrase, a leftover
                filler) that stills can't show
Upload reminders per platform go at the end of the report.
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
from _common import (DELIVERY, ffprobe, filter_path, find_font, frac, latest_video, run, tc, tool,  # noqa: E402
                     work_paths, writing)
from _timeline import total as timeline_total  # noqa: E402
from audio import TARGETS, measure  # noqa: E402
from subtitles import SAFE_V as SUB_SAFE_V, SAFE_X_V  # noqa: E402

WRT: dict = {}   # the video's writing system: caption line length and glyph width (set in main)

# Keep in sync with references/platforms.md (values change; verify periodically).
PLATFORMS = {
    "tiktok": {"aspect": 9 / 16, "max_s": 3600},
    "reels": {"aspect": 9 / 16, "max_s": 180},
    "shorts": {"aspect": 9 / 16, "max_s": 180},
    "youtube": {"aspect": 16 / 9, "max_s": 15 * 60, "note": "longer needs a verified account"},
    "x": {"aspect": None, "max_s": 140},
    "linkedin": {"aspect": None, "max_s": 600},
}
SAFE_V = (SAFE_X_V[0], SUB_SAFE_V[0], SAFE_X_V[1], SUB_SAFE_V[1])   # x0, y0, x1, y1 of the 9:16 safe zone
COPY_LIMITS = {      # caption characters, title characters (verify: they change)
    "reels": {"caption": 2200}, "tiktok": {"caption": 4000},
    "youtube": {"title": 100, "title_soft": 60, "description": 5000}, "shorts": {"title": 100},
}
REMINDERS = {
    "reels": "Instagram: turn on \"Upload at highest quality\" (Settings → Data usage and media quality; "
             "off by default) and set the cover from the cover_reels file.",
    "tiktok": "TikTok: turn on HD upload when posting (More options → upload quality), and set the cover.",
    "youtube": "YouTube: upload the thumbnail, the .srt (Subtitles) and the chapters in the description.",
    "shorts": "Shorts: the cover can only be chosen in the YouTube mobile app.",
}
COVER_SIZES = {"youtube": (1280, 720), "reels": (1080, 1920), "tiktok": (1080, 1920), "shorts": (1080, 1920)}


def image_size(path: Path) -> tuple[int, int]:
    p = ffprobe(path)
    s = next(st for st in p["streams"] if st.get("codec_type") == "video")
    return int(s["width"]), int(s["height"])


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


def check_srt(path: Path, dur: float, r: Report, line: int = 42) -> None:
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
    long_lines = [ln for _, _, ls in cues for ln in ls if len(ln) > line]
    too_many = [ls for _, _, ls in cues if len(ls) > 2]
    bad_dur = [(a, b) for a, b, _ in cues if not 0.99 <= b - a <= 7.01]
    overlaps = [(x[0], y[0]) for x, y in zip(cues, cues[1:]) if y[0] < x[1] - 1e-3]
    outside = [a for a, b, _ in cues if b > dur + 0.05]
    r.ok(not (long_lines or too_many), "subtitles: line length",
         f"{len(cues)} cues; lines over {line}: {len(long_lines)}, cues over 2 lines: {len(too_many)}", "WARN")
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


def digital_silence(video: Path, dur: float) -> list[tuple[float, float]]:
    """Stretches of exact silence (≥0.25 s, not at the very ends): real rooms are never silent."""
    import subprocess

    import numpy as np
    raw = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "8000",
                          "-f", "f32le", "-"], capture_output=True).stdout
    x = np.abs(np.frombuffer(raw, np.float32))
    hop = 80                                                  # 10 ms
    n = len(x) // hop
    if not n:
        return []
    dead = x[: n * hop].reshape(n, hop).max(1) < 1e-5
    out, start = [], None
    for i, d in enumerate(np.r_[dead, False]):
        if d and start is None:
            start = i
        elif not d and start is not None:
            a, b = start / 100, i / 100
            if b - a >= 0.25 and a > 0.15 and b < dur - 0.15:
                out.append((a, b))
            start = None
    return out


def check_copy(path: Path, platforms: list[str], dur: float, r: Report) -> None:
    """copy.txt: a '## <platform>' section per platform, within each platform's limits."""
    if not platforms:
        return
    if not path.exists():
        r.add("WARN", "delivery: copy", f"{path.name} missing (the text to publish with, per platform)")
        return
    text = path.read_text(encoding="utf-8")
    parts = {m.group(1).lower(): m.group(2).strip()
             for m in re.finditer(r"^##\s*(\w+)\s*\n(.*?)(?=^##\s|\Z)", text, re.M | re.S)}
    for p in platforms:
        body = parts.get(p)
        if body is None:
            r.add("WARN", f"{p}: copy", f"no '## {p}' section in {path.name}")
            continue
        lim = COPY_LIMITS.get(p, {})
        title = re.search(r"^(?:title|título|titulo)\s*:\s*(.+)$", body, re.I | re.M)
        if "title" in lim:
            if not title:
                r.add("WARN", f"{p}: title", "no 'Title:' line")
            else:
                n = len(title.group(1).strip())
                r.ok(n <= lim["title"], f"{p}: title length", f"{n} chars (max {lim['title']})")
                if n <= lim["title"] and lim.get("title_soft") and n > lim["title_soft"]:
                    r.add("WARN", f"{p}: title length", f"{n} chars: past ~{lim['title_soft']} it's cut in search")
        caption = re.sub(r"^(?:title|título|titulo)\s*:.*$", "", body, flags=re.I | re.M).strip()
        if "caption" in lim:
            r.ok(len(caption) <= lim["caption"], f"{p}: caption length",
                 f"{len(caption)} chars (max {lim['caption']})")
            tags = re.findall(r"(?<!\w)#\w+", caption)
            r.ok(3 <= len(tags) <= 5, f"{p}: hashtags", f"{len(tags)} (3–5 work best)", "WARN")
        if p == "youtube":
            r.ok(len(caption) <= lim["description"], "youtube: description length", f"{len(caption)} chars")
            if dur > 300:
                ch = re.findall(r"^\s*(\d{1,2}:\d{2}(?::\d{2})?)\s+\S", caption, re.M)
                r.ok(len(ch) >= 3 and ch[0] in ("0:00", "00:00"), "youtube: chapters",
                     f"{len(ch)} timestamps (over 5 min: chapters from 0:00, at least 3)", "WARN")


def listen(video: Path, edl: dict, ing: Path, lang: str | None, r: Report) -> None:
    """Transcribe the export again and compare it with the words the edit should contain."""
    import difflib
    import unicodedata
    from subtitles import timeline_words

    def norm(text: str) -> str:              # same as transcribe.py (which needs faster-whisper to import)
        t = unicodedata.normalize("NFD", text.lower())
        t = "".join(c for c in t if unicodedata.category(c) != "Mn")
        return re.sub(r"[^\w\s']", " ", t).strip()

    trp = ing / "transcript.json"
    if not trp.exists():
        r.add("WARN", "listening", "no transcript to compare with")
        return
    tr = json.loads(trp.read_text(encoding="utf-8"))
    lang = lang or tr.get("lang")
    if not lang:
        r.add("WARN", "listening", "skipped: pass --lang (this transcript doesn't record its language)")
        return
    out = video.parent / "qa" / "heard"
    print("listening: transcribing the export again…", flush=True)
    p = run(["uv", "run", str(Path(__file__).parent / "transcribe.py"), str(video), "--lang", lang,
             "--glossary", tr.get("glossary", "") or "", "--out", str(out)], check=False)
    if p.returncode != 0 or not (out / "transcript.json").exists():
        r.add("WARN", "listening", f"couldn't transcribe the export: {(p.stderr or '').strip()[-200:]}")
        return
    heard_tr = json.loads((out / "transcript.json").read_text(encoding="utf-8"))
    hw = [w for s in heard_tr["files"][video.name]["segments"] for w in s["words"]]
    heard = [(norm(w["word"]), w["start"]) for w in hw if norm(w["word"])]
    expected = [(norm(w["text"]), w["t"]) for w in timeline_words(edl, tr) if norm(w["text"])]
    sm = difflib.SequenceMatcher(a=[x[0] for x in expected], b=[x[0] for x in heard], autojunk=False)
    issues = []
    for op, a0, a1, b0, b1 in sm.get_opcodes():
        if op == "equal":
            continue
        exp_txt = " ".join(x[0] for x in expected[a0:a1])
        got_txt = " ".join(x[0] for x in heard[b0:b1])
        # A word heard slightly differently (accents, plural) is the model, not the edit.
        if op == "replace" and a1 - a0 == b1 - b0 and \
                difflib.SequenceMatcher(a=exp_txt, b=got_txt).ratio() > 0.7:
            continue
        at = expected[a0][1] if a0 < len(expected) else (heard[b0][1] if b0 < len(heard) else 0)
        what = {"delete": f"missing «{exp_txt}»", "insert": f"extra «{got_txt}»",
                "replace": f"«{exp_txt}» heard as «{got_txt}»"}[op]
        issues.append(f"{tc(at)} {what}")
    r.ok(not issues, "listening: the export says what the edit should",
         f"{len(expected)} words, {len(issues)} difference(s)" + (": " + "; ".join(issues[:12]) if issues else "")
         + (" — listen there: a cut mid-word, a lost or doubled word, a leftover filler" if issues else ""), "WARN")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", nargs="?", help="default: the latest version in 03-delivery/")
    ap.add_argument("--edl")
    ap.add_argument("--variant", help="check a variant (hookB, 4x5…): its file, cut and captions")
    ap.add_argument("--platform", action="append", choices=tuple(PLATFORMS), default=[])
    ap.add_argument("--loudness", choices=tuple(TARGETS), default="social")
    ap.add_argument("--srt", help="default: the video's name with .srt")
    ap.add_argument("--subs")
    ap.add_argument("--cleanup", action="store_true", help="delete disposable intermediates if nothing failed")
    ap.add_argument("--no-listen", action="store_true", help="skip transcribing the export again")
    ap.add_argument("--lang", help="language for the listening check (default: the transcript's)")
    ap.add_argument("--ingest", default="01-ingest")
    args = ap.parse_args()

    paths = work_paths(args.variant)
    tp = Path(args.ingest) / "transcript.json"
    WRT.update(writing(args.lang or (json.loads(tp.read_text(encoding="utf-8")).get("lang") if tp.exists() else None)))
    args.edl, args.subs = args.edl or str(paths["edl"]), args.subs or str(paths["subs"])
    video = Path(args.video) if args.video else (latest_video() or DELIVERY / "edit.mp4")
    if args.variant and not args.video:
        video = video.with_name(f"{video.stem}_{args.variant}.mp4")
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
        same = (W, H) == (o["width"], o["height"])
        upscaled = H > o["height"] and abs(W / H - o["width"] / o["height"]) < 0.01
        r.ok(same or upscaled, "size: matches the edit", f"{W}×{H} (edit {o['width']}×{o['height']}"
             + (", scaled up for upload" if upscaled else "") + ")")
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
        dead = digital_silence(video, vd)
        r.ok(not dead, "sound: no digital silence", (", ".join(f"{tc(s)}–{tc(e)}" for s, e in dead) or "none")
             + (" — sounds like a dropout: fill it with room tone" if dead else ""), "WARN")
    else:
        r.add("FAIL", "sound", "no audio stream")

    # Subtitles
    srt = Path(args.srt) if args.srt else video.with_name(re.sub(r"_v\d+(_[^_]+)?$", lambda k: k.group(1) or "",
                                                                 video.stem) + ".srt")
    if srt.exists():
        check_srt(srt, vd, r, WRT["line"])
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
            half_w = len(visible) * WRT["glyph"] * fs / 2 / W
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
        if p == "youtube" and W > H:
            r.ok(H >= 1440, "youtube: upload resolution", f"{H}p — YouTube gives 1440p+ uploads its better codecs "
                 "(VP9/AV1), which look better even watched at 1080p: render.py --upload-height 1440", "WARN")
    thumbs = [p for p in video.parent.glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png")
              and re.search(r"cover|thumb|portada", p.stem, re.I)]
    r.ok(bool(thumbs), "delivery: thumbnails", f"{len(thumbs)} found (three concepts expected)", "WARN")
    for p in args.platform:
        if p not in COVER_SIZES:
            continue
        mine = sorted(video.parent.glob(f"cover_{p}_*.jpg"))
        r.ok(len(mine) >= 3, f"{p}: covers", f"{len(mine)} (three concepts expected; cover.py render)", "WARN")
        for c in mine:
            w, h = image_size(c)
            r.ok((w, h) == COVER_SIZES[p], f"{p}: {c.name} size", f"{w}×{h} (expected "
                 f"{COVER_SIZES[p][0]}×{COVER_SIZES[p][1]})")
            if p == "youtube":
                r.ok(c.stat().st_size <= 2_000_000, f"{p}: {c.name} weight",
                     f"{c.stat().st_size / 1e6:.2f} MB (YouTube's limit is 2 MB)")

    check_copy(video.parent / "copy.txt", args.platform, vd, r)
    if not args.no_listen and edl:
        listen(video, edl, Path(args.ingest), args.lang, r)

    qa_dir = video.parent / "qa"
    qa_dir.mkdir(exist_ok=True)
    shots = sheets(video, qa_dir, W, H, vd)
    icon = {"PASS": "✅", "WARN": "⚠️", "FAIL": "❌"}
    lines = [f"# QA: {video.name}", "", f"{W}×{H} · {fps:.2f} fps · {tc(vd)} · {size_mb:.1f} MB", "",
             "| | Check | Detail |", "|---|---|---|"]
    lines += [f"| {icon[s]} | {c} | {d} |" for s, c, d in r.rows]
    lines += ["", "Contact sheets (safe zone in green on vertical): " + ", ".join(f"qa/{s}" for s in shots) +
              " — look at them: framing, captions readable, nothing cut off."]
    notes = [REMINDERS[p] for p in args.platform if p in REMINDERS]
    if notes:
        lines += ["", "## When uploading", ""] + [f"- {n}" for n in notes]
    (qa_dir / "qa.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    for s, c, d in r.rows:
        print(f"{s:4}  {c}: {d}")
    for n in notes:
        print(f"NOTE  {n}")
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
