# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""Several shorts from one long video: each a variant with its own cut, framing, captions, cover and copy.

    uv run shorts.py add --name precio --keep "L14-L22" [--hook L18] [--title "…"]
    uv run shorts.py list                      # every short: lines, length, what's made
    uv run shorts.py make [--only precio,demo] [--format vertical] [--pace tight] [--preview]
    uv run shorts.py remove --name precio

add: a short is a stretch of the transcript that stands on its own (one idea, a start that needs
nothing before it, an end that lands), optionally opened by a hook line from inside it (a cold
open: shown first, and again in its place). Retakes inside a range are left out (the kept take
stays). It checks what's cheap to get wrong — the length for a feed, a first line that leans on
something said before ("and", "so", "that…"), an end mid-thought, a hook too long to hook, two
shorts sharing lines — and stores it in 02-work/shorts.json. Names: letters, digits and hyphens.

make: for each short, the same chain as the main video with --variant <name>: cutlist.py (its
lines, --pace tight by default), reframe.py, subtitles.py, render.py → 03-delivery/
<video>_v<N>_<name>.mp4 (with --preview: 02-work/preview_<name>.mp4). Graphics, cover and copy
are made per short after it: graphics.py cues/render --variant <name>, covers with "short":
"<name>" in covers.json (→ cover_<name>_<platform>_<n>.jpg), copy in copy_<name>.txt; then
qa.py --variant <name>.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import SENTENCE_END, die, load_json, tool, work_paths  # noqa: E402

SHORTS = Path("02-work/shorts.json")
FEED = (15.0, 90.0)       # a short that holds attention in a feed; platforms allow more (references/platforms.md)
HARD_MAX = 180.0          # Reels, TikTok and Shorts all take up to 3 min
HOOK_MAX = 4.5
# First words that lean on something said before (a short starting there feels cut).
LEANS = {
    "es": ("y", "pero", "entonces", "eso", "esto", "esa", "ese", "esos", "esas", "además", "también", "o sea",
           "porque", "como te dije", "como dije", "lo que", "ahí", "por eso", "sino"),
    "en": ("and", "but", "so", "that", "this", "these", "those", "also", "because", "as i said", "which", "then",
           "it", "they"),
    "pt": ("e", "mas", "então", "isso", "isto", "também", "porque", "como eu disse", "aí", "por isso"),
}


def transcript(ingest: str) -> dict:
    p = Path(ingest) / "transcript.json"
    if not p.exists():
        die(f"no {p}: shorts are chosen from the transcript (ingest first)")
    return load_json(p)


def line_text(tr: dict, s: dict) -> str:
    words = [w for seg in tr["files"][s["file"]]["segments"] for w in seg["words"]
             if s["start"] - 1e-3 <= w["start"] and w["end"] <= s["end"] + 1e-3]
    return "".join(w["word"] for w in words).strip()


def expand(spec: str, sents: dict) -> list[int]:
    """L14-L22 → its lines without retakes (the kept take stays); a single line is taken as named."""
    out = []
    for part in (p.strip() for p in spec.split(",") if p.strip()):
        m = re.fullmatch(r"L?(\d+)(?:-L?(\d+))?", part, re.I)
        if not m:
            die(f"can't read '{part}' (use L3 or L3-L7)")
        a, b = int(m.group(1)), int(m.group(2) or m.group(1))
        rng = list(range(a, b + 1)) if a <= b else list(range(a, b - 1, -1))
        missing = [n for n in rng if n not in sents]
        if missing:
            die(f"lines not in the transcript: {missing}")
        out += rng if a == b else [n for n in rng if not sents[n].get("retake_of")]
    return out


