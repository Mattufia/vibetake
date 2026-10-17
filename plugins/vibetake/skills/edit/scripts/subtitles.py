# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""Subtitles for the edit: an .srt and burned-in word-by-word captions.

    uv run subtitles.py [--style highlight|sentence] [--upper] [--font "Arial Black"] [--no-burn]

Words come from the transcript, mapped onto the edit's timeline (what was cut isn't
there). Outputs:
    03-delivery/<name>.srt  readable captions: ≤2 lines of ≤42 characters, ≤17
                            characters per second, 1–7 s each (platforms index it and
                            use it for accessibility even when captions are burned in)
    02-work/subs.ass        burned-in captions that render.py draws over everything:
                            groups of 2–3 words with the word being said highlighted
                            (--style sentence: whole phrases, no highlight)
Placement: inside the safe zone, never over the face. Where the face is in the final
frame (after reframing) decides it: low by default, moved when the face is there.
Names, brands and numbers are listed at the end: check them by ear.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import DELIVERY, deliverable_name, die, project_font  # noqa: E402
from _timeline import starts  # noqa: E402

SAFE_V = (0.13, 0.77)          # vertical 9:16 safe band (references/safe-margins.md)
SAFE_H = (0.08, 0.92)
SAFE_X_V = (0.06, 0.86)        # horizontal extent of the 9:16 safe zone
CHAR_W = 0.68                  # average glyph width / font size for a heavy sans (Arial Black), with margin
WHITE, YELLOW, BLACK = "&H00FFFFFF", "&H0000FFFF", "&H00000000"
OUTLINE = 0.09                 # outline / font size: reads on any image without looking like a sticker


def ass_color(hex_: str) -> str:
    h = hex_.lstrip("#")
    return f"&H00{h[4:6]}{h[2:4]}{h[0:2]}".upper()


def luminance(hex_: str) -> float:
    """WCAG relative luminance (0 black, 1 white)."""
    h = hex_.lstrip("#")
    r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
               for c in (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def timeline_words(edl: dict, tr: dict) -> list[dict]:
    fps = edl["output"]["fps"]
    t0 = starts(edl["segments"], fps)
    words = {n: sorted((w for s in f["segments"] for w in s["words"]), key=lambda w: w["start"])
             for n, f in tr["files"].items()}
    out = []
    for k, s in enumerate(edl["segments"]):
        ws = words.get(s["name"], [])
        if "words" in s:
            # cutlist.py says which words the segment holds: trust that over the timestamps,
            # which can sit a little outside the cut that was placed on the real audio.
            a, b = s["words"]
            chosen = ws[a:b + 1]
        else:
            chosen = [w for w in ws if s["in"] - 1e-3 <= w["start"] and w["end"] <= s["out"] + 1e-3]
        for w in chosen:
            st = min(max(w["start"], s["in"]), s["out"])
            en = min(max(w["end"], st), s["out"])
            out.append({"text": w["word"].strip(), "t": round(t0[k] + st - s["in"], 3),
                        "end": round(t0[k] + en - s["in"], 3), "seg": k, "src": w["start"]})
    return out


def srt_cues(words: list[dict]) -> list[dict]:
    cues, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        text = " ".join(x["text"] for x in cur)
        if (nxt is None or w["text"].endswith((".", "?", "!", "…")) or len(text) > 70
                or nxt["t"] - w["end"] > 0.6 or w["end"] - cur[0]["t"] > 6):
            cues.append({"start": cur[0]["t"], "end": w["end"], "text": text})
            cur = []
    for i, c in enumerate(cues):
        limit = cues[i + 1]["start"] - 0.04 if i + 1 < len(cues) else c["end"] + 2
        need = max(1.0, len(c["text"]) / 17)                       # ≥1 s and ≤17 characters per second
        c["end"] = round(min(max(c["end"], c["start"] + need), limit, c["start"] + 7), 3)
        c["lines"] = wrap(c["text"], 42)
    return cues


def wrap(text: str, width: int) -> list[str]:
    if len(text) <= width:
        return [text]
    words = text.split()
    best = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(text) / 2))
    return [" ".join(words[:best]), " ".join(words[best:])]


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def groups(words: list[dict], max_words: int, max_chars: int) -> list[list[dict]]:
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        text = " ".join(x["text"] for x in cur)
        if (nxt is None or len(cur) >= max_words or w["text"].endswith((".", "?", "!", "…", ",", ";", ":"))
                or nxt["t"] - w["end"] > 0.3 or len(text) + 1 + len(nxt["text"]) > max_chars):
            out.append(cur)
            cur = []
    return out


