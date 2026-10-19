# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Audio: clean each source once, add music, master the final mix.

    uv run audio.py clean [--nr normal|light|off] [--force]   # raw → 01-ingest/audio/<file>.wav
    uv run audio.py music --file track.mp3 [--in 0] [--gain 0] [--start 0] [--fade-in 1] [--fade-out 2]
    uv run audio.py music --clear

Cleaning depends on how each file was recorded, so it runs on the whole raw file:
high-pass ~80 Hz, noise reduction calibrated on that file's noise floor (the long
pauses of the raw are the best place to learn the noise), a de-esser, and a gain
that brings every file to the same speech level (phone and camera end up matching).
Same length and timing as the original; render.py uses it instead of the camera
audio. Transcription stays on the original audio (it recognizes speech better).

Mixing and level depend on the final edit, so render.py does them on the cut:
gentle compression across the whole voice, music ducked under the voice, then a
second, audio-only pass that measures the result and sets it to the destination's
loudness (render.py --loudness social|podcast|broadcast-eu|broadcast-us).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import Cache, Log, channel_pan, die, ffmpeg_progress, ffprobe, run, tool  # noqa: E402

TARGETS = {"social": (-14.0, -1.0), "podcast": (-16.0, -1.0),
           "broadcast-eu": (-23.0, -2.0), "broadcast-us": (-24.0, -2.0)}
SPEECH_LEVEL = -20.0     # LUFS of every cleaned file: headroom for the mix
MUSIC_LEVEL = -28.0      # LUFS of the music bed before ducking (~18 dB under the voice while talking)
BUS = "acompressor=threshold=0.125:ratio=2.5:attack=15:release=200:makeup=1"   # gentle, across cuts


NR = {"normal": 12, "light": 6, "off": 0}   # dB of noise reduction (--nr; a profile can ask for less)
AMBIENT_SPEECH_PCT = 15                     # below this share of speech a file is ambience: kept as recorded


def chain(noise_floor_db: float | None, nr: str = "normal") -> str:
    nf = min(max(noise_floor_db if noise_floor_db is not None else -60, -80), -20)
    nrf = f",afftdn=nr={NR[nr]}:nf={nf:.0f}:tn=1" if NR[nr] else ""
    return f"highpass=f=80{nrf},deesser=i=0.4"


def speech_spans(words: list[dict], merge: float = 1.0, limit: int = 150) -> list[tuple[float, float]]:
    """Where someone speaks (from the transcript's words), merged into at most `limit` spans."""
    spans: list[list[float]] = []
    for w in sorted(words, key=lambda w: w["start"]):
        if spans and w["start"] - spans[-1][1] < merge:
            spans[-1][1] = max(spans[-1][1], w["end"])
        else:
            spans.append([w["start"], w["end"]])
    while len(spans) > limit:                  # merge the closest pair until it fits
        k = min(range(len(spans) - 1), key=lambda i: spans[i + 1][0] - spans[i][1])
        spans[k:k + 2] = [[spans[k][0], spans[k + 1][1]]]
    return [(a, b) for a, b in spans]


_LATENCY: dict[str, int] = {}


def latency(af: str) -> int:
    """Samples of delay a filter chain adds at 48 kHz (noise reduction works on FFT blocks
    and delays the sound ~25 ms). Measured here, on a noise burst, because it depends on
    the ffmpeg version; the cleaned audio is shifted back by it so lips stay in sync."""
    if af not in _LATENCY:
        import numpy as np
        src = "anoisesrc=d=2:a=0.5:c=white:r=48000,volume='if(between(t,1,1.05),1,0)':eval=frame"

        def get(f):
            cmd = [tool("ffmpeg"), "-v", "error", "-f", "lavfi", "-i", src, *(["-af", f] if f else []),
                   "-ac", "1", "-f", "s16le", "-"]
            raw = subprocess.run(cmd, capture_output=True).stdout
            return np.abs(np.frombuffer(raw, np.int16).astype(float))
        a, b = get(None), get(af)
        n = min(len(a), len(b))
        c = np.fft.irfft(np.fft.rfft(b[:n], 2 * n) * np.conj(np.fft.rfft(a[:n], 2 * n)))
        k = int(np.argmax(c))
        _LATENCY[af] = max(k if k < n else k - 2 * n, 0)
    return _LATENCY[af]


