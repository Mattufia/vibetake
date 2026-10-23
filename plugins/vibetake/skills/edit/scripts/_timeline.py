"""Edit timeline helpers shared by render.py and broll.py (no third-party imports)."""
from __future__ import annotations


import math

# Per-segment timing (all optional, in edl.json's segments):
#   speed      2 (constant), or a curve [[s, speed], …]: s = seconds of the played material from
#              the segment's start, linear between points, flat outside (a ramp; a step = two
#              close points)
#   hold       [[s, seconds], …]: freeze the picture at s for that long (s = the length: the last frame)
#   reverse    true: played backwards
#   loop       n: played n times · boomerang: true: forward then backwards
#   audio      keep (tempo changed, pitch kept) | pitch (like tape) | mute
#   smooth     true: interpolated slow motion / blended fast motion
#   j, l       seconds: this segment's sound starts before its picture (J) / runs on under the
#              next one (L)
#   transition {"type": "dissolve"|xfade name, "dur": s}: from the previous segment, centered on the cut
#   fade_in, fade_out  {"dur": s, "color": "black"|"white"|"#hex"}: picture and sound
TIMING = ("speed", "hold", "reverse", "loop", "boomerang", "audio", "smooth", "j", "l")
CARRY = TIMING + ("transition", "fade_in", "fade_out", "stabilize", "fit", "flip", "rotate", "key", "redact", "voice",
                  "pip", "split", "behind")


def play_len(seg: dict) -> float:
    """Seconds of material played, before speed and holds (loops and boomerang repeat it)."""
    return (seg["out"] - seg["in"]) * max(1, int(seg.get("loop", 1))) * (2 if seg.get("boomerang") else 1)


def pieces(seg: dict) -> list[tuple[float, float, float, float, float]]:
    """The speed curve as (s0, s1, v0, v1, t0): linear speed from v0 to v1 over [s0, s1] of the
    played material, starting at t0 seconds of the segment. The last one runs on forever."""
    sp = seg.get("speed", 1)
    knots = [(0.0, float(sp))] if isinstance(sp, (int, float)) else sorted((float(a), float(b)) for a, b in sp)
    if any(v <= 0 for _, v in knots):
        raise ValueError(f"speed must be > 0 (a freeze is 'hold'): {sp}")
    if knots[0][0] > 0:
        knots.insert(0, (0.0, knots[0][1]))
    out, t = [], 0.0
    for (s0, v0), (s1, v1) in zip(knots, knots[1:]):
        if s1 <= s0:
            continue
        out.append((s0, s1, v0, v1, t))
        t += _span(s1 - s0, v0, v1)
    s0, v = knots[-1]
    return out + [(s0, math.inf, v, v, t)]


def _span(ds: float, v0: float, v1: float) -> float:
    """Seconds it takes to play ds of material while the speed goes linearly from v0 to v1."""
    return ds / v0 if abs(v1 - v0) < 1e-9 else ds * math.log(v1 / v0) / (v1 - v0)


def _speed_time(ps: list, s: float) -> float:
    for s0, s1, v0, v1, t0 in ps:
        if s < s1:
            return t0 + _span(s - s0, v0, v0 + (v1 - v0) * (s - s0) / (s1 - s0) if s1 < math.inf else v0)
    return 0.0


def play_to_seg(seg: dict, s: float) -> float:
    """Seconds into the segment at which the played material's moment s is on screen."""
    return _speed_time(pieces(seg), s) + sum(d for a, d in seg.get("hold", []) if a <= s + 1e-9)


def seg_time(seg: dict) -> float:
    """The segment's length on the timeline (before rounding to frames)."""
    return play_to_seg(seg, play_len(seg)) + sum(d for a, d in seg.get("hold", []) if a > play_len(seg))


def is_simple(seg: dict) -> bool:
    """Constant speed, played once forwards, no freeze: a straight line from source to timeline."""
    return (isinstance(seg.get("speed", 1), (int, float)) and not seg.get("hold") and not seg.get("reverse")
            and int(seg.get("loop", 1)) <= 1 and not seg.get("boomerang"))