def checks(sh: dict, tr: dict, sents: dict, others: list[dict]) -> tuple[float, list[str]]:
    """Estimated length (the cut's pauses included roughly) and what to look at."""
    lines = sh["lines"]
    est = sum(sents[n]["end"] - sents[n]["start"] for n in lines) + 0.3 * max(len(lines) - 1, 0)
    warn = []
    if sh.get("hook"):
        h = sents[sh["hook"]]
        if sh["hook"] == lines[0]:
            warn.append(f"the hook L{sh['hook']} is already the first line: it opens the short once, not twice "
                        "(pick a line from further in to open with)")
        else:
            est += h["end"] - h["start"] + 0.3
        if sh["hook"] not in lines:
            warn.append(f"the hook L{sh['hook']} isn't in the short's lines: it opens the short and isn't heard "
                        "again in its place (fine if on purpose)")
        if h["end"] - h["start"] > HOOK_MAX:
            warn.append(f"the hook L{sh['hook']} lasts {h['end'] - h['start']:.1f} s: a hook works in 1.5–4 s")
    if est > HARD_MAX:
        warn.append(f"~{est:.0f} s: over the 3 min every platform takes — split it")
    elif not FEED[0] <= est <= FEED[1]:
        warn.append(f"~{est:.0f} s: a feed short usually lands between {FEED[0]:.0f} and {FEED[1]:.0f} s")
    lang = (tr.get("lang") or next((f.get("language") for f in tr["files"].values() if f.get("language")), "")
            or "").split("-")[0]
    first = line_text(tr, sents[lines[0]]).lower().lstrip("¿¡\"'«(")
    lean = next((w for w in LEANS.get(lang, ()) if re.match(rf"{re.escape(w)}\b", first)), None)
    if lean:
        warn.append(f"L{lines[0]} starts with “{lean}”: does it need what was said before? (start a line earlier, "
                    "or open with the hook)")
    last = line_text(tr, sents[lines[-1]]).rstrip("\"'»)")
    if last and not last.endswith(SENTENCE_END):
        warn.append(f"L{lines[-1]} doesn't end a sentence: does the short land there?")
    mine = set(lines)
    for o in others:
        shared = mine & set(o["lines"])
        if shared and o["name"] != sh["name"]:
            warn.append(f"shares {len(shared)} line(s) with '{o['name']}' (fine if on purpose: the same idea twice)")
    return est, warn


def load() -> dict:
    return load_json(SHORTS) if SHORTS.exists() else {"shorts": []}


def save(data: dict) -> None:
    SHORTS.parent.mkdir(parents=True, exist_ok=True)
    SHORTS.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def cmd_add(args) -> None:
    if not re.fullmatch(r"[A-Za-z0-9-]+", args.name):
        die("--name: letters, digits and hyphens (it becomes part of the file names)")
    tr = transcript(args.ingest)
    sents = {s["line"]: s for s in tr["sentences"]}
    lines = expand(args.keep, sents)
    if not lines:
        die(f"{args.keep}: every line in it is a retake (the kept takes are elsewhere): name the lines to keep")
    hook = int(args.hook.lstrip("Ll")) if args.hook else None
    if hook and hook not in sents:
        die(f"L{hook} isn't in the transcript")
    data = load()
    others = [s for s in data["shorts"] if s["name"] != args.name]
    sh = {"name": args.name, "keep": args.keep, "lines": lines, **({"hook": hook} if hook else {}),
          **({"title": args.title} if args.title else {})}
    est, warn = checks(sh, tr, sents, others)
    sh["estimate_s"] = round(est, 1)
    data["shorts"] = others + [sh]
    save(data)
    print(f"short '{args.name}': {len(lines)} line(s){f', hook L{hook}' if hook else ''}, ~{est:.0f} s")
    print(f"  starts: {line_text(tr, sents[lines[0]])[:90]}")
    print(f"  ends:   {line_text(tr, sents[lines[-1]])[-90:]}")
    for w in warn:
        print(f"WARNING: {w}")


def made(name: str) -> dict:
    p = work_paths(name)
    # By version number: _v10_ is newer than _v9_ (as text it sorts first).
    vids = sorted(Path("03-delivery").glob(f"*_v*_{name}.mp4"), key=lambda p: [
        int(m) for m in re.findall(rf"_v(\d+)_{re.escape(name)}\.mp4$", p.name)] or [-1]) \
        if Path("03-delivery").exists() else []
    return {"cut": p["edl"].exists(), "video": vids[-1].name if vids else None,
            "srt": any(Path("03-delivery").glob(f"*_{name}.srt")) if Path("03-delivery").exists() else False,
            "covers": len(list(Path("03-delivery").glob(f"cover_{name}_*.jpg"))) if Path("03-delivery").exists() else 0,
            "copy": Path(f"03-delivery/copy_{name}.txt").exists()}


