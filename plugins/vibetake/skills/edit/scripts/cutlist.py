# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""From transcript lines to an edit decision list (02-work/edl.json).

    uv run cutlist.py --keep auto [--fillers drop|keep] [--pace tight|normal|relaxed|natural]
                      [--format vertical|horizontal|square|source]
    uv run cutlist.py --keep "L9,L2-L7,S2,L12"   # explicit lines (and silent stretches), in this order
    uv run cutlist.py --protect S1 --why "suspense walk"   # never cut this, on every rebuild
    uv run cutlist.py --protect L3 --why "the reaction is the joke"
    uv run cutlist.py --protect "0:03.4-0:07.0" --why "…"  # a time range (file@range with several files)
    uv run cutlist.py --unprotect P2 | --unprotect all | --list-protected

Re-running it reuses the last run's settings (format, pace, fillers, keep) unless
given again, so a rebuild for a change doesn't undo earlier choices.

--keep auto keeps every line that isn't marked as a retake. The script turns the kept
lines into cut points: word-accurate, without the dropped fillers, long pauses
shortened (--pace tight|normal|relaxed), a pause at every join that sounds like speech
(longer after a sentence than after a comma), and every cut moved to the quietest
moment nearby (never into a word that was dropped). Word edges are measured on the
audio, not taken from Whisper's timestamps (which can be a quarter second off and
usually include some air): so a dropped word leaves no fragment at the cut and the
pauses heard are the ones intended. Each segment records which words it holds. Then:

    uv run render.py            # renders 02-work/edl.json

edl.json is the source of truth of the edit: change it (or re-run this) and render
again. Segments: source, in, out (seconds in the source), lines, words, note.

Protected ranges (02-work/protected.json) are what the user asked to keep: every
rebuild includes them whole — words, fillers, silences and all — over any automatic
rule. S1, S2… are the stretches without speech listed in transcript.md (action, a
look, a walk, a dramatic pause): --keep can name them like lines; --pace natural keeps
every pause and silent stretch next to the kept lines as recorded (acted scenes).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _common import CLAUSE_END, SENTENCE_END, die, seconds, tc, tool, work_paths  # noqa: E402
from _timeline import CARRY, seg_time  # noqa: E402

FORMATS = {"vertical": (1080, 1920), "horizontal": (1920, 1080), "square": (1080, 1080)}
SNAP_S = 0.04
MIN_SEGMENT_S = 0.25
SHORT_JOIN_S = 0.6     # a short word joins its neighbor across a pause up to this long
# Pause at a join, by what ends the phrase before it (seconds, at normal pace).
GAPS = {"sentence": 0.40, "comma": 0.25, "word": 0.15}
PACE = {"tight": (0.75, 0.30), "normal": (1.0, 0.45), "relaxed": (1.35, 0.80),   # (gap scale, max pause)
        "natural": (1.0, float("inf"))}                     # pauses as recorded: acted scenes
DEFAULTS = {"keep": "auto", "fillers": "drop", "pace": "normal", "format": "source", "size": None,
            "max_pause": None}
PROTECTED = Path("02-work/protected.json")
TAIL_S = 0.05          # a word's sound lingers past its timestamp; never start inside it
START_LEAD_S = 0.10    # before the very first word
END_TAIL_S = 0.35      # after the very last word


def join_kind(last_word: str, follows: bool) -> str:
    """What kind of pause a join needs. A jump to another file or back in time reads as a
    new sentence; otherwise the punctuation of the last kept word decides."""
    w = last_word.strip()
    if not follows or w.endswith(SENTENCE_END):
        return "sentence"
    return "comma" if w.endswith(CLAUSE_END) else "word"


def transcript_sig(tr: dict) -> str:
    """Fingerprint of the transcript's numbering: which line is which moment of which file."""
    import hashlib
    items = [(s["line"], s["file"], round(s["start"], 2)) for s in tr["sentences"]]
    items += [(x["id"], x["file"], round(x["start"], 2)) for x in tr.get("silences") or []]
    return hashlib.sha1(json.dumps(items).encode()).hexdigest()[:12]