def measure(path: Path, args: list[str] | None = None, af: str | None = None) -> dict:
    """Integrated loudness, loudness range and true peak (EBU R128)."""
    filt = (af + "," if af else "") + "ebur128=peak=true:framelog=verbose"
    p = run([tool("ffmpeg"), "-hide_banner", "-nostats", "-i", str(path), *(args or []), "-af", filt,
             "-f", "null", "-"], check=False)
    s = p.stderr[p.stderr.rfind("Summary:"):]
    num = lambda pat: (float(m.group(1)) if (m := re.search(pat, s)) and m.group(1) != "-inf" else None)  # noqa: E731
    return {"I": num(r"I:\s+(-?[\d.]+|-inf) LUFS"), "LRA": num(r"LRA:\s+([\d.]+) LU"),
            "TP": num(r"Peak:\s+(-?[\d.]+|-inf) dBFS")}


ROOM_TONE_S = 1.5


def room_tone(src: Path, dst: Path) -> Path | None:
    """The quietest ROOM_TONE_S of a cleaned file that still has sound (not digital silence):
    the room's own air, to fill a gap instead of a dead, digital silence."""
    import subprocess

    import numpy as np
    raw = subprocess.run([tool("ffmpeg"), "-v", "error", "-i", str(src), "-ac", "1", "-ar", "8000", "-f", "f32le",
                          "-"], capture_output=True).stdout
    x = np.frombuffer(raw, np.float32)
    win = int(ROOM_TONE_S * 8000)
    if len(x) < win * 2:
        return None
    hop = 800
    frames = len(x) // hop
    e = (x[: frames * hop].reshape(frames, hop) ** 2).mean(1)
    n = win // hop
    rms = np.sqrt(np.convolve(e, np.ones(n) / n, mode="valid"))
    ok = rms > 10 ** (-90 / 20)                                # not digital silence
    if not ok.any():
        return None
    i = int(np.argmin(np.where(ok, rms, np.inf)))
    start = i * hop / 8000
    run([tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{ROOM_TONE_S}", "-i", str(src),
         "-af", "afade=t=in:d=0.02,areverse,afade=t=in:d=0.02,areverse", "-c:a", "pcm_s24le", str(dst)])
    return dst


