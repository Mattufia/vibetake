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
the model to judge with context. ⚠ marks low-confidence text (check it by ear).
GPU (CUDA float16) when available, otherwise the same model on CPU int8.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import die, ffprobe, tc, tool  # noqa: E402

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

FILLERS = Path(__file__).resolve().parents[1] / "references" / "fillers.json"
PAUSE_LINE_S = 0.7      # pauses at least this long get their own line in transcript.md
SPLIT_PAUSE_S = 0.5     # a pause this long ends a sentence
MAX_WORDS = 20          # longer sentences are split at their longest internal pause
FILLER_PAUSE_S = 0.3    # a contextual filler followed by this much silence counts as filler
RETAKE_WINDOW_S = 20
RETAKE_LOOKAHEAD = 4


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


def transcribe_file(model, f: Path, args, ld: dict) -> dict:
    dur = float(ffprobe(f).get("format", {}).get("duration") or 0)
    # Decode with ffmpeg ourselves: faster-whisper's own decoder (PyAV) breaks across versions.
    raw = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", str(f), "-map", f"0:a:{args.track}",
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
    return {"language": args.lang, "model": args.model, "track": args.track, "duration": dur,
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
        if (nxt is None or w["word"].strip().endswith((".", "?", "!", "…"))
                or nxt["start"] - w["end"] >= SPLIT_PAUSE_S):
            out += split_long(cur)
            cur = []
    return out


def mark_fillers(words: list[dict], ld: dict) -> list[tuple[int, int]]:
    """Index spans [i, j) of filler candidates."""
    toks = [norm(w["word"]) for w in words]
    phrases = sorted((norm(p).split() for p in ld["always"]), key=len, reverse=True)
    contextual = {norm(c) for c in ld["contextual"]}
    spans, i = [], 0
    while i < len(words):
        hit = next((len(p) for p in phrases if toks[i:i + len(p)] == p), 0)
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
        for ws in sentences_of(words):
            spans = mark_fillers(ws, ld)
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
    if s["low_confidence"]:
        line += "   ⚠ low confidence"
    return line


def write_outputs(out: Path, result: dict, ld: dict) -> None:
    sents = analyze(result["order"], result["files"], ld)
    md = ["# Transcript", "",
          "⟨…⟩ filler candidate · ↻ possible retake (usually keep the later take) · ⚠ low confidence", ""]
    prev_file, prev_end = None, 0.0
    for s in sents:
        if s["file"] != prev_file:
            md += ([""] if prev_file else []) + [f"## {s['file']}", ""]
            prev_file, prev_end = s["file"], 0.0
        if s["start"] - prev_end >= PAUSE_LINE_S:
            md.append(f"   [{tc(prev_end)}–{tc(s['start'])}] (pause {s['start'] - prev_end:.1f} s)")
        md.append(render(s))
        prev_end = s["end"]
    (out / "transcript.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    result["sentences"] = [{k: v for k, v in s.items() if k not in ("words", "tokens", "_spans")} for s in sents]
    (out / "transcript.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--lang", required=True, help="language code; never auto-detect")
    ap.add_argument("--glossary", help="comma-separated names, brands and terms to spell right")
    ap.add_argument("--out", default="01-ingest")
    ap.add_argument("--model", default="large-v3")
    ap.add_argument("--track", type=int, default=0, help="audio track index to transcribe")
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
        print(f"loading {args.model} (first run downloads the model, ~3 GB)…", flush=True)
        model, device = load_model(args.model)
        print(f"model ready on {device}", flush=True)
        for f in map(Path, args.files):
            result["files"][f.name] = {**transcribe_file(model, f, args, ld), "device": device}
            result_path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
        names = [Path(f).name for f in args.files]
        result["order"] = names + [n for n in result["order"] if n not in names]
    elif not result["files"]:
        die(f"nothing to rebuild: {result_path} is missing or empty")

    write_outputs(out, result, ld)
    print(f"transcript → {out / 'transcript.md'}", flush=True)


if __name__ == "__main__":
    main()
