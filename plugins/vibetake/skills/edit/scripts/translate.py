# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""Translated subtitles: the delivered .srt in another language, same timing.

    uv run translate.py export --to en [--variant precio]    # → 02-work/translate_en.json to fill in
    uv run translate.py apply --to en [--variant precio]     # → 03-delivery/<video>.en.srt

export: the delivered .srt's cues with their times, each with the whole sentence it belongs to
(context: a cue is often half a sentence). Fill every cue's "to" with its translation — you
translate, no service. Read the whole first; inside one sentence, words can move between its
cues so each reads naturally in the target language, but every cue keeps its own time.
apply: checks every cue is translated, wraps each to two lines of the target writing system,
flags cues that read too fast for it (condense those: subtitles are a summary of speech, not a
transcript), and writes <video>[_<variant>].<lang>.srt — the name YouTube and Meta take with its
language. A translation done for one cut belongs to it: after a re-cut, export again (what was
already translated is kept for cues with the same text).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import DELIVERY, SENTENCE_END, deliverable_name, die, load_json, work_paths, writing  # noqa: E402


def read_srt(p: Path) -> list[dict]:
    cues = []
    for block in re.split(r"\n\s*\n", p.read_text(encoding="utf-8-sig").strip()):
        ln = block.strip().splitlines()
        m = next((re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", x) for x in ln if "-->" in x), None)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        end = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        i = next(k for k, x in enumerate(ln) if "-->" in x)
        cues.append({"start": round(start, 3), "end": round(end, 3), "text": " ".join(ln[i + 1:]).strip()})
    return cues


def srt_time(t: float) -> str:
    ms = round(t * 1000)
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def sentences(cues: list[dict]) -> list[str]:
    """The sentence each cue belongs to (cues joined until one ends a sentence)."""
    out, cur = [], []
    for c in cues:
        cur.append(c)
        if c["text"].rstrip("\"'»)").endswith(SENTENCE_END) or c is cues[-1]:
            text = " ".join(x["text"] for x in cur)
            out += [text] * len(cur)
            cur = []
    return out


def wrap(text: str, wr: dict) -> list[str]:
    """Two balanced lines in the target writing system (no spaces: between characters)."""
    if len(text) <= wr["line"]:
        return [text]
    if not wr["join"]:
        mid = len(text) // 2
        return [text[:mid], text[mid:]]
    words = text.split()
    if len(words) < 2:
        return [text]
    best = min(range(1, len(words)), key=lambda i: abs(len(" ".join(words[:i])) - len(text) / 2))
    return [" ".join(words[:best]), " ".join(words[best:])]


def paths(args) -> tuple[Path, Path, Path]:
    edl_p = work_paths(args.variant)["edl"]
    edl = load_json(edl_p) if edl_p.exists() else None
    stem = f"{deliverable_name(edl)}{'_' + args.variant if args.variant else ''}"
    src = DELIVERY / f"{stem}.srt"
    lang = args.to.lower()
    return src, Path("02-work") / f"translate{'_' + args.variant if args.variant else ''}_{lang}.json", \
        DELIVERY / f"{stem}.{lang}.srt"


def cmd_export(args) -> None:
    src, work, _ = paths(args)
    if not src.exists():
        die(f"no {src}: subtitles.py first (the translation follows the delivered .srt)")
    cues = read_srt(src)
    done = {}
    if work.exists():                       # a re-cut: keep what was translated for the same text
        done = {c["text"]: c.get("to", "") for c in load_json(work)["cues"] if c.get("to")}
    sent = sentences(cues)
    data = {"from": src.name, "to_lang": args.to.lower(),
            "how": "Fill every cue's \"to\". Read it all first; within a sentence, words may move between its cues "
                   "so each reads naturally; keep each cue's time. Short and clear: a subtitle summarizes speech.",
            "cues": [{"i": i, "start": c["start"], "end": c["end"], "text": c["text"],
                      **({"sentence": s} if s != c["text"] else {}), "to": done.get(c["text"], "")}
                     for i, (c, s) in enumerate(zip(cues, sent), 1)]}
    work.parent.mkdir(parents=True, exist_ok=True)
    work.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    left = sum(1 for c in data["cues"] if not c["to"])
    print(f"{len(cues)} cues → {work} ({left} to translate into {args.to})")


def cmd_apply(args) -> None:
    src, work, dst = paths(args)
    if not work.exists():
        die(f"no {work}: translate.py export --to {args.to} first")
    data = load_json(work)
    if src.exists():
        # The times always come from the delivered .srt as it is now (a re-cut that only moved
        # things keeps the translation); a cue whose words changed needs translating again.
        cur = read_srt(src)
        if len(cur) != len(data["cues"]) or any(a["text"] != b["text"] for a, b in zip(cur, data["cues"])):
            die(f"{src.name} changed since the export (another cut?): export again — translations of unchanged cues stay")
        for c, now in zip(data["cues"], cur):
            c["start"], c["end"] = now["start"], now["end"]
    missing = [c["i"] for c in data["cues"] if not c.get("to", "").strip()]
    if missing:
        die(f"cues without a translation: {missing[:20]}{' …' if len(missing) > 20 else ''}")
    wr = writing(args.to)
    fast, long_ = [], []
    blocks = []
    for c in data["cues"]:
        text = " ".join(c["to"].split()) if wr["join"] else c["to"].strip()
        lines = wrap(text, wr)
        if len(lines) > 2 or any(len(x) > wr["line"] for x in lines):
            long_.append(c["i"])
        dur = max(c["end"] - c["start"], 0.01)
        if len(text.replace(" ", "")) / dur > wr["cps"] * 1.2:
            fast.append(c["i"])
        blocks.append(f"{c['i']}\n{srt_time(c['start'])} --> {srt_time(c['end'])}\n" + "\n".join(lines) + "\n")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("\n".join(blocks), encoding="utf-8")
    print(f"{dst} · {len(blocks)} cues in {args.to}")
    if fast:
        print(f"WARNING: cues {fast[:20]} read faster than {wr['cps']} characters/s in {args.to}: condense them "
              "(fewer words, same meaning) and apply again")
    if long_:
        print(f"WARNING: cues {long_[:20]} don't fit two lines of {wr['line']} characters: shorten them")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("export", "apply"):
        p = sub.add_parser(name)
        p.add_argument("--to", required=True, help="target language code: en, pt, fr, de, it, ja, zh, ar…")
        p.add_argument("--variant", help="a short or variant: its own .srt")
    args = ap.parse_args()
    {"export": cmd_export, "apply": cmd_apply}[args.cmd](args)


if __name__ == "__main__":
    main()