def remap_keep(kept: list[dict], tr: dict) -> str:
    """The kept lines and silent stretches found again, by file and time, in a renumbered transcript."""
    def overlap(a, b):
        return max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    out, moved = [], []
    last_id, last_hits = None, set()
    for k in kept:
        pool = (tr.get("silences") or []) if k["id"].upper().startswith("S") else tr["sentences"]
        hits = [x for x in pool if x["file"] == k["file"]
                and overlap(x, k) >= 0.5 * min(x["end"] - x["start"], k["end"] - k["start"])]
        if not hits:
            moved.append(f"{k['id']} → (gone)")
            continue
        found = []
        for x in hits:
            new = x["id"].upper() if "id" in x else f"L{x['line']}"
            found.append(new)
            # Two old lines now one: once. The same line named twice (a hook and its place): twice.
            if new in last_hits and k["id"] != last_id:
                continue
            out.append(new)
            if new != k["id"]:
                moved.append(f"{k['id']} → {new}")
        last_id, last_hits = k["id"], set(found)
    if not out:
        die("the transcript changed and none of the kept lines were found again: pass --keep")
    print("the transcript changed since the last cut: kept lines found again by time"
          + (f" ({', '.join(moved)})" if moved else " (same numbers)"))
    return ",".join(out)


def parse_keep(spec: str, sentences: list[dict], silences: list[dict] | None = None) -> list[int | str]:
    """What to keep, in the order given: line numbers and the silent stretches (S…) named among them."""
    if spec == "auto":
        return [s["line"] for s in sentences if not s.get("retake_of")]
    order: list[int | str] = []
    known_s = {x["id"].upper() for x in silences or []}
    for part in spec.replace(" ", "").split(","):
        if re.fullmatch(r"S\d+", part, re.I):
            if part.upper() not in known_s:
                die(f"{part} isn't in the transcript's silent stretches ({', '.join(sorted(known_s)) or 'none'})")
            order.append(part.upper())
            continue
        m = re.fullmatch(r"L?(\d+)(?:-L?(\d+))?", part, re.I)
        if not m:
            die(f"can't read '{part}' (use L3, L3-L7, …)")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        order += list(range(a, b + 1)) if a <= b else list(range(a, b - 1, -1))
    known = {s["line"] for s in sentences}
    missing = [n for n in order if isinstance(n, int) and n not in known]
    if missing:
        die(f"lines not in the transcript: {missing}")
    return order


def quiet_db(path: str, track: int) -> np.ndarray:
    """10 ms RMS (dB) of the source audio, to snap cuts to silence."""
    p = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", path, "-map", f"0:a:{track}", "-ac", "1", "-ar", "16000",
                        "-f", "s16le", "-"], capture_output=True)
    x = np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768
    n = len(x) // 160
    return 20 * np.log10(np.sqrt((x[:n * 160].reshape(n, 160) ** 2).mean(1)) + 1e-9)


def snap(t: float, lo: float, hi: float, db: np.ndarray) -> float:
    """Quietest 10 ms frame within ±SNAP_S of t, staying inside [lo, hi]."""
    a, b = max(t - SNAP_S, lo), min(t + SNAP_S, hi)
    i0, i1 = int(a * 100), int(b * 100)
    if i1 <= i0 or i1 > len(db):
        return min(max(t, lo), hi)
    return round((i0 + int(np.argmin(db[i0:i1]))) / 100 + 0.005, 3)


VALLEY_REACH = 0.25   # how far Whisper's word edges can be off around a gap


def valley(db: np.ndarray, gap_a: float, gap_b: float) -> tuple[float, float] | None:
    """The real silence between two words whose timestamps put the gap at [gap_a, gap_b]:
    among the stretches (≥50 ms, 18 dB under the speech around them) within VALLEY_REACH of
    it, the one that overlaps it most (then the nearest). Whisper's word edges can be off by
    a quarter second; the audio isn't. A stop consonant inside a word (the closure of a
    't') is short and close to the word's middle, so it rarely wins."""
    lo, hi = gap_a - VALLEY_REACH, gap_b + VALLEY_REACH
    i0, i1 = max(int(lo * 100), 0), min(int(hi * 100), len(db))
    if i1 - i0 < 5:
        return None
    ctx = db[max(i0 - 30, 0):min(i1 + 30, len(db))]
    quiet = db[i0:i1] < np.percentile(ctx, 90) - 18
    found, start = [], None
    for k, q in enumerate(np.r_[quiet, False]):
        if q and start is None:
            start = k
        elif not q and start is not None:
            if k - start >= 5:
                found.append(((i0 + start) / 100, (i0 + k) / 100))
            start = None
    if not found:
        return None

    def score(v):
        overlap = min(v[1], gap_b + 0.15) - max(v[0], gap_a - 0.15)
        dist = max(v[0] - gap_b, gap_a - v[1], 0)
        return (overlap, -dist)
    return max(found, key=score)