def src_to_seg(seg: dict, time: float) -> float:
    """Seconds into the segment where a moment of its source is first seen."""
    rel = time - seg["in"]
    if seg.get("reverse") and rel > 1e-6:      # its first moment = the cut into it (an anchor --cut N)
        rel = (seg["out"] - seg["in"]) - rel
    return play_to_seg(seg, rel)


def seg_to_src(seg: dict, t: float) -> float:
    """The source moment on screen t seconds into the segment."""
    n = seg["out"] - seg["in"]
    lo, hi = 0.0, play_len(seg)
    for _ in range(40):                          # play_to_seg only grows: bisect it
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if play_to_seg(seg, mid) <= t else (lo, mid)
    s = lo % (2 * n) if seg.get("boomerang") else lo % n if n else 0.0
    if s > n:
        s = 2 * n - s
    return seg["in"] + (n - s if seg.get("reverse") else s)


def audio_mode(seg: dict) -> str:
    """keep | pitch | mute. Only a straight segment keeps its sound: a curve, a freeze or a
    reversal is a sound-design moment (room tone under it, music or an effect on top)."""
    a = seg.get("audio")
    if not is_simple(seg):
        return "mute"
    if a in ("keep", "pitch", "mute"):
        return a
    v = float(seg.get("speed", 1))
    return "keep" if 0.5 - 1e-9 <= v <= 2.5 else "mute"


def seg_frames(seg: dict, fps: float) -> int:
    return max(1, round(seg_time(seg) * fps))


def starts(segs: list[dict], fps: float) -> list[float]:
    """Timeline start of each segment, with the same frame rounding render.py uses."""
    out, t = [], 0
    for s in segs:
        out.append(t / fps)
        t += seg_frames(s, fps)
    return out


def total(segs: list[dict], fps: float) -> float:
    return sum(seg_frames(s, fps) for s in segs) / fps


EDGE_SLACK = 0.3     # how far outside a cut a word timestamp can sit and still belong to it


def transition_dur(segs: list[dict], k: int, fps: float) -> float:
    """Length of the transition into segment k (0 = a straight cut): whole frames, even, and no
    longer than either segment."""
    tr = segs[k].get("transition") if k else None
    if not tr:
        return 0.0
    d = min(float(tr.get("dur", 0.5)), seg_frames(segs[k], fps) / fps, seg_frames(segs[k - 1], fps) / fps)
    return 2 * math.floor(d * fps / 2) / fps


def jl_cuts(segs: list[dict], fps: float) -> tuple[list[tuple[float, float]], list[str]]:
    """The J and L cuts as they're really played, per cut k (before segment k): (j, l) in seconds
    of the timeline. A cut with a transition has none; a segment that doesn't keep its sound
    can't lend it; each segment keeps at least 0.1 s of its own sound, even when an L eats its
    start and a J its end. render.py, subtitles and anchors all use this. Returns warnings too."""
    n = len(segs)
    durs = [seg_frames(s, fps) / fps for s in segs]
    out, warn, eaten = [(0.0, 0.0)] * n, [], [0.0] * n
    for k in range(1, n):
        s, prev = segs[k], segs[k - 1]
        if transition_dur(segs, k, fps) > 0:
            if s.get("j") or prev.get("l"):
                warn.append(f"segment {k}: a transition and a J/L cut on the same cut — the transition wins")
            continue
        jl = []
        for key, owner, room in (("j", s, durs[k - 1] - eaten[k - 1]), ("l", prev, durs[k])):
            x = float(owner.get(key, 0) or 0)
            if x and audio_mode(owner) == "mute":
                warn.append(f"segment {k}: {key.upper()} cut needs a segment that keeps its sound (straight speed) — skipped")
                x = 0.0
            jl.append(min(max(x, 0.0), max(room - 0.1, 0.0)))
        out[k] = (jl[0], jl[1])
        eaten[k] = jl[1]
    for k in (0, n - 1):
        key = "j" if k == 0 else "l"
        if segs[k].get(key):
            warn.append(f"segment {k}: {'a J on the first' if k == 0 else 'an L on the last'} segment has nothing "
                        "to run under — skipped")
    return out, warn


