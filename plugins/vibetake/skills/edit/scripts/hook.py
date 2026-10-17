# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""Rank transcript lines that could open the video as a hook (cold open).

    uv run hook.py [--top 5] [--ingest 01-ingest]

A good feed hook is short (1.5–4 s), makes sense on its own, and opens a loop: a
question, a promise, a conflict, a number, talking to "you". This ranks lines by
those signals (word lists per language in references/hooks.json) and writes
02-work/hooks.md. It only shortlists: the choice is an editorial judgment — read
the candidates in context. Then put the chosen line first:

    uv run cutlist.py --keep "L9,L1-L12"     # L9 opens; also kept in place as the payoff
    uv run cutlist.py --keep "L9,L1-L8,L10-L12"   # moved: it only opens
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import tc  # noqa: E402

HOOKS = Path(__file__).resolve().parents[1] / "references" / "hooks.json"


def norm(t: str) -> str:
    t = unicodedata.normalize("NFD", t.lower())
    return re.sub(r"[^\w\s?']", " ", "".join(c for c in t if unicodedata.category(c) != "Mn"))


def score(text: str, dur: float, lists: dict) -> tuple[float, list[str]]:
    n = f" {norm(text)} "
    s, why = 0.0, []
    if 1.5 <= dur <= 4.5:
        s, why = s + 2, why + ["short"]
    elif dur <= 6:
        s += 1
    else:
        s, why = s - 1, why + ["long"]
    if text.strip().endswith("?"):
        s, why = s + 2, why + ["question"]
    if re.search(r"\d", text):
        s, why = s + 1, why + ["number"]
    for kind in ("promise", "conflict", "curiosity"):
        if any(f" {norm(w).strip()} " in n for w in lists.get(kind, [])):
            s, why = s + 1.5, why + [kind]
    if any(f" {norm(w).strip()} " in n for w in lists.get("you", [])):
        s, why = s + 0.5, why + ["talks to you"]
    first = n.split()[0] if n.split() else ""
    if first in {norm(w).strip() for w in lists.get("continuation", [])}:
        s, why = s - 1.5, why + [f"starts with '{first}' (needs context)"]
    if not text.strip().endswith((".", "?", "!", "…")):
        s, why = s - 1, why + ["unfinished"]
    return s, why


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--ingest", default="01-ingest")
    ap.add_argument("--out", default="02-work/hooks.md")
    args = ap.parse_args()

    tr = json.loads((Path(args.ingest) / "transcript.json").read_text(encoding="utf-8"))
    lang = next(iter(tr["files"].values()))["language"]
    data = json.loads(HOOKS.read_text(encoding="utf-8"))
    lists = data.get(lang) or data.get(lang.split("-")[0]) or {}
    words = {name: [w for s in f["segments"] for w in s["words"]] for name, f in tr["files"].items()}

    ranked = []
    for s in tr["sentences"]:
        if s.get("retake_of"):
            continue
        filler = s.get("fillers", [])
        ws = [w for w in words[s["file"]] if s["start"] - 1e-3 <= w["start"] and w["end"] <= s["end"] + 1e-3
              and not any(w["start"] < e and w["end"] > b for b, e, _ in filler)]
        if not ws:
            continue
        text = "".join(w["word"] for w in ws).strip()
        dur = ws[-1]["end"] - ws[0]["start"]
        sc, why = score(text, dur, lists)
        if s.get("low_confidence"):
            sc, why = sc - 1, why + ["low confidence"]
        ranked.append((sc, s["line"], dur, text, why, s["file"], s["start"]))
    ranked.sort(key=lambda r: -r[0])

    lines = ["# Hook candidates", "",
             "Ranked by signals only — choose by reading them in context. A hook must make sense on its own.", ""]
    for sc, line, dur, text, why, name, start in ranked[:args.top]:
        lines.append(f"- **L{line}** ({sc:+.1f}, {dur:.1f} s, {name} {tc(start)}): {text}  — {', '.join(why)}")
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