def load_protected() -> dict:
    return json.loads(PROTECTED.read_text(encoding="utf-8")) if PROTECTED.exists() else {"ranges": []}


def save_protected(data: dict) -> None:
    PROTECTED.parent.mkdir(parents=True, exist_ok=True)
    PROTECTED.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def resolve_target(spec: str, tr: dict) -> tuple[str, float, float]:
    """L3 / S1 / 0:03.4-0:07.0 / file@0:03.4-0:07.0 → (file, start, end) in the source."""
    m = re.fullmatch(r"L(\d+)", spec.strip(), re.I)
    if m:
        s = next((x for x in tr["sentences"] if x["line"] == int(m.group(1))), None) or die(f"no line {spec}")
        return s["file"], s["start"], s["end"]
    if re.fullmatch(r"S\d+", spec.strip(), re.I):
        x = next((x for x in tr.get("silences", []) if x["id"].upper() == spec.strip().upper()), None) \
            or die(f"no silent stretch {spec} (see transcript.md)")
        return x["file"], x["start"], x["end"]
    name, _, rng = spec.rpartition("@")
    if not name:
        if len(tr["files"]) != 1:
            die("several files: write it as file@0:03.4-0:07.0")
        name = next(iter(tr["files"]))
    if name not in tr["files"]:
        die(f"{name} isn't in the transcript")
    a, _, b = rng.partition("-")
    if not b:
        die(f"can't read the range '{rng}' (use 0:03.4-0:07.0)")
    return name, seconds(a), seconds(b)


def merge_forward(segments: list[dict]) -> None:
    """Neighbors in the timeline that go on in the source (same file, the next one starting
    inside or right after the previous) become one segment. A segment that goes back in the
    source (a hook, then its place) or repeats a piece stays its own: that order was chosen."""
    k = 0
    while k + 1 < len(segments):
        x, y = segments[k], segments[k + 1]
        tol = 0.3 if x.get("_sil") or y.get("_sil") else 0.05   # a named silence's edges are measured loosely
        if x["name"] == y["name"] and x["in"] - 1e-3 <= y["in"] <= x["out"] + tol and y["out"] >= x["out"] - 1e-3:
            x["out"] = max(x["out"], y["out"])
            x["lines"] = sorted(set(x["lines"]) | set(y["lines"]))
            if y.get("role") and not x.get("role"):
                x["role"] = y["role"]
            x["_sil"] = x.get("_sil") or y.get("_sil")
            del segments[k + 1]
        else:
            k += 1