def face_band(t: float, edl: dict, t0: list[float], faces: dict, material: dict, broll: list) -> tuple | None:
    """Vertical extent (0-1 of the output) of the head on screen at timeline t, or None."""
    if any(b["at"] <= t <= b["at"] + b["dur"] for b in broll):
        return None                                        # b-roll on screen: no face to avoid
    k = max(i for i, s in enumerate(t0) if s <= t + 1e-6)
    seg = edl["segments"][k]
    src_t = seg["in"] + t - t0[k]
    samples = faces.get(seg["name"]) or []
    if not samples:
        return None
    s = min(samples, key=lambda x: abs(x["t"] - src_t))
    if abs(s["t"] - src_t) > 0.6 or not s["faces"]:
        return None
    x, y, w, h = max(s["faces"], key=lambda f: f[3])[:4]
    top, bottom = y - 0.3 * h, y + h                        # hair above the box down to the chin (neck is fine)
    fr = seg.get("frame")
    if fr:
        ch = fr["crop"][1]
        keys = fr["keys"] or [[0, 0.5, 0.5]]
        cy = min(keys, key=lambda kk: abs(kk[0] - (t - t0[k])))[2]
    else:
        m = material.get(seg["name"], {})
        sw, sh = m.get("display_width", 1), m.get("display_height", 1)
        W, H = edl["output"]["width"], edl["output"]["height"]
        ch, cy = min(1.0, sw / (W / H) / sh), 0.5
    y0 = cy - ch / 2
    mouth = y + 0.85 * h                                     # bottom of the lips, roughly
    return (top - y0) / ch, (bottom - y0) / ch, (mouth - y0) / ch


def place(band: tuple | None, default: float, half: float, safe: tuple) -> tuple[float, bool]:
    """Caption center (0-1). Default low; below the face if it fits in the safe zone,
    above it otherwise. Returns (y, clashed) — clashed if it couldn't avoid the face."""
    if band is None:
        return default, False
    top, bottom, mouth = band
    margin = 0.01
    if default + half < top - margin or default - half > bottom + margin:
        return default, False
    below = bottom + margin + half
    if below + half <= safe[1]:
        return below, False
    above = top - margin - half
    if above - half >= safe[0]:
        return above, False
    # The face fills the frame: the chin can be covered, the mouth and eyes never.
    chin = mouth + margin + half
    if chin + half <= safe[1]:
        return max(chin, default), False
    return default, True


