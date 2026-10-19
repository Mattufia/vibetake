"""Edit timeline helpers shared by render.py and broll.py (no third-party imports)."""
from __future__ import annotations


def seg_frames(seg: dict, fps: float) -> int:
    return max(1, round((seg["out"] - seg["in"]) * fps))


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


def to_timeline(segs: list[dict], fps: float, name: str, time: float) -> float | None:
    """Where a moment of a source file lands in the edit. A moment used twice (a cold open
    repeated later) maps to its regular place, not the hook. None if it was cut out."""
    t0 = starts(segs, fps)
    hits = [(s.get("role") == "hook", t0[i] + time - s["in"]) for i, s in enumerate(segs)
            if s.get("name") == name and s["in"] - 1e-3 <= time <= s["out"] + 1e-3]
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
        return t0[i] + min(max(time, s["in"]), s["out"]) - s["in"]
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
    segs = [{k: s.get(k) for k in ("source", "name", "in", "out", "frame", "role")} for s in edl["segments"]]
    blob = json.dumps({"output": edl["output"], "segments": segs}, sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()[:12]