def apply_protection(segments: list[dict], ranges: list[dict], srcs: dict) -> list[str]:
    """Make every protected source range part of the edit, whole: extend each segment of that
    file that touches it (a hook and its place are both extended, and stay two), or add it as
    its own segment in source order."""
    notes = []
    for r in ranges:
        name, a, b = r["file"], r["start"], r["end"]
        touch = [k for k, sg in enumerate(segments) if sg["name"] == name and sg["in"] <= b + 0.3
                 and sg["out"] >= a - 0.3]
        for k in touch:
            segments[k]["in"] = min(segments[k]["in"], a)
            segments[k]["out"] = max(segments[k]["out"], b)
        if not touch:
            new = {"source": srcs[name], "name": name, "in": a, "out": b, "lines": []}
            before = [k for k, sg in enumerate(segments) if sg["name"] == name and sg["out"] <= a]
            after = [k for k, sg in enumerate(segments) if sg["name"] == name and sg["in"] >= b]
            pos = before[-1] + 1 if before else (after[0] if after else len(segments))
            segments.insert(pos, new)
        notes.append(f"{r.get('id', '')} {r.get('what', '')} kept whole ({tc(a)}–{tc(b)}){': ' + r['why'] if r.get('why') else ''}".strip())
    merge_forward(segments)
    return notes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", help='"auto" or lines like "L9,L2-L7,S2"')
    ap.add_argument("--fillers", choices=("drop", "keep"))
    ap.add_argument("--pace", choices=tuple(PACE),
                    help="scales the pauses at the joins and the longest pause kept inside a phrase; "
                         "natural keeps them as recorded")
    ap.add_argument("--max-pause", type=float, help="override: longer pauses inside a phrase are cut")
    ap.add_argument("--format", choices=(*FORMATS, "source"))
    ap.add_argument("--size", help="explicit WxH, overrides --format")
    ap.add_argument("--protect", action="append", default=[], help="L3, S1 or a time range: never cut it")
    ap.add_argument("--why", help="why it's protected (what the user said)")
    ap.add_argument("--unprotect", help="a protection id (P2) or 'all'")
    ap.add_argument("--list-protected", action="store_true")
    ap.add_argument("--ingest", default="01-ingest")
    ap.add_argument("--out", help="default: 02-work/edl.json (a variant: 02-work/edl_<variant>.json)")
    ap.add_argument("--variant", help="a variant of the video (hookB, 4x5…): its own files next to the main ones")
    args = ap.parse_args()
    args.out = args.out or str(work_paths(args.variant)["edl"])

    ingest = Path(args.ingest)
    tr = json.loads((ingest / "transcript.json").read_text(encoding="utf-8"))

    # Protections: what the user asked to keep, stored apart so every rebuild honors it.
    prot = load_protected()
    if args.unprotect:
        before = len(prot["ranges"])
        prot["ranges"] = [] if args.unprotect == "all" else [r for r in prot["ranges"]
                                                              if r["id"].upper() != args.unprotect.upper()]
        save_protected(prot)
        print(f"removed {before - len(prot['ranges'])} protection(s)")
    for spec in args.protect:
        name, a, b = resolve_target(spec, tr)
        n = max([int(r["id"][1:]) for r in prot["ranges"]] + [0]) + 1
        prot["ranges"].append({"id": f"P{n}", "what": spec, "file": name, "start": round(a, 3), "end": round(b, 3),
                               "why": args.why or ""})
        save_protected(prot)
        print(f"protected P{n}: {spec} ({name} {tc(a)}–{tc(b)}){' — ' + args.why if args.why else ''}")
    if args.list_protected:
        for r in prot["ranges"]:
            print(f"{r['id']}  {r['what']}  {r['file']} {tc(r['start'])}–{tc(r['end'])}  {r.get('why', '')}")
        if not prot["ranges"]:
            print("nothing protected")
        return

    # A rebuild reuses the last run's choices unless they're given again, and keeps everything
    # placed on the edit (b-roll, sound effects, music, name…): they're anchored to words and
    # cuts, so they follow the new cut. A new cut file (a variant) starts from the main edit's.
    # Its settings too (format, pace, lines…): a variant differs only in what's given for it.
    prev_edl = {}
    main_edl = Path("02-work/edl.json")
    for src in (Path(args.out), main_edl):
        if src.exists():
            try:
                prev_edl = json.loads(src.read_text(encoding="utf-8"))
            except ValueError:
                prev_edl = {}
            break
    prev = prev_edl.get("params", {})
    shorts_file = Path("02-work/shorts.json")
    if args.variant and shorts_file.exists() and any(
            s["name"] == args.variant for s in json.loads(shorts_file.read_text(encoding="utf-8"))["shorts"]):
        # A short (shorts.py) is its own video: not the long one's chapters or montage block.
        prev_edl = {k: v for k, v in prev_edl.items() if k not in ("chapters", "montage")}
        prev_edl["segments"] = [sg for sg in prev_edl.get("segments", []) if sg.get("role") != "montage"]
        prev_edl["music"] = [mu for mu in prev_edl.get("music", []) if mu.get("block") != "montage"]
    keep_given = args.keep is not None
    format_given = args.format is not None
    for k, v in DEFAULTS.items():
        if k == "size" and format_given:
            continue                           # a new --format replaces a saved --size
        if getattr(args, k) is None:
            setattr(args, k, prev.get(k, v))
    tsig = transcript_sig(tr)
    if not keep_given and args.keep != "auto" and prev.get("transcript_sig") not in (None, tsig):
        # The transcript changed (a file redone, a glossary): line numbers may have moved.
        # Find the same moments again by file and time, instead of keeping stale numbers.
        args.keep = remap_keep(prev.get("kept_spans") or [], tr)
    material = {}
    if (ingest / "material.json").exists():
        material = {m["name"]: m for m in json.loads((ingest / "material.json").read_text(encoding="utf-8"))["files"]}
    sents = {s["line"]: s for s in tr["sentences"]}
    order = parse_keep(args.keep, tr["sentences"], tr.get("silences"))
    keep = [x for x in order if isinstance(x, int)]
    keep_sil = [x for x in order if isinstance(x, str)]
    # Where each named silence goes: after the lines listed before it.
    sil_at = [(x, sum(isinstance(y, int) for y in order[:i])) for i, x in enumerate(order) if isinstance(x, str)]
    sil_by_id = {x["id"].upper(): x for x in tr.get("silences") or []}
    args.transcript_sig = tsig
    args.kept_spans = [{"id": f"L{x}", "file": sents[x]["file"], "start": sents[x]["start"], "end": sents[x]["end"]}
                       if isinstance(x, int) else {"id": x, "file": sil_by_id[x]["file"], "start": sil_by_id[x]["start"],
                                                   "end": sil_by_id[x]["end"]} for x in order]
    prot_ranges = prot["ranges"]
    if args.variant:
        # Protections are the main edit's: a variant takes only the ones next to its own lines.
        prot_ranges = [r for r in prot_ranges if any(
            s["file"] == r["file"] and s["start"] <= r["end"] + 0.3 and s["end"] >= r["start"] - 0.3
            for s in args.kept_spans)]
    scale, default_pause = PACE[args.pace]
    max_pause = args.max_pause if args.max_pause is not None else default_pause

    # Every word of every file, in source order, so neighbors (kept or not) are known.
    words: dict[str, list[dict]] = {}
    for name, f in tr["files"].items():
        words[name] = sorted((w for s in f["segments"] for w in s["words"]), key=lambda w: w["start"])
        for i, w in enumerate(words[name]):
            w["_i"] = i

    def in_span(w, spans):
        return any(w["start"] < e and w["end"] > s for s, e, _ in spans)

    kept: list[tuple[int, dict, int]] = []      # (line, word, its place in --keep)
    dropped_fillers = 0
    for j, n in enumerate(keep):
        s = sents[n]
        for w in words[s["file"]]:
            if w["start"] >= s["start"] - 1e-3 and w["end"] <= s["end"] + 1e-3:
                if args.fillers == "drop" and in_span(w, s.get("fillers", [])):
                    dropped_fillers += 1
                    continue
                kept.append((n, w, j))
    if not kept:
        die("nothing to keep")

    # Runs of contiguous words: same file, consecutive in the source, short gap.
    runs: list[list[tuple[int, dict, int]]] = []
    file_of = {id(w): sents[n]["file"] for n, w, _ in kept}
    for item in kept:
        if runs:
            pw = runs[-1][-1][1]
            same = file_of[id(pw)] == file_of[id(item[1])]
            if same and item[1]["_i"] == pw["_i"] + 1 and item[1]["start"] - pw["end"] <= max_pause:
                runs[-1].append(item)
                continue
        runs.append([item])
    # A word too short to stand as its own cut ("Si" … pause … "necesitás plata") joins its
    # neighbor in the same take when the pause between them is short; after a long pause it
    # stays on its own (widened below), so the dead air isn't kept.
    def short(r):
        return r[-1][1]["end"] - r[0][1]["start"] < MIN_SEGMENT_S
    merged: list[list[tuple[int, dict, int]]] = []
    for r in runs:
        p = merged[-1] if merged else None
        if p and file_of[id(p[-1][1])] == file_of[id(r[0][1])] and r[0][1]["_i"] == p[-1][1]["_i"] + 1 \
                and r[0][1]["start"] - p[-1][1]["end"] <= SHORT_JOIN_S and (short(p) or short(r)):
            p += r
        else:
            merged.append(r)
    runs = merged

    dbs: dict[str, np.ndarray] = {}
    srcs: dict[str, str] = {}
    for name in {file_of[id(r[0][1])] for r in runs}:
        srcs[name] = (material.get(name) or {}).get("file") or die(f"no source path for {name} in material.json")
        dbs[name] = quiet_db(srcs[name], tr["files"][name].get("track", 0))

    def sound_start(name: str, i: int) -> float:
        """Where word i's sound really starts: the end of the silence the audio shows before it
        (Whisper's edges can be a quarter second off, and they usually include some air)."""
        ws, w = words[name], words[name][i]
        v = valley(dbs[name], ws[i - 1]["end"] if i > 0 else w["start"] - 0.3, w["start"])
        return v[1] if v and v[1] <= w["end"] else w["start"]

    def sound_end(name: str, i: int) -> float:
        ws, w = words[name], words[name][i]
        v = valley(dbs[name], w["end"], ws[i + 1]["start"] if i + 1 < len(ws) else w["end"] + 0.3)
        return v[0] if v and v[0] >= w["start"] else w["end"]

    # Clean silence available around each run, from the audio: before its first word (after
    # the previous word's tail, kept or dropped) and after its last word.
    info = []
    for run in runs:
        name = file_of[id(run[0][1])]
        ws, first, last = words[name], run[0][1], run[-1][1]
        s0, e0 = sound_start(name, first["_i"]), sound_end(name, last["_i"])
        before = sound_end(name, first["_i"] - 1) + TAIL_S if first["_i"] > 0 else 0.0
        after = sound_start(name, last["_i"] + 1) - 0.02 if last["_i"] + 1 < len(ws) \
            else tr["files"][name]["duration"]
        info.append({"name": name, "first": first, "last": last, "s0": s0, "e0": e0,
                     "lead_avail": max(s0 - before, 0.0), "tail_avail": max(after - e0, 0.0)})

    # The pause at each join sounds like speech: longer after a sentence than after a comma,
    # short where a filler was taken out mid-phrase. It's taken from wherever there's clean
    # silence — mostly after the previous phrase, only a little before the next word.
    lead = [min(i["lead_avail"], START_LEAD_S) for i in info]
    tail = [min(i["tail_avail"], END_TAIL_S) for i in info]
    for k in range(len(info) - 1):
        a, b = info[k], info[k + 1]
        follows = a["name"] == b["name"] and b["first"]["_i"] > a["last"]["_i"]
        g = GAPS[join_kind(a["last"]["word"], follows)] * scale
        lead[k + 1] = min(b["lead_avail"], min(max(g * 0.3, 0.04), 0.10))
        tail[k] = min(a["tail_avail"], g - lead[k + 1])
        if tail[k] < g - lead[k + 1]:
            lead[k + 1] = min(b["lead_avail"], g - tail[k])

    segments = []
    for run, i, ld, tl in zip(runs, info, lead, tail):
        name, s0, e0 = i["name"], i["s0"], i["e0"]
        src = srcs[name]
        # Edges measured on the audio, so a misplaced timestamp neither leaves a fragment of a
        # dropped word at the cut ("…no") nor eats the air the join needs.
        t_in = snap(s0 - ld, s0 - i["lead_avail"], s0 - min(0.01, ld), dbs[name])
        t_out = snap(e0 + tl, e0 + min(0.01, tl), e0 + i["tail_avail"], dbs[name])
        # On the source's frame grid, as render.py cuts: so subtitles, graphics, b-roll and
        # sound effects, which read these values, land on the same frames the render does.
        fr = (material.get(name) or {}).get("fps")
        if t_out - t_in < MIN_SEGMENT_S:
            # A short word on its own: widen it into the clean silence around it, after first.
            need = MIN_SEGMENT_S + 0.01 - (t_out - t_in)
            grow = min(need, max(e0 + i["tail_avail"] - t_out, 0.0))
            t_out += grow
            t_in -= min(need - grow, max(t_in - (s0 - i["lead_avail"]), 0.0))
        if fr:
            t_in, t_out = (round(round(t * fr) / fr, 4) for t in (t_in, t_out))
        if t_out - t_in < MIN_SEGMENT_S - 1e-3:
            print(f"  left out (too short to cut alone): «{''.join(w['word'] for _, w, _ in run).strip()}» "
                  f"{name} {s0:.2f}s — keep it with a neighbor if it's content")
            continue
        segments.append({"source": src, "name": name, "in": t_in, "out": t_out,
                         "lines": sorted({n for n, _, _ in run}), "words": [run[0][1]["_i"], run[-1][1]["_i"]],
                         "text": "".join(w["word"] for _, w, _ in run).strip(), "_j": run[0][2]})
    # A first line that comes later in the material is a cold open (hook).
    if segments and len(keep) > 1 and keep[0] > min(keep[1:]) and segments[0]["_j"] == 0:
        segments[0]["role"] = "hook"
    # Silent stretches to keep: the ones named in --keep, and with --pace natural every one
    # next to a kept line (the walk before the answer, the look after the line).
    kept_lines = set(keep)
    sil_keep = list(keep_sil)
    if args.pace == "natural":
        by_file = {}
        for x in tr["sentences"]:
            by_file.setdefault(x["file"], []).append(x)
        for x in tr.get("silences", []):
            ls = by_file.get(x["file"], [])
            prev_l = max((y for y in ls if y["end"] <= x["start"] + 1e-3), key=lambda y: y["end"], default=None)
            next_l = min((y for y in ls if y["start"] >= x["end"] - 1e-3), key=lambda y: y["start"], default=None)
            if (prev_l and prev_l["line"] in kept_lines) or (next_l and next_l["line"] in kept_lines):
                if x["id"] not in sil_keep:
                    sil_keep.append(x["id"])
    sil_by_id = {x["id"]: x for x in tr.get("silences", [])}
    for sid in sil_keep:
        f = sil_by_id[sid]["file"]
        srcs.setdefault(f, (material.get(f) or {}).get("file") or die(f"no source for {f}"))
    # A silence named in --keep plays where it's listed ("L4,S1,L1": the walk, then L1) and joins
    # the neighbor it continues in the source. Already inside a kept segment: nothing to add.
    for sid, m in sil_at:
        x = sil_by_id[sid]
        if any(sg["name"] == x["file"] and sg["in"] <= x["start"] + 0.05 and sg["out"] >= x["end"] - 0.05
               for sg in segments):
            continue
        pos = next((k for k, sg in enumerate(segments) if sg["_j"] >= m), len(segments))
        segments.insert(pos, {"source": srcs[x["file"]], "name": x["file"], "in": x["start"], "out": x["end"],
                              "lines": [], "_j": m - 0.5, "_sil": True})
    merge_forward(segments)
    # Silences --pace natural adds, and protections: in source order, extending what they touch.
    ranges = [{"id": "", "what": sid, "file": sil_by_id[sid]["file"], "start": sil_by_id[sid]["start"],
               "end": sil_by_id[sid]["end"]} for sid in sil_keep if sid not in keep_sil]
    ranges += [{**r, "what": r["what"]} for r in prot_ranges]
    for r in ranges:
        srcs.setdefault(r["file"], (material.get(r["file"]) or {}).get("file") or die(f"no source for {r['file']}"))
    notes = [f"{sid} where listed" for sid in dict.fromkeys(keep_sil)] + apply_protection(segments, ranges, srcs)
    for sg in segments:
        sg.pop("_j", None)
        sg.pop("_sil", None)
    for sg in segments:
        fr = (material.get(sg["name"]) or {}).get("fps")
        if fr:
            sg["in"], sg["out"] = (round(round(t * fr) / fr, 4) for t in (sg["in"], sg["out"]))
        ws = words.get(sg["name"], [])
        inside = [w for w in ws if sg["in"] <= (w["start"] + w["end"]) / 2 <= sg["out"]]
        if inside:
            sg["words"] = [inside[0]["_i"], inside[-1]["_i"]]
            sg["text"] = "".join(w["word"] for w in inside).strip()
            sg["lines"] = sorted({x["line"] for x in tr["sentences"] if x["file"] == sg["name"]
                                  and x["start"] < sg["out"] and x["end"] > sg["in"]})
        else:
            sg.pop("words", None)
            sg["text"] = "(no speech)"

    first_src = material.get(segments[0]["name"], {})
    if args.size:
        w, h = map(int, args.size.lower().split("x"))
    elif args.format == "source":
        w, h = first_src.get("display_width", 1920), first_src.get("display_height", 1080)
    else:
        w, h = FORMATS[args.format]
    for s in segments:                         # the audio track the transcript (and cleanup) used
        t = tr["files"].get(s["name"], {}).get("track", 0)
        if t:
            s["track"] = t
    # A montage block (montage.py --where start|end) isn't made from lines: it comes back as built.
    mon = prev_edl.get("montage") or {}
    if mon.get("where") in ("start", "end") and mon.get("segments"):
        block = [dict(s) for s in mon["segments"]]
        segments = block + segments if mon["where"] == "start" else segments + block
        print(f"kept the montage block ({len(block)} shots, at the {mon['where']}): montage.py build again to change it")
    # Time effects, J/L cuts and transitions belong to a piece of the material: a segment that
    # still covers most of the same piece keeps them through the re-cut.
    # One old segment → at most one new one (the one sharing most of it, at least half of each), so
    # a segment split in two doesn't fade out or transition twice.
    # Montage shots keep their own fields (the block comes back as built): their mute never lands
    # on a spoken segment of the same file. Equal candidates (a hook and its place): the one in
    # the same place in the edit.
    kept_fx, taken = 0, {}
    for oi, old in enumerate(prev_edl.get("segments", [])):
        if old.get("role") == "montage" or not any(k in old for k in CARRY):
            continue
        olap = lambda sg: min(old["out"], sg["out"]) - max(old["in"], sg["in"])  # noqa: E731
        cands = [i for i, sg in enumerate(segments) if sg["name"] == old.get("name") and sg.get("role") != "montage"
                 and olap(sg) >= 0.5 * max(sg["out"] - sg["in"], old["out"] - old["in"])]
        if not cands:
            continue
        score = lambda i: (round(olap(segments[i]), 3), -abs(i - oi))  # noqa: E731
        i = max(cands, key=score)
        if i not in taken or score(i) > taken[i][0]:
            taken[i] = (score(i), old)
    for i, (_, old) in taken.items():
        sg = segments[i]
        sg.update({k: old[k] for k in CARRY if k in old})
        if sg.get("hold") and abs(sg["in"] - old["in"]) > 1e-3 and not sg.get("reverse"):
            # Freezes sit at a moment of the material: same moment, new start.
            n = sg["out"] - sg["in"]
            sg["hold"] = [[round(min(max(a + old["in"] - sg["in"], 0), n), 3), d] for a, d in sg["hold"]]
        keys = [q[0] for r in sg.get("redact", []) for q in r.get("keys", [])]
        if keys and (min(keys) > sg["in"] + 0.1 or max(keys) < sg["out"] - 0.1):
            print(f"WARNING: segment {i + 1} now runs {sg['in']:.2f}–{sg['out']:.2f} s, past its tracked blur "
                  f"({min(keys):.2f}–{max(keys):.2f} s): the box stays at its edge position there — check it, "
                  f"or redact.py {i + 1} --box … again")
        kept_fx += 1
    if kept_fx:
        print(f"kept the time effects / transitions of {kept_fx} segment(s) from the previous edit")
    edl = {"version": 1, "output": {"width": w, "height": h, "fps": first_src.get("fps", 30)},
           "made_by": "cutlist", "params": vars(args), "segments": segments}
    carried = {k: v for k, v in prev_edl.items() if k not in edl}
    edl.update(carried)
    if carried:
        print("kept from the previous edit: " + ", ".join(
            f"{k} ({len(v)})" if isinstance(v, list) else k for k, v in carried.items()))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(edl, indent=1, ensure_ascii=False), encoding="utf-8")

    total_src = sum(f["duration"] for f in tr["files"].values())
    total = sum(seg_time(s) for s in segments)
    dropped_lines = [s["line"] for s in tr["sentences"] if s["line"] not in keep and not any(
        sg["name"] == s["file"] and sg["in"] <= s["start"] and sg["out"] >= s["end"] for sg in segments)]
    print(f"{len(segments)} segments · {tc(total_src)} of material → {tc(total)} · pace {args.pace} · "
          f"fillers {args.fillers} · lines left out: {dropped_lines or 'none'}")
    for s in segments:
        # Montage shots (the re-inserted block) have no lines or text.
        what = f"L{','.join(map(str, s['lines']))}  {s.get('text', '')[:70]}" if s.get("lines") is not None \
            else f"({s.get('role', 'shot')})"
        print(f"  [{tc(s['in'])}–{tc(s['out'])}] {s['name']}  {what}")
    for n in notes:
        print("  kept:", n)
    # What the edit leaves out, so it can be checked and said in the delivery.
    out_fill = [(w["start"], w["word"].strip()) for name, ws in words.items() for w in ws
                if not any(sg["name"] == name and sg["in"] <= (w["start"] + w["end"]) / 2 <= sg["out"]
                           for sg in segments) and any(
                    x["file"] == name and x["line"] in kept_lines and any(
                        f[0] <= w["start"] + 1e-3 and w["end"] <= f[1] + 1e-3 for f in x.get("fillers", []))
                    for x in tr["sentences"])]
    if out_fill:
        print("  fillers out:", ", ".join(f"«{t}» {tc(a)}" for a, t in out_fill))
    for x in tr.get("silences", []):
        cov = sum(max(0.0, min(sg["out"], x["end"]) - max(sg["in"], x["start"]))
                  for sg in segments if sg["name"] == x["file"])
        if 0 < cov < (x["end"] - x["start"]) - 0.2 or (cov == 0 and any(
                sg["name"] == x["file"] and sg["in"] < x["start"] for sg in segments) and any(
                sg["name"] == x["file"] and sg["out"] > x["end"] for sg in segments)):
            print(f"  {x['id']} (no speech, {x['end'] - x['start']:.1f} s at {tc(x['start'])}) shortened to "
                  f"{cov:.1f} s — if it's action or a meaningful pause, keep it: --keep …,{x['id']} or --protect "
                  f"{x['id']}")
    print(f"edl → {out}")


if __name__ == "__main__":
    main()