def cmd_clean(args) -> None:
    ing = Path(args.ingest)
    material = json.loads((ing / "material.json").read_text(encoding="utf-8"))["files"]
    tracks, words = {}, {}
    if (ing / "transcript.json").exists():
        tfiles = json.loads((ing / "transcript.json").read_text(encoding="utf-8"))["files"]
        tracks = {n: f.get("track", 0) for n, f in tfiles.items()}
        words = {n: [w for s in f["segments"] for w in s.get("words", [])] for n, f in tfiles.items()}
    out = ing / "audio"
    out.mkdir(parents=True, exist_ok=True)
    log = Log(ing / "ingest.log")
    cache = Cache(out / ".cache.json", args.force)
    index_path = out / "clean.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    by_name = {m["name"]: m for m in material}
    used_external = {m["external_audio"]["name"] for m in material if m.get("external_audio")}
    for m in material:
        if not m.get("audio_tracks"):
            continue
        name, src = m["name"], Path(m["file"])
        if name in used_external and not args.no_external:
            continue                       # cleaned through the videos it covers, aligned to each
        track = tracks.get(name, m.get("suggested_audio_track", 0))
        info = next((a for a in m.get("audio", []) if a["index"] == track), {})
        floor = info.get("noise_floor_db")
        pan = channel_pan(info.get("channels"))
        # Sound recorded separately (a recorder, a wireless mic): usually the best — use it,
        # cut and aligned to this video (offset, and the drift of the two clocks).
        ext, align, sound = m.get("external_audio"), "", src
        if ext and not args.no_external:
            sound, dur = Path(ext["file"]), float(m["duration"])
            e_meta = by_name.get(ext["name"], {})
            ch = next((t.get("channels") for t in e_meta.get("audio_tracks", [])), 1) or 1
            from ingest import channel_layout
            pan = channel_pan(channel_layout(sound, 0, ch)) if ch > 1 else ""
            floor, off, drift = None, ext["offset_s"], ext.get("drift_s") or 0.0
            align = ",".join(x for x in (
                f"atrim=start={max(0.0, -off):.4f},asetpts=PTS-STARTPTS",
                f"atempo={(dur - drift) / dur:.6f}" if drift else "",
                f"adelay={max(0.0, off) * 1000:.1f}:all=1" if off > 0 else "",
                f"apad,atrim=end={dur:.4f}") if x)
        spans = speech_spans(words.get(name, []))
        speech_pct = 100 * sum(b - a for a, b in spans) / max(float(m["duration"]), 1e-6)
        ambient = name in words and speech_pct < AMBIENT_SPEECH_PCT     # no transcript → treated as speech
        # Ambience (little or no speech: the sea, a street, a walk) keeps its sound as recorded:
        # no noise reduction, no gain — that sound is what the shot is for.
        af = ",".join(x for x in (pan, "highpass=f=40" if ambient else chain(floor, args.nr)) if x)
        dst = out / f"{Path(name).stem}.wav"
        if dst.exists() and name in index and cache.fresh(f"clean:{name}", src, f"{align}|{af}"):
            log(f"{name}: clean audio is up to date")
            continue
        sel = ["-map", f"0:a:{track}"] if m.get("kind") == "video" and sound == src else []
        full = ",".join(x for x in (align, af) if x)
        if ambient:
            level, gain = None, 0.0
        else:
            # The level of the voice, measured where someone speaks — not the ambience or music.
            only = "aselect='" + "+".join(f"between(t,{a:.2f},{b:.2f})" for a, b in spans) + "',asetpts=N/SR/TB"
            level = measure(sound, sel, f"{full},{only}" if spans else full)["I"]
            gain = max(min(SPEECH_LEVEL - level, 20.0), -20.0) if level is not None else 0.0
        lat = latency(af)
        log(f"{name}: " + (f"ambience ({speech_pct:.0f}% speech): kept as recorded" if ambient else
                           f"cleaning (noise floor {floor} dB, noise reduction {args.nr}, speech {level} LUFS → "
                           f"{SPEECH_LEVEL}, {gain:+.1f} dB, chain latency {lat / 48:.1f} ms compensated)")
            + (f" · channels → center ({pan})" if pan else "")
            + (f" · sound from {ext['name']} at {ext['offset_s']:+.3f} s"
               + (f", drift {ext['drift_s'] * 1000:+.0f} ms corrected" if ext.get("drift_s") else "") if align else ""))
        # Resample first so the latency (measured at 48 kHz) is in the same samples.
        shift = (f",atrim=start_sample={lat},asetpts=PTS-STARTPTS,apad=pad_len={lat}" if lat else "")
        ffmpeg_progress(["-i", str(sound), *sel, "-af", f"aresample=48000,{full}{shift},volume={gain:.2f}dB",
                         "-c:a", "pcm_s24le", str(dst)], float(m["duration"]), f"{name} clean audio", log, step=50)
        index[name] = {"file": str(dst), "track": track, "chain": af, "gain_db": round(gain, 2),
                       "measured_lufs": level, "ambient": ambient,
                       "sound": f"external {ext['name']}" if align else "camera"}
        rt = room_tone(dst, out / f"{Path(name).stem}.roomtone.wav")
        if rt:
            index[name]["roomtone"] = str(rt)
        cache.mark(f"clean:{name}", src, f"{align}|{af}")
        index_path.write_text(json.dumps(index, indent=1), encoding="utf-8")
    log(f"clean audio → {out}")