def sound_windows(segs: list[dict], fps: float) -> list[tuple[float, float]]:
    """The source stretch of each segment's sound that is heard (J/L cuts move its ends off the picture's)."""
    cuts, _ = jl_cuts(segs, fps)
    out = []
    for k, s in enumerate(segs):
        v = float(s.get("speed", 1)) if is_simple(s) else 1.0
        j, l_in = cuts[k]
        j_next, l_out = cuts[k + 1] if k + 1 < len(segs) else (0.0, 0.0)
        out.append((s["in"] - (j - l_in) * v, s["out"] + (l_out - j_next) * v))
    return out


def to_timeline(segs: list[dict], fps: float, name: str, time: float) -> float | None:
    """Where a moment of a source file lands in the edit. A moment used twice (a cold open
    repeated later) maps to its regular place, not the hook. None if it was cut out."""
    t0 = starts(segs, fps)
    hits = [(s.get("role") == "hook", t0[i] + src_to_seg(s, time)) for i, s in enumerate(segs)
            if s.get("name") == name and s["in"] - 1e-3 <= time <= s["out"] + 1e-3]
    # J and L cuts: the sound around a simple segment is heard over its neighbors' picture.
    win = sound_windows(segs, fps)
    hits += [(s.get("role") == "hook", t0[i] + (time - s["in"]) / float(s.get("speed", 1)))
             for i, s in enumerate(segs) if s.get("name") == name and is_simple(s) and audio_mode(s) != "mute"
             and (win[i][0] - 1e-3 <= time < s["in"] - 1e-3 or s["out"] + 1e-3 < time <= win[i][1] + 1e-3)]
    if not hits:
        # Cuts sit on the real audio, word timestamps can sit a little outside them: a moment
        # just before a segment's start (or after its end) belongs to that segment's edge.
        near = [(min(abs(time - s["in"]), abs(time - s["out"])), s.get("role") == "hook", i)
                for i, s in enumerate(segs) if s.get("name") == name
                and s["in"] - EDGE_SLACK <= time <= s["out"] + EDGE_SLACK]
        if not near:
            return None
        _, _, i = min(near)
        s = segs[i]
        return t0[i] + src_to_seg(s, min(max(time, s["in"]), s["out"]))
    return sorted(hits)[0][1]


def resolve_broll(segs: list[dict], fps: float, items: list[dict]) -> tuple[list[dict], list[str]]:
    """Timeline position of each b-roll item (anchored to a source moment). Returns the
    placed items (with 'at' and clamped 'dur') and warnings."""
    end = total(segs, fps)
    placed, warnings = [], []
    for b in items:
        a = b["anchor"]
        at = to_timeline(segs, fps, a["name"], a["time"])
        if at is None:
            warnings.append(f"b-roll {b['clip']}: its anchor ({a['name']} @ {a['time']:.2f} s) was cut out — skipped")
            continue
        at = max(0.0, at + b.get("offset", 0.0))
        dur = min(b["dur"], end - at)
        if dur < 0.5:
            warnings.append(f"b-roll {b['clip']}: less than 0.5 s left at {at:.2f} s — skipped")
            continue
        placed.append({**b, "at": round(at, 3), "dur": round(dur, 3)})
    placed.sort(key=lambda b: b["at"])
    for x, y in zip(placed, placed[1:]):
        if y["at"] < x["at"] + x["dur"] - 0.02:
            warnings.append(f"b-roll {x['clip']} and {y['clip']} overlap at {y['at']:.2f} s")
    return placed, warnings