def ass_escape(t: str) -> str:
    return t.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def ass_time(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edl", default="02-work/edl.json")
    ap.add_argument("--ingest", default="01-ingest")
    ap.add_argument("--style", choices=("highlight", "sentence"), default="highlight")
    ap.add_argument("--upper", action="store_true")
    ap.add_argument("--font", help="font name (default: the project's caption font, fonts.py / design.json)")
    ap.add_argument("--size", type=float, help="font size as a share of the frame height (default by format)")
    ap.add_argument("--srt", help="default: 03-delivery/<name>.srt (one per project, not versioned)")
    ap.add_argument("--ass", default="02-work/subs.ass")
    ap.add_argument("--no-burn", action="store_true", help="only the .srt (removes the burned-in file)")
    ap.add_argument("--off", default="", help="no burned-in captions here: 'hook' and/or timeline ranges "
                                              "'12.5-15' (where a graphic already shows the words); "
                                              "the .srt keeps everything")
    args = ap.parse_args()

    edl = json.loads(Path(args.edl).read_text(encoding="utf-8"))
    ing = Path(args.ingest)
    tr = json.loads((ing / "transcript.json").read_text(encoding="utf-8"))
    faces = json.loads((ing / "faces.json").read_text(encoding="utf-8"))["files"] if (ing / "faces.json").exists() \
        else {}
    material = {m["name"]: m for m in json.loads((ing / "material.json").read_text(encoding="utf-8"))["files"]} \
        if (ing / "material.json").exists() else {}
    words = timeline_words(edl, tr)
    if not words:
        die("no words inside the edit")
    if not args.font and not args.no_burn:
        pf = project_font("captions")
        if not pf:
            die("no project font for the captions: choose this video's typefaces first (fonts.py get …) — "
                "system fonts aren't used")
        args.font = pf["name"]

    cues = srt_cues(words)
    srt = Path(args.srt) if args.srt else DELIVERY / f"{deliverable_name(edl)}.srt"
    srt.parent.mkdir(parents=True, exist_ok=True)
    srt.write_text("".join(f"{i}\n{srt_time(c['start'])} --> {srt_time(c['end'])}\n" + "\n".join(c["lines"]) + "\n\n"
                           for i, c in enumerate(cues, 1)), encoding="utf-8")

    ass = Path(args.ass)
    if args.no_burn:
        ass.unlink(missing_ok=True)
        print(f"{len(cues)} captions → {srt} (no burned-in captions)")
        return
    W, H, fps = edl["output"]["width"], edl["output"]["height"], edl["output"]["fps"]
    vertical = H > W
    size = args.size or (0.055 if vertical else 0.06)
    fs = round(H * size * (1.15 if args.upper else 1))
    safe, default = (SAFE_V, 0.70) if vertical else (SAFE_H, 0.86)
    # The 9:16 safe zone isn't symmetric (the like/comment/share column is on the right):
    # center captions on it, not on the frame.
    x0, x1 = SAFE_X_V if vertical else (0.05, 0.95)
    center_x, safe_w = (x0 + x1) / 2, (x1 - x0)
    t0 = starts(edl["segments"], fps)
    from _timeline import resolve_broll
    broll, _ = resolve_broll(edl["segments"], fps, edl.get("broll", []))
    if args.style == "highlight":
        blocks = groups(words, 3, 16 if vertical else 28)
    else:
        blocks = groups(words, 12, 42)
    off = []
    for part in filter(None, (p.strip() for p in args.off.split(","))):
        if part == "hook":
            off += [(t0[k], t0[k] + s["out"] - s["in"]) for k, s in enumerate(edl["segments"])
                    if s.get("role") == "hook"]
        else:
            a, _, b = part.partition("-")
            off.append((float(a), float(b)))
    # The highlight takes this video's accent when its design has one (design.json).
    # A dark accent would vanish on the black outline: then the highlighted word gets a
    # light outline (the design's paper, or white) of the same thickness, never thicker.
    hl_color, hl_edge = YELLOW, ""
    dj = Path("02-work/design.json")
    if dj.exists():
        try:
            cols = json.loads(dj.read_text(encoding="utf-8")).get("colors", {})
            acc = cols.get("accent", "")
            if re.fullmatch(r"#?[0-9A-Fa-f]{6}", acc or ""):
                hl_color = ass_color(acc)
                if luminance(acc) < 0.18:
                    paper = cols.get("paper", "")
                    ok = re.fullmatch(r"#?[0-9A-Fa-f]{6}", paper or "") and luminance(paper) > 0.5
                    hl_edge = ass_color(paper if ok else "#FFFFFF")
        except ValueError:
            pass
    outline = max(2, round(fs * OUTLINE))
    events, clashes, skipped = [], 0, 0
    for gi, g in enumerate(blocks):
        start = g[0]["t"]
        nxt = blocks[gi + 1][0]["t"] if gi + 1 < len(blocks) else g[-1]["end"] + 0.5
        end = min(g[-1]["end"] + 0.3, nxt)                    # hold briefly, never overlap the next
        if any(a - 1e-3 <= (start + g[-1]["end"]) / 2 <= b + 1e-3 for a, b in off):
            skipped += 1
            continue
        lines = 1 if args.style == "highlight" else len(wrap(" ".join(w["text"] for w in g), 42))
        half = fs * 1.25 * lines / 2 / H
        y, clash = place(face_band((start + end) / 2, edl, t0, faces, material, broll), default, half, safe)
        clashes += clash
        texts = [w["text"].upper() if args.upper else w["text"] for w in g]
        # Width: shrink the font for a group that wouldn't fit the safe width (a long word).
        longest = max(wrap(" ".join(texts), 42) if args.style == "sentence" else [" ".join(texts)], key=len)
        fs_g = min(fs, int(safe_w * W / (len(longest) * CHAR_W)))
        # A smaller font keeps the outline in proportion (it's set in pixels, not relative).
        bord = max(2, round(outline * fs_g / fs))
        pos = (f"{{\\an5\\pos({int(center_x * W)},{int(y * H)})"
               + (f"\\fs{fs_g}\\bord{bord}\\shad{max(1, bord // 3)}" if fs_g < fs else "") + "}")
        on = f"{{\\c{hl_color}&" + (f"\\3c{hl_edge}&" if hl_edge else "") + "}"
        back = f"{{\\c{WHITE}&" + (f"\\3c{BLACK}&" if hl_edge else "") + "}"
        if args.style == "highlight":
            for i, w in enumerate(g):
                ws = start if i == 0 else w["t"]
                we = g[i + 1]["t"] if i + 1 < len(g) else end
                body = " ".join((f"{on}{ass_escape(tx)}{back}" if j == i else ass_escape(tx))
                                for j, tx in enumerate(texts))
                events.append(f"Dialogue: 0,{ass_time(ws)},{ass_time(we)},Main,,0,0,0,,{pos}{body}")
        else:
            body = "\\N".join(ass_escape(x) for x in wrap(" ".join(texts), 42))
            events.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Main,,0,0,0,,{pos}{body}")
    header = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {W}\nPlayResY: {H}\nWrapStyle: 2\n"
              "ScaledBorderAndShadow: yes\n\n[V4+ Styles]\n"
              "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
              "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
              "Alignment, MarginL, MarginR, MarginV, Encoding\n"
              f"Style: Main,{args.font},{fs},{WHITE},{WHITE},{BLACK},&H80000000,-1,0,0,0,100,100,0,0,1,"
              f"{outline},{max(1, outline // 3)},5,{int(W * 0.08)},{int(W * 0.14 if vertical else W * 0.08)},0,1\n\n"
              "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
    ass.parent.mkdir(parents=True, exist_ok=True)
    ass.write_text(header + "\n".join(events) + "\n", encoding="utf-8")

    review = [c["text"] for c in cues if re.search(r"\d|(?<=\s)[A-ZÁÉÍÓÚÑ][\wáéíóúñ]+", c["text"])]
    print(f"{len(cues)} captions → {srt} · {len(blocks) - skipped} burned-in groups ({args.style}) → {ass}"
          + (f" · {skipped} left out (--off)" if skipped else ""))
    if clashes:
        print(f"WARNING: {clashes} group(s) couldn't avoid the face inside the safe zone (left low)")
    if review:
        print("Check names, brands and numbers by ear:")
        for r in review:
            print("  -", r)


if __name__ == "__main__":
    main()