def cmd_music(args) -> None:
    edl_path = Path(args.edl)
    edl = json.loads(edl_path.read_text(encoding="utf-8"))
    if args.clear:
        edl.pop("music", None)
        edl_path.write_text(json.dumps(edl, indent=1, ensure_ascii=False), encoding="utf-8")
        print("music removed")
        return
    if not args.file:
        die("--file is required")
    f = Path(args.file)
    level = measure(f)["I"] or -20.0
    gain = MUSIC_LEVEL - level + args.gain
    item = {"source": str(f), "in": args.in_, "at": args.start, "gain_db": round(gain, 2),
            "fade_in": args.fade_in, "fade_out": args.fade_out, "duration": float(ffprobe(f)["format"]["duration"])}
    edl["music"] = (edl.get("music", []) if args.append else []) + [item]
    edl_path.write_text(json.dumps(edl, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"music: {f.name} ({level:.1f} LUFS → bed at {MUSIC_LEVEL + args.gain:.1f} LUFS, ducked under speech)")


def music_graph(first_input: int, voice: str, items: list[dict], total: float) -> tuple[list, list, str]:
    """Music beds under the voice: leveled, looped to cover the edit, faded, and ducked
    with the voice as the key (down while someone talks, back up in the pauses)."""
    if not items:
        return [], [], voice
    inputs, graphs, beds = [], [], []
    for j, mu in enumerate(items):
        k = first_input + j
        inputs += ["-stream_loop", "-1", "-ss", f"{mu['in']:.3f}", "-i", mu["source"]]
        dur = max(total - mu["at"], 0.1)
        fo = min(mu["fade_out"], dur / 2)
        graphs.append(f"[{k}:a:0]aformat=sample_rates=48000:channel_layouts=stereo,volume={mu['gain_db']:.2f}dB,"
                      "equalizer=f=3000:t=q:w=1:g=-3,"
                      f"atrim=duration={dur:.3f},asetpts=PTS-STARTPTS,afade=t=in:d={mu['fade_in']},"
                      f"afade=t=out:st={dur - fo:.3f}:d={fo:.3f},adelay=delays={int(mu['at'] * 1000)}:all=1[m{j}]")
        beds.append(f"[m{j}]")
    bed = "mb"
    graphs.append(f"{''.join(beds)}amix=inputs={len(beds)}:normalize=0[{bed}]" if len(beds) > 1
                  else f"{beds[0]}anull[{bed}]")
    graphs.append(f"[{voice}]asplit=2[vmix][vkey]")
    graphs.append(f"[{bed}][vkey]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400[mduck]")
    graphs.append("[vmix][mduck]amix=inputs=2:normalize=0:duration=first[mixed]")
    return inputs, graphs, "mixed"


def master(src: Path, dst: Path, target: str, log: Log) -> dict:
    """Audio-only second pass: measure the finished mix, set it to the destination's
    loudness, and keep true peak under the ceiling (oversampled limiter only if needed)."""
    lufs, ceiling = TARGETS[target]
    m = measure(src)
    if m["I"] is None:
        die("the render has no measurable audio")
    gain = lufs - m["I"]
    for attempt in range(2):
        af = f"volume={gain:.2f}dB"
        if m["TP"] is not None and m["TP"] + gain > ceiling - 0.3:
            # True peak: limit at 4× the sample rate so inter-sample peaks are caught too.
            af += (f",aresample=192000,alimiter=limit={10 ** ((ceiling - 0.4) / 20):.4f}:attack=1:release=60:"
                   "level=false,aresample=48000")
        run([tool("ffmpeg"), "-hide_banner", "-v", "error", "-y", "-i", str(src), "-map", "0:v", "-map", "0:a",
             "-c:v", "copy", "-af", af, "-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-movflags", "+faststart",
             str(dst)])
        final = measure(dst)
        if final["I"] is not None and abs(final["I"] - lufs) <= 0.5:
            break
        gain += lufs - (final["I"] or lufs)            # the limiter took some level: one correction
    log(f"loudness ({target}): {m['I']} → {final['I']} LUFS (target {lufs}), true peak {final['TP']} dBTP "
        f"(ceiling {ceiling})")
    return final


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ingest", default="01-ingest")
    ap.add_argument("--edl", default="02-work/edl.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("clean")
    c.add_argument("--force", action="store_true")
    c.add_argument("--no-external", action="store_true",
                   help="use the camera's sound even where a separate recording matches")
    c.add_argument("--nr", choices=tuple(NR), default="normal",
                   help="noise reduction on speech: normal, light (vlog, spontaneous material) or off")
    mu = sub.add_parser("music")
    mu.add_argument("--file")
    mu.add_argument("--in", dest="in_", type=float, default=0.0, help="start inside the music file")
    mu.add_argument("--start", type=float, default=0.0, help="timeline second where it comes in")
    mu.add_argument("--gain", type=float, default=0.0, help="dB relative to the default bed level")
    mu.add_argument("--fade-in", type=float, default=1.0)
    mu.add_argument("--fade-out", type=float, default=2.0)
    mu.add_argument("--append", action="store_true")
    mu.add_argument("--clear", action="store_true")
    args = ap.parse_args()
    {"clean": cmd_clean, "music": cmd_music}[args.cmd](args)


if __name__ == "__main__":
    main()