def signature(edl: dict) -> str:
    """A fingerprint of the edit's timeline (output size and fps, which piece of which file,
    framing). Subtitles and the graphics layer carry the one they were made for, so render.py
    can tell when they belong to an older cut."""
    import hashlib
    import json
    segs = [{**{k: s.get(k) for k in ("source", "name", "in", "out", "frame", "role")},
             **{k: s[k] for k in TIMING if k in s}} for s in edl["segments"]]
    blob = json.dumps({"output": edl["output"], "segments": segs}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]


def word_key(t: str) -> str:
    """A word reduced to letters and digits, without accents or case: 'Ventas,' == 'ventas'.
    The ñ stays a letter of its own ('año' isn't 'ano')."""
    import re
    import unicodedata
    t = unicodedata.normalize("NFD", t.lower())
    t = "".join(c for i, c in enumerate(t) if unicodedata.category(c) != "Mn" or (c == "̃" and i and t[i - 1] == "n"))
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFC", t))


def chapters(edl: dict) -> list[tuple[float, str]]:
    """edl.json's chapters on the timeline, in order (deliver.py chapter adds them, anchored to the
    source so re-cuts move them along); the first one shown at 0:00. Cut-out anchors are dropped."""
    fps = edl["output"]["fps"]
    out = sorted((t, c["title"]) for c in edl.get("chapters", [])
                 for t in [to_timeline(edl["segments"], fps, c["anchor"]["name"], c["anchor"]["time"])] if t is not None)
    return [(0.0, out[0][1]), *out[1:]] if out else []


def bleeped(word: str, patterns: list[str]) -> bool:
    """Whether a word is on edl.json's "bleep" list (accents and case ignored; "put*" = any word starting so)."""
    k = word_key(word)
    return bool(k) and any(word_key(p) and (k == word_key(p) or (p.endswith("*") and k.startswith(word_key(p))))
                           for p in patterns)


def anchor_from(args, edl: dict) -> dict:
    """Where b-roll or a sound effect is anchored: the cut before segment --cut N, or a word of
    a transcript line (--line L5 [--word x]) — moments of the source, so re-cuts don't move them."""
    from pathlib import Path
    from _common import die, load_json
    if getattr(args, "cut", None) is not None:
        segs = edl["segments"]
        if not 1 <= args.cut < len(segs):
            die(f"--cut must be between 1 and {len(segs) - 1} (the cut before that segment, 0-based)")
        return {"name": segs[args.cut]["name"], "time": segs[args.cut]["in"]}
    tr = load_json(Path(args.ingest) / "transcript.json")
    line = int(args.line.lstrip("Ll"))
    sent = next((s for s in tr["sentences"] if s["line"] == line), None) or die(f"no line L{line}")
    words = [w for seg in tr["files"][sent["file"]]["segments"] for w in seg["words"]
             if sent["start"] - 1e-3 <= w["start"] and w["end"] <= sent["end"] + 1e-3]
    w = words[0] if not args.word else next((w for w in words if word_key(w["word"]) == word_key(args.word)), None)
    if w is None:
        die(f"'{args.word}' isn't in L{line}: {''.join(x['word'] for x in words).strip()}")
    return {"name": sent["file"], "time": w["start"]}


def place_music(music: list[dict], segs: list[dict], fps: float) -> list[dict]:
    """Music items as played. A montage block's music (montage.py) follows its block wherever the
    spoken edit leaves it: it starts with the block's first shot and fades out after its last
    one (under the first words when speech follows)."""
    block = [k for k, s in enumerate(segs) if s.get("role") == "montage"]
    out = []
    t0 = starts(segs, fps)
    for mu in music:
        if mu.get("block") == "montage":
            if not block:
                continue
            end = t0[block[-1]] + seg_frames(segs[block[-1]], fps) / fps
            mu = {**mu, "at": t0[block[0]], "until": end + (mu.get("fade_out", 2.0) if block[-1] < len(segs) - 1 else 0)}
        out.append(mu)
    return out
