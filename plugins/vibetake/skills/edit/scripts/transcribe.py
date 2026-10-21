# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["faster-whisper>=1.1"]
# ///
"""Verbatim, word-level transcription with faster-whisper, plus filler and retake marks.

    uv run transcribe.py <files...> --lang es [--glossary "Claude, Vibetake"] [--out 01-ingest]
                         [--model large-v3] [--track 0]
    uv run transcribe.py --rebuild-md --lang es [--out 01-ingest]   # redo marks only

Files are treated as one sequence in the order given (ingest.py orders them by
recording time), so a retake that spans two files is still found.

Writes transcript.json (words, segments, sentences with marks; used by scripts)
and transcript.md (read by the model):

    L3 [00:00.4–00:03.1] ⟨Bueno,⟩ hoy les voy a mos…   ↻ retake → L4
    L4 [00:03.9–00:06.0] Hoy les voy a mostrar el error.

⟨…⟩ marks filler candidates and ↻ marks possible retakes; both are suggestions for
the model to judge with context. With ingest's faces.json, a retake also says when one
take's picture is clearly softer, shakier, more blown out or darker than the other's. ⚠ marks low-confidence text (check it by ear).
GPU (CUDA float16) when available, otherwise the same model on CPU int8.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import SENTENCE_END, die, ffprobe, tc, tool  # noqa: E402

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

FILLERS = Path(__file__).resolve().parents[1] / "references" / "fillers.json"
PAUSE_LINE_S = 0.7      # pauses at least this long get their own line in transcript.md
SPLIT_PAUSE_S = 0.5     # a pause this long ends a sentence
MAX_WORDS = 20          # longer sentences are split at their longest internal pause
FILLER_PAUSE_S = 0.3    # a contextual filler followed by this much silence counts as filler
REACTION_PAUSE_S = 0.5  # a reaction word after this much silence, answering a question, is content
SILENT_ITEM_S = 1.5     # a stretch without speech this long becomes an item (S1, S2…) to keep or cut
RETAKE_WINDOW_S = 20
RETAKE_LOOKAHEAD = 4
# Two takes of the same line, compared with each other only (never against a fixed bar:
# a soft, dark or moving shot can be the intent, and then both takes share it).
SOFTER_RATIO = 0.6      # sharpness below 60% of the other take's
SHAKIER_RATIO = 2.0     # camera jitter at least twice the other take's…
SHAKE_MIN = 0.01        # …and at least 1% of the frame width between samples
BLOWN_PTS = 3.0         # percentage points more of blown-out picture
CRUSHED_PTS = 10.0      # percentage points more of black picture


# ------------------------------------------------------------- transcription

def add_cuda_dlls() -> None:
    """On Windows, CUDA/cuDNN come as pip wheels (nvidia-*); make their DLLs findable."""
    if os.name != "nt":
        return
    for base in map(Path, sys.path):
        nv = base / "nvidia"
        if nv.is_dir():
            for b in nv.glob("*/bin"):
                os.add_dll_directory(str(b))
                os.environ["PATH"] = f"{b}{os.pathsep}{os.environ['PATH']}"


def load_model(name: str):
    add_cuda_dlls()
    from faster_whisper import WhisperModel
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return WhisperModel(name, device="cuda", compute_type="float16"), "cuda float16"
    except Exception as e:  # missing CUDA libs, driver issues…
        print(f"GPU unavailable for transcription ({e.__class__.__name__}: {e}); using CPU int8", flush=True)
    return WhisperModel(name, device="cpu", compute_type="int8"), "cpu int8"


def lang_data(lang: str) -> dict:
    data = json.loads(FILLERS.read_text(encoding="utf-8"))
    return data.get(lang) or data.get(lang.split("-")[0]) or {"prompt": "", "always": [], "contextual": [],
                                                              "restart": []}


def transcribe_file(model, f: Path, args, ld: dict, track: int = 0) -> dict:
    dur = float(ffprobe(f).get("format", {}).get("duration") or 0)
    # Decode with ffmpeg ourselves: faster-whisper's own decoder (PyAV) breaks across versions.
    raw = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", str(f), "-map", f"0:a:{track}",
                          "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True, check=True).stdout
    import numpy as np
    audio = np.frombuffer(raw, np.int16).astype(np.float32) / 32768
    glossary = [g.strip() for g in (args.glossary or "").split(",") if g.strip()]
    # The prompt is punctuated verbatim speech (keeps fillers and punctuation) plus the glossary.
    prompt = " ".join(x for x in (ld["prompt"], ", ".join(glossary) + "." if glossary else "") if x) or None
    t0 = time.time()
    segs, _ = model.transcribe(
        audio, language=args.lang, word_timestamps=True, beam_size=5,
        initial_prompt=prompt, hotwords=", ".join(glossary) or None,
        condition_on_previous_text=False,             # avoids repeated-phrase loops
        vad_filter=True,                              # no invented text in long silences/music…
        vad_parameters={"min_silence_duration_ms": 1000, "speech_pad_ms": 400})  # …without eating soft words
    segments, next_pct = [], 10
    for s in segs:
        segments.append({"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip(),
                         "avg_logprob": round(s.avg_logprob, 3), "no_speech_prob": round(s.no_speech_prob, 3),
                         "compression_ratio": round(s.compression_ratio, 2),
                         "words": [{"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word,
                                    "probability": round(w.probability, 3)} for w in (s.words or [])]})
        pct = s.end / dur * 100 if dur else 0
        if pct >= next_pct:
            eta = (time.time() - t0) / pct * (100 - pct)
            print(f"{f.name}: {pct:.0f}% ({tc(s.end)} / {tc(dur)}) · ~{eta:.0f} s left", flush=True)
            next_pct = (int(pct) // 10 + 1) * 10
    print(f"{f.name}: done in {time.time() - t0:.0f} s", flush=True)
    tighten_words(segments, audio)
    return {"language": args.lang, "model": args.model, "track": track, "duration": dur,
            "glossary": glossary, "segments": segments}


def tighten_words(segments: list[dict], audio) -> None:
    """Whisper often stretches a word over the silence before or after it (a word after a
    pause "starts" in the middle of the pause). Trim each word's edges where the audio is
    silent, so word times can be cut on."""
    import numpy as np
    hop = 160                                            # 10 ms at 16 kHz
    n = len(audio) // hop
    if n < 10:
        return
    db = 20 * np.log10(np.sqrt((audio[:n * hop].reshape(n, hop) ** 2).mean(1)) + 1e-9)
    thr = min(max(float(np.percentile(db, 10)), -70.0) + 15, -30.0)   # digital silence reads as -180 dB
    silent = db < thr
    words = [w for s in segments for w in s["words"]]
    for i, w in enumerate(words):
        nxt = words[i + 1]["start"] if i + 1 < len(words) else n / 100
        # Start: skip silence, searching up to the next word (the word may sit entirely
        # after its reported span, as when Whisper lays it over the preceding pause).
        # A sound run shorter than 50 ms is the tail of the previous word, not this one.
        a, limit = int(w["start"] * 100), min(int(max(nxt, w["end"]) * 100), n)
        while a < limit and (silent[a] or not all(not x for x in silent[a:a + 5])):
            a += 1
        if a < limit and a / 100 - w["start"] > 0.1:
            dur = w["end"] - w["start"]
            w["start"] = round(a / 100, 3)
            if w["end"] <= w["start"]:
                w["end"] = round(min(nxt, w["start"] + dur) if nxt > w["start"] else w["start"] + 0.1, 3)
        # End: trim trailing silence.
        a, b = int(w["start"] * 100), min(int(w["end"] * 100), n)
        while b > a + 5 and silent[b - 1]:
            b -= 1
        if w["end"] - b / 100 > 0.1:
            w["end"] = round(b / 100, 3)
    for s in segments:
        if s["words"]:
            s["start"], s["end"] = s["words"][0]["start"], s["words"][-1]["end"]


# ------------------------------------------------------------------ analysis

def norm(text: str) -> str:
    t = unicodedata.normalize("NFD", text.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w\s']", " ", t).strip()


def split_long(words: list[dict]) -> list[list[dict]]:
    if len(words) <= MAX_WORDS:
        return [words]
    lo, hi = 3, len(words) - 3
    k = max(range(lo, hi), key=lambda i: words[i]["start"] - words[i - 1]["end"])
    return split_long(words[:k]) + split_long(words[k:])


def sentences_of(words: list[dict]) -> list[list[dict]]:
    out, cur = [], []
    for i, w in enumerate(words):
        cur.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if (nxt is None or w["word"].strip().endswith(SENTENCE_END)
                or nxt["start"] - w["end"] >= SPLIT_PAUSE_S):
            out += split_long(cur)
            cur = []
    return out


def is_reaction(words: list[dict], i: int, ld: dict, prev: dict | None) -> bool:
    """A filler word said as content, not hesitation: written as an exclamation or a
    question ("¡Ah!", "¿Eh?"), or a reaction word ("ah", "oh"…) starting the answer to a
    question after a pause ("¿Qué estás haciendo?" … "Ah, me agarraste"). A lone "Eh." is
    still hesitation."""
    raw = words[i]["word"].strip()
    if raw.endswith(("!", "?")) or raw.startswith(("¡", "¿")):
        return True
    if norm(words[i]["word"]) not in {norm(r) for r in ld.get("reactions", [])}:
        return False
    if i == 0 and prev is not None and words[0]["start"] - prev["end"] >= REACTION_PAUSE_S \
            and prev["word"].strip().endswith(("?", "!")):
        return True
    return False


def mark_fillers(words: list[dict], ld: dict, prev: dict | None = None) -> list[tuple[int, int]]:
    """Index spans [i, j) of filler candidates. prev: the word before this sentence, if any."""
    toks = [norm(w["word"]) for w in words]
    phrases = sorted((norm(p).split() for p in ld["always"]), key=len, reverse=True)
    contextual = {norm(c) for c in ld["contextual"]}
    spans, i = [], 0
    while i < len(words):
        hit = next((len(p) for p in phrases if toks[i:i + len(p)] == p), 0)
        if hit == 1 and is_reaction(words, i, ld, prev):
            hit = 0
        if not hit and toks[i] in contextual:
            raw = words[i]["word"].strip()
            nxt = words[i + 1] if i + 1 < len(words) else None
            pause = nxt is not None and nxt["start"] - words[i]["end"] >= FILLER_PAUSE_S
            if raw.endswith((",", "…", "...")) or pause:
                hit = 1
        if hit:
            spans.append((i, i + hit))
            i += hit
        else:
            i += 1
    return spans


def content_tokens(words: list[dict], spans: list[tuple[int, int]]) -> list[str]:
    skip = {k for a, b in spans for k in range(a, b)}
    return [t for k, w in enumerate(words) if k not in skip for t in norm(w["word"]).split()]


def is_retake(a: list[str], b: list[str], a_has_restart: bool) -> bool:
    """Is sentence a an abandoned or repeated take of the later sentence b?"""
    if len(a) < 2 or len(b) < 2:
        return False
    k = 0
    while k < min(len(a), len(b)) and a[k] == b[k]:
        k += 1
    # A truncated last word ("mos…" before "mostrar") still counts as matching.
    if k == len(a) - 1 and k < len(b) and len(a[k]) >= 2 and b[k].startswith(a[k]):
        k += 1
    if k >= 3 or (k >= 2 and (k == len(a) or a_has_restart)):
        return True
    return min(len(a), len(b)) >= 4 and SequenceMatcher(None, a, b).ratio() >= 0.7


def analyze(order: list[str], files: dict, ld: dict) -> list[dict]:
    restart = [norm(r) for r in ld["restart"]]
    sents: list[dict] = []
    for fi, name in enumerate(order):
        words = [w for s in files[name]["segments"] for w in s["words"]]
        weak = [(s["start"], s["end"]) for s in files[name]["segments"]
                if s["avg_logprob"] < -1.0 or s["compression_ratio"] > 2.4
                or (s["no_speech_prob"] > 0.6 and s["avg_logprob"] < -0.5)]
        prev = None
        for ws in sentences_of(words):
            spans = mark_fillers(ws, ld, prev)
            prev = ws[-1]
            start, end = ws[0]["start"], ws[-1]["end"]
            text_n = norm("".join(w["word"] for w in ws))
            sents.append({
                "file": name, "file_index": fi, "start": start, "end": end, "words": ws,
                "fillers": [[ws[a]["start"], ws[b - 1]["end"], "".join(w["word"] for w in ws[a:b]).strip()]
                            for a, b in spans],
                "tokens": content_tokens(ws, spans),
                "restart": any(re.search(rf"\b{re.escape(r)}\b", text_n) for r in restart),
                "low_confidence": any(s < end and e > start for s, e in weak),
                "_spans": spans,
            })
    for n, s in enumerate(sents, 1):
        s["line"] = n
    for i, a in enumerate(sents):
        for b in sents[i + 1:i + 1 + RETAKE_LOOKAHEAD]:
            same_file = a["file_index"] == b["file_index"]
            near = (b["start"] - a["end"] <= RETAKE_WINDOW_S) if same_file else b["file_index"] == a["file_index"] + 1
            if near and is_retake(a["tokens"], b["tokens"], a["restart"]):
                a["retake_of"] = b["line"]
                break
    return sents


def take_stats(samples: list, s: dict) -> dict | None:
    from statistics import median
    q = [x["q"] for x in samples if "q" in x and s["start"] <= x["t"] <= s["end"]]
    if len(q) < 2:
        return None
    faces = [v[0] for v in q if v[0] is not None]
    jitter = [((q[i][2] - q[i - 1][2]) ** 2 + (q[i][3] - q[i - 1][3]) ** 2) ** .5 for i in range(1, len(q))]
    return {"face": median(faces) if len(faces) * 2 >= len(q) else None, "frame": median(v[1] for v in q),
            "jitter": median(jitter), "blown": median(v[4] for v in q), "crushed": median(v[5] for v in q)}


def worse(x: dict, y: dict) -> list[str]:
    """What's clearly worse in take x than in take y of the same line."""
    key = "face" if x["face"] and y["face"] else "frame"
    out = []
    if x["blown"] - y["blown"] >= BLOWN_PTS:
        out.append("more blown out")
    if x["crushed"] - y["crushed"] >= CRUSHED_PTS:
        out.append("darker")
    # Blown or black areas have no detail: that's the exposure, not the focus.
    if not out and y[key] > 0 and x[key] < SOFTER_RATIO * y[key]:
        out.append("softer")
    if x["jitter"] >= max(SHAKIER_RATIO * y["jitter"], SHAKE_MIN):
        out.append("shakier")
    return out