def cmd_list(args) -> None:
    data = load()
    if not data["shorts"]:
        print("no shorts yet: shorts.py add --name … --keep L…")
        return
    for sh in data["shorts"]:
        m = made(sh["name"])
        hook = f" (hook L{sh['hook']})" if sh.get("hook") else ""
        print(f"- {sh['name']}: {sh['keep']}{hook}"
              f" ~{sh['estimate_s']:.0f} s · video {m['video'] or '—'} · srt {'yes' if m['srt'] else '—'} · "
              f"covers {m['covers']} · copy {'yes' if m['copy'] else '—'}")


def run(script: str, *a: str) -> None:
    print(f"$ {script} {' '.join(a)}", flush=True)
    p = subprocess.run([tool("uv"), "run", str(Path(__file__).with_name(script)), *a])
    if p.returncode:
        die(f"{script} failed for this short (see above)")


def cmd_make(args) -> None:
    data = load()
    todo = [s for s in data["shorts"] if not args.only or s["name"] in args.only.split(",")]
    if not todo:
        die("no shorts to make (shorts.py add first, or check --only)")
    for sh in todo:
        n = sh["name"]
        print(f"\n== {n}")
        # A hook that's already the first line opens it once: not twice back to back.
        hook = [sh["hook"]] if sh.get("hook") and sh["hook"] != sh["lines"][0] else []
        if sh.get("hook") and not hook:
            print(f"WARNING: the hook L{sh['hook']} is already the first line: not repeated")
        keep = ",".join(f"L{x}" for x in hook + sh["lines"])
        run("cutlist.py", "--variant", n, "--keep", keep, "--format", args.format, "--pace", args.pace)
        run("reframe.py", "--variant", n)
        run("subtitles.py", "--variant", n)
        paths = work_paths(n)
        if paths["captions"].exists():
            # Captions drawn by the graphics layer: the short gets its own layer. Its graphics are
            # planned for it (graphics_<name>.json), never the long video's: none until then.
            if Path("02-work/design.json").exists() and Path("02-work/remotion").exists():
                if not paths["plan"].exists():
                    paths["plan"].write_text('{"elements": []}', encoding="utf-8")
                run("graphics.py", "cues", "--variant", n)
                run("graphics.py", "render", "--variant", n)
            else:
                print("no design/Remotion project yet: captions burned in instead (subtitles --engine ass)")
                paths["captions"].unlink()
                run("subtitles.py", "--variant", n, "--engine", "ass")
        if args.preview:
            run("render.py", "--variant", n, "--preview", "--out", f"02-work/preview_{n}.mp4", "--allow-stale")
        else:
            run("render.py", "--variant", n)
    print("\nnext, per short: look at it; graphics.py cues/render --variant <name> if the video has graphics; "
          "covers (\"short\": \"<name>\" in covers.json); copy_<name>.txt; qa.py --variant <name>")


def cmd_remove(args) -> None:
    data = load()
    data["shorts"] = [s for s in data["shorts"] if s["name"] != args.name]
    save(data)
    print(f"'{args.name}' removed from the list (its files stay: delete them if you want)")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ingest", default="01-ingest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("--name", required=True)
    a.add_argument("--keep", required=True)
    a.add_argument("--hook")
    a.add_argument("--title")
    sub.add_parser("list")
    m = sub.add_parser("make")
    m.add_argument("--only")
    m.add_argument("--format", choices=("vertical", "square", "horizontal", "source"), default="vertical")
    m.add_argument("--pace", choices=("tight", "normal", "relaxed", "natural"), default="tight")
    m.add_argument("--preview", action="store_true")
    r = sub.add_parser("remove")
    r.add_argument("--name", required=True)
    args = ap.parse_args()
    {"add": cmd_add, "list": cmd_list, "make": cmd_make, "remove": cmd_remove}[args.cmd](args)


if __name__ == "__main__":
    main()