def compare_takes(out: Path, sents: list[dict]) -> None:
    """Puts on each retake what's clearly worse in the picture of either take (faces.json)."""
    try:
        files = json.loads((out / "faces.json").read_text(encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError):
        return
    by_line = {s["line"]: s for s in sents}
    for a in sents:
        b = by_line.get(a.get("retake_of"))
        if not b or a["file"] not in files or b["file"] not in files:
            continue
        sa, sb = take_stats(files[a["file"]], a), take_stats(files[b["file"]], b)
        if sa and sb:
            a["take_picture"] = {"this": worse(sa, sb), "later": worse(sb, sa)}


def render(s: dict) -> str:
    ws, spans = s["words"], s["_spans"]
    open_at = {a for a, _ in spans}
    close_at = {b - 1 for _, b in spans}
    out = []
    for k, w in enumerate(ws):
        text = w["word"]
        lead = text[:len(text) - len(text.lstrip())]
        core = text.strip()
        if k in open_at:
            core = "⟨" + core
        if k in close_at:
            core = core + "⟩"
        out.append(lead + core)
    line = f"L{s['line']} [{tc(s['start'])}–{tc(s['end'])}] {''.join(out).strip()}"
    if s.get("retake_of"):
        line += f"   ↻ retake → L{s['retake_of']}"
        pic = s.get("take_picture") or {}
        notes = []
        if pic.get("later"):
            notes.append(f"L{s['retake_of']} {', '.join(pic['later'])}")
        if pic.get("this"):
            notes.append(f"this take {', '.join(pic['this'])}")
        if notes:
            line += f" ({'; '.join(notes)})"
    if s["low_confidence"]:
        line += "   ⚠ low confidence"
    return line


def write_outputs(out: Path, result: dict, ld: dict) -> None:
    sents = analyze(result["order"], result["files"], ld)
    compare_takes(out, sents)
    md = ["# Transcript", "",
          "⟨…⟩ filler candidate · ↻ possible retake (usually keep the later take; '(L12 softer…)' = its "
          "picture is clearly worse than the other take's) · ⚠ low confidence · "
          "S… a stretch without speech: action or a meaningful pause? look at the contact sheets; "
          "keep it like a line (--keep …,S2) or protect it", ""]
    if not (ld.get("always") or ld.get("contextual")):
        md += [f"No filler list for '{result.get('lang')}': fillers aren't marked — judge them yourself "
               "(references/fillers.json takes a new language).", ""]
    silences: list[dict] = []

    def gap(name: str, a: float, b: float) -> None:
        if b - a >= SILENT_ITEM_S:
            sid = f"S{len(silences) + 1}"
            silences.append({"id": sid, "file": name, "start": round(a, 3), "end": round(b, 3)})
            md.append(f"{sid} [{tc(a)}–{tc(b)}] (no speech, {b - a:.1f} s)")
        elif b - a >= PAUSE_LINE_S:
            md.append(f"   [{tc(a)}–{tc(b)}] (pause {b - a:.1f} s)")

    prev_file, prev_end = None, 0.0
    for s in sents:
        if s["file"] != prev_file:
            if prev_file is not None:
                gap(prev_file, prev_end, float(result["files"][prev_file].get("duration") or prev_end))
            md += ([""] if prev_file else []) + [f"## {s['file']}", ""]
            prev_file, prev_end = s["file"], 0.0
        gap(s["file"], prev_end, s["start"])
        md.append(render(s))
        prev_end = s["end"]
    if prev_file is not None:
        gap(prev_file, prev_end, float(result["files"][prev_file].get("duration") or prev_end))
    (out / "transcript.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    result["silences"] = silences
    result["sentences"] = [{k: v for k, v in s.items() if k not in ("words", "tokens", "_spans")} for s in sents]
    (out / "transcript.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


CUDA_WHEELS = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12>=9,<10"]


def ensure_cuda_libs() -> None:
    """With an NVIDIA GPU, faster-whisper needs cuBLAS/cuDNN; they come as pip wheels that
    this script's own environment doesn't include (they're heavy, useless without the GPU).
    If they're missing, run again through uv with them added — so every caller (ingest,
    qa, a person by hand) gets the GPU without remembering the flags."""
    if os.environ.get("VIBETAKE_CUDA_REEXEC") or not shutil.which("nvidia-smi"):
        return
    try:
        import nvidia.cublas  # noqa: F401
        import nvidia.cudnn  # noqa: F401
        return
    except ImportError:
        pass
    uv = shutil.which("uv")
    if not uv:
        return
    cmd = [uv, "run"]
    for w in CUDA_WHEELS:
        cmd += ["--with", w]
    env = {**os.environ, "VIBETAKE_CUDA_REEXEC": "1"}
    sys.exit(subprocess.run([*cmd, str(Path(__file__).resolve()), *sys.argv[1:]], env=env).returncode)


def main() -> None:
    ensure_cuda_libs()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--lang", required=True, help="language code; never auto-detect")
    ap.add_argument("--glossary", help="comma-separated names, brands and terms to spell right")
    ap.add_argument("--out", default="01-ingest")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--track", type=int, default=0, help="audio track index to transcribe")
    ap.add_argument("--track-for", action="append", default=[], metavar="NAME=N",
                    help="a file's own audio track (ingest passes the best one per file)")
    ap.add_argument("--force", action="store_true", help="transcribe again even if nothing changed")
    ap.add_argument("--rebuild-md", action="store_true", help="only recompute marks from transcript.json")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    ld = lang_data(args.lang)
    result_path = out / "transcript.json"
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        result = {"files": {}, "order": []}
    result.setdefault("order", list(result["files"]))

    if not args.rebuild_md:
        if not args.files:
            die("no files given")
        tracks = {k: int(v) for k, _, v in (x.rpartition("=") for x in args.track_for)}
        model = device = None
        for f in map(Path, args.files):
            track = tracks.get(f.name, args.track)
            # A file whose transcript was made with the same settings isn't transcribed again.
            sig = [f.stat().st_size, int(f.stat().st_mtime), args.lang, args.glossary or "", track, args.model]
            old = result["files"].get(f.name)
            if old and old.get("sig") == sig and not args.force:
                print(f"{f.name}: transcript up to date", flush=True)
                continue
            if model is None:
                print(f"loading {args.model} (first run downloads the model, ~3 GB)…", flush=True)
                model, device = load_model(args.model)
                print(f"model ready on {device}", flush=True)
            result["files"][f.name] = {**transcribe_file(model, f, args, ld, track), "device": device, "sig": sig}
            result_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        # Recording order is ingest's (it passes every file in that order); redoing some files
        # keeps the order they had, so line numbers don't move around.
        names = [Path(f).name for f in args.files]
        if set(names) >= set(result["order"]):
            result["order"] = names
        else:
            result["order"] = result["order"] + [n for n in names if n not in result["order"]]
    elif not result["files"]:
        die(f"nothing to rebuild: {result_path} is missing or empty")

    result["lang"] = args.lang
    if args.glossary or "glossary" not in result:
        result["glossary"] = args.glossary or ""
    write_outputs(out, result, ld)
    print(f"transcript → {out / 'transcript.md'}", flush=True)


if __name__ == "__main__":
    main()
