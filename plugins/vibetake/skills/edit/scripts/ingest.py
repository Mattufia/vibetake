# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26", "opencv-python-headless>=4.9"]
# ///
"""Ingest: analyze raw footage before editing.

    uv run ingest.py [inputs...] --lang es [--out 01-ingest] [--no-transcribe] [--force]

Inputs are files or folders (default: 00-raw). Writes to --out:
    material.md / material.json   technical data, traps, classification signals, warnings
    faces.json                    face boxes every 0.5 s (normalized 0-1)
    transcript.json / .md         word-level / condensed transcript (from transcribe.py)
    contact/                      4x4 contact sheets with burned-in timecode
    normalized/                   CFR / SDR working copies, only when needed
    ingest.log                    progress
Transcription runs in parallel (GPU) while the rest runs on CPU.
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
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _common import (AUDIO_EXT, HOME, VIDEO_EXT, Cache, Log, die, ffmpeg_progress, ffprobe,  # noqa: E402
                     filter_path, find_font, run, tc, tool)

STANDARD_FPS = [23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60, 100, 119.88, 120]
LOG_PATTERN = re.compile(
    r"\b(s-?log\s?[23]?|v-?log|c-?log\s?[23]?|d-?log(?:\s?m)?|f-?log\s?2?|n-?log|apple\s?log|"
    r"log-?c\s?[34]?|log3g10|bmd\s?film|hlg\d?)\b", re.I)
YUNET_URL = ("https://github.com/opencv/opencv_zoo/raw/main/models/"
             "face_detection_yunet/face_detection_yunet_2023mar.onnx")
SCENE_THRESHOLD = 0.3
MAX_SCENE_FRAMES = 64          # 4 sheets of 4x4
FACE_FPS = 2


# ---------------------------------------------------------------- detection

def frac(s: str | None) -> float:
    try:
        n, d = (s or "0/0").split("/")
        return float(n) / float(d) if float(d) else 0.0
    except ValueError:
        return 0.0


def check_vfr(path: Path, duration: float) -> tuple[bool, float]:
    """Packet timestamps at the start, middle and end (10 s each). Returns (is_vfr, measured fps)."""
    starts = sorted({0.0, max(0.0, duration / 2 - 5), max(0.0, duration - 10)})
    intervals = ",".join(f"{s:.2f}%+10" for s in starts)
    p = run([tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-read_intervals", intervals,
             "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(path)], check=False)
    pts = sorted({float(x) for x in p.stdout.split() if re.fullmatch(r"-?[\d.]+", x.strip(","))})
    d = np.diff(pts)
    d = d[(d > 0) & (d < 1)]          # ignore the jumps between sampled windows
    if len(d) < 10:
        return False, 0.0
    med = float(np.median(d))
    irregular = float(np.mean(np.abs(d - med) > 0.1 * med))
    return irregular > 0.02, 1.0 / med


def exif_strings(path: Path) -> dict:
    exe = tool("exiftool", required=False)
    if not exe:
        return {}
    p = run([exe, "-j", "-G1", "-a", "-s", "-ee3", "-api", "LargeFileSupport=1", str(path)], check=False)
    try:
        data = json.loads(p.stdout)[0]
    except (ValueError, IndexError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, (str, int, float)) and not k.startswith("System:")}


def detect(path: Path) -> dict:
    info = ffprobe(path)
    streams = info.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"
              and not s.get("disposition", {}).get("attached_pic")), None)
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    fmt = info.get("format", {})
    duration = float(fmt.get("duration") or (v or {}).get("duration") or 0)
    recorded = fmt.get("tags", {}).get("creation_time") or next(
        (s.get("tags", {}).get("creation_time") for s in streams if s.get("tags", {}).get("creation_time")), None)
    meta: dict = {"file": str(path), "name": path.name, "duration": duration, "recorded": recorded,
                  "audio_tracks": [{"index": i, "codec": a.get("codec_name"), "channels": a.get("channels"),
                                    "sample_rate": a.get("sample_rate"),
                                    "title": a.get("tags", {}).get("title") or a.get("tags", {}).get("handler_name")}
                                   for i, a in enumerate(audio)]}
    if v is None:
        meta["kind"] = "audio"
        return meta
    meta["kind"] = "video"
    w, h = int(v["width"]), int(v["height"])
    rotation = 0
    for sd in v.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = int(round(float(sd["rotation"])))
    ew, eh = (h, w) if abs(rotation) % 180 == 90 else (w, h)
    vfr, measured = check_vfr(path, duration)
    # For VFR the dominant frame interval is the real rate; the average is dragged down by drops.
    avg = measured if vfr else (frac(v.get("avg_frame_rate")) or measured)
    pix = v.get("pix_fmt", "")
    bits = 12 if "12" in pix else 10 if "10" in pix else 8
    trc = v.get("color_transfer")
    hdr = {"arib-std-b67": "HLG", "smpte2084": "PQ"}.get(trc)

    tags = {**fmt.get("tags", {}), **{f"video:{k}": val for k, val in v.get("tags", {}).items()}}
    exif = exif_strings(path)
    strings = {**tags, **exif}
    make = next((str(val) for k, val in strings.items() if re.search(r"(^|[:.])make$", k, re.I)), None)
    model = next((str(val) for k, val in strings.items() if re.search(r"(^|[:.])model(name)?$", k, re.I)), None)
    hint = None
    for k, val in strings.items():
        m = LOG_PATTERN.search(str(val))
        if m and not m.group(1).lower().startswith("hlg"):
            hint = f"{m.group(1)} ({k})"
            break

    meta.update({
        "codec": v.get("codec_name"), "width": w, "height": h, "rotation": rotation,
        "display_width": ew, "display_height": eh,
        "orientation": "vertical" if eh > ew else "horizontal" if ew > eh else "square",
        "fps": round(avg, 3), "vfr": vfr, "pix_fmt": pix, "bit_depth": bits,
        "color": {"primaries": v.get("color_primaries"), "transfer": trc,
                  "matrix": v.get("color_space"), "range": v.get("color_range")},
        "hdr": hdr, "camera": " ".join(x for x in (make, model) if x) or None, "log_hint": hint,
    })
    return meta


# ------------------------------------------------------------ normalization

_ENCODER: tuple | None = None
_TONEMAP: tuple | None = None
HDR_TEST_SRC = ("testsrc2=s=320x180:d=0.3,format=yuv420p10le,"
                "setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc")


def _works(args: list[str], src: str = "testsrc2=s=320x180:d=0.3") -> bool:
    p = run([tool("ffmpeg"), "-hide_banner", "-v", "error", "-f", "lavfi", "-i", src, *args, "-f", "null", "-"],
            check=False)
    return p.returncode == 0


def pick_encoder() -> tuple[str, list[str]]:
    """10-bit visually lossless working codec: GPU HEVC, then CPU HEVC, then ProRes."""
    global _ENCODER
    if _ENCODER is None:
        candidates = [
            ("hevc_nvenc", ["-c:v", "hevc_nvenc", "-preset", "p7", "-tune", "hq", "-rc", "vbr", "-cq", "16",
                            "-b:v", "0", "-profile:v", "main10", "-pix_fmt", "p010le", "-tag:v", "hvc1"]),
            ("hevc_videotoolbox", ["-c:v", "hevc_videotoolbox", "-q:v", "80", "-profile:v", "main10",
                                   "-pix_fmt", "p010le", "-tag:v", "hvc1"]),
            ("hevc_qsv", ["-c:v", "hevc_qsv", "-global_quality", "16", "-profile:v", "main10",
                          "-pix_fmt", "p010le", "-tag:v", "hvc1"]),
            ("hevc_amf", ["-c:v", "hevc_amf", "-quality", "quality", "-rc", "cqp", "-qp_i", "16", "-qp_p", "16",
                          "-profile:v", "main10", "-pix_fmt", "p010le", "-tag:v", "hvc1"]),
            ("libx265", ["-c:v", "libx265", "-preset", "medium", "-crf", "14", "-pix_fmt", "yuv420p10le",
                         "-tag:v", "hvc1"]),
            ("prores_ks", ["-c:v", "prores_ks", "-profile:v", "2", "-pix_fmt", "yuv422p10le"]),
        ]
        _ENCODER = next(((n, a) for n, a in candidates if _works(a)), candidates[-1])
    return _ENCODER


def pick_tonemap() -> tuple[str, str]:
    """HDR -> SDR Rec.709. libplacebo (BT.2390, perceptual gamut mapping) or zscale+hable fallback."""
    global _TONEMAP
    if _TONEMAP is None:
        lp = ("libplacebo=tonemapping=bt.2390:gamut_mode=perceptual:colorspace=bt709:"
              "color_primaries=bt709:color_trc=bt709:range=tv:format=yuv420p10le")
        zs = ("zscale=t=linear:npl=203,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
              "zscale=t=bt709:m=bt709:r=tv,format=yuv420p10le")
        _TONEMAP = ("libplacebo", lp) if _works(["-vf", lp], HDR_TEST_SRC) else ("zscale+hable", zs)
    return _TONEMAP


def normalize(meta: dict, out: Path, cache: Cache, log: Log) -> Path | None:
    if not (meta["vfr"] or meta["hdr"]):
        return None
    src = Path(meta["file"])
    dst = out / "normalized" / f"{meta['stem']}.mov"
    enc_name, enc = pick_encoder()
    vf, notes = [], []
    if meta["hdr"]:
        tm_name, tm = pick_tonemap()
        vf.append(tm)
        notes.append(f"{meta['hdr']} -> SDR Rec.709 ({tm_name})")
    rate: list[str] = []
    if meta["vfr"]:
        target = min(STANDARD_FPS, key=lambda f: abs(f - meta["fps"]))
        rate = ["-fps_mode", "cfr", "-r", str(target)]
        notes.append(f"VFR -> CFR {target}")
        meta["fps"] = target
    meta["normalized"] = {"file": str(dst), "notes": notes, "encoder": enc_name}
    params = f"{enc_name}|{'|'.join(notes)}"
    if dst.exists() and cache.fresh(f"normalize:{meta['stem']}", src, params):
        log(f"{meta['name']}: normalized copy is up to date")
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    tags = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
            "-color_range", "tv"] if meta["hdr"] else []
    log(f"{meta['name']}: normalizing ({', '.join(notes)}; {enc_name})")
    ffmpeg_progress(["-i", str(src), "-map", "0:v:0", "-map", "0:a?", "-map_metadata", "0",
                     *(["-vf", ",".join(vf)] if vf else []), *rate, *enc, *tags,
                     "-c:a", "pcm_s24le", str(dst)], meta["duration"], f"{meta['name']} normalize", log)
    cache.mark(f"normalize:{meta['stem']}", src, params)
    return dst


# ----------------------------------------------------------- contact sheets

def drawtext(font: str | None) -> str:
    f = f"fontfile='{filter_path(font)}':" if font else ""
    return (f"drawtext={f}text='%{{pts\\:hms}}':x=8:y=8:fontsize=26:fontcolor=white:"
            "box=1:boxcolor=black@0.65:boxborderw=6")


def sheets_and_scenes(meta: dict, src: Path, sheet_dir: Path, font: str | None, log: Log) -> dict:
    """Pass 1: regular-grid sheets + scene detection. Pass 2: one sheet frame per shot change."""
    dur, stem = meta["duration"], meta["stem"]
    for old in sheet_dir.glob(f"{stem}_*.jpg"):
        old.unlink()
    n = int(min(96, max(16, dur / 2)))
    interval = max(dur / n, 0.04)
    fc = (f"[0:v]split=2[s][g];"
          f"[s]scale=320:-2,select='gt(scene,{SCENE_THRESHOLD})',metadata=print,nullsink;"
          f"[g]fps=1/{interval:.4f},scale=480:-2,{drawtext(font)},tile=4x4:padding=4:margin=4[out]")
    err = ffmpeg_progress(["-hwaccel", "auto", "-i", str(src), "-filter_complex", fc, "-map", "[out]",
                           "-fps_mode", "vfr", "-q:v", "3", str(sheet_dir / f"{stem}_grid_%02d.jpg")],
                          dur, f"{meta['name']} grid sheets + shot detection", log)
    cuts = [float(x) for x in re.findall(r"pts_time:([\d.]+)", err)]

    times = [0.0] + cuts
    if len(times) > MAX_SCENE_FRAMES:
        idx = np.linspace(0, len(times) - 1, MAX_SCENE_FRAMES).round().astype(int)
        times = [times[i] for i in idx]
    half = 0.5 / (meta.get("fps") or 30)
    expr = "+".join(f"lt(abs(t-{t:.4f}),{half:.5f})" for t in times)
    ffmpeg_progress(["-hwaccel", "auto", "-i", str(src), "-vf",
                     f"select='{expr}',scale=480:-2,{drawtext(font)},tile=4x4:padding=4:margin=4",
                     "-fps_mode", "vfr", "-q:v", "3", str(sheet_dir / f"{stem}_shots_%02d.jpg")],
                    dur, f"{meta['name']} shot sheets", log, step=50)
    sheets = sorted(p.name for p in sheet_dir.glob(f"{stem}_*.jpg"))
    return {"shot_changes": [round(c, 3) for c in cuts],
            "shot_changes_per_min": round(len(cuts) / (dur / 60), 1) if dur else 0,
            "grid_interval_s": round(interval, 2), "sheets": sheets}


# ---------------------------------------------------- faces + image signature

def yunet(w: int, h: int):
    os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
    import cv2
    path = HOME / "models" / "face_detection_yunet_2023mar.onnx"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(YUNET_URL, path)
    return cv2.FaceDetectorYN.create(str(path), "", (w, h), 0.7, 0.3, 5000)


def analyze_frames(meta: dict, src: Path, log: Log) -> tuple[dict, list]:
    """Decodes 2 fps at 640 px: face boxes on every sample, luma/saturation signature every 2.5 s."""
    W = 640
    H = int(round(W * meta["display_height"] / meta["display_width"] / 2)) * 2
    det = yunet(W, H)
    proc = subprocess.Popen([tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-hwaccel", "auto",
                             "-i", str(src), "-vf", f"fps={FACE_FPS},scale={W}:{H}", "-f", "rawvideo",
                             "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    size, i, samples, sig = W * H * 3, 0, [], []
    next_pct = 25
    while True:
        buf = proc.stdout.read(size)
        if len(buf) < size:
            break
        img = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
        _, found = det.detect(img)
        faces = [] if found is None else [
            [round(float(f[0]) / W, 3), round(float(f[1]) / H, 3), round(float(f[2]) / W, 3),
             round(float(f[3]) / H, 3), round(float(f[14]), 2)] for f in found]
        samples.append({"t": round(i / FACE_FPS, 2), "faces": faces})
        if i % 5 == 0:
            rgb = img[..., ::-1].astype(np.float32) / 255
            luma = rgb @ np.array([0.2126, 0.7152, 0.0722], np.float32)
            mx, mn = rgb.max(-1), rgb.min(-1)
            lit = mx > 0.05
            sig.append((float(np.percentile(luma, 1)), float(np.percentile(luma, 99)),
                        float(((mx - mn)[lit] / mx[lit]).mean()) if lit.any() else 0.0))
        i += 1
        pct = i / FACE_FPS / max(meta["duration"], 0.1) * 100
        if pct >= next_pct:
            log(f"{meta['name']} faces + image signature: {min(pct, 100):.0f}%")
            next_pct += 25
    proc.wait()

    with_face = [s for s in samples if s["faces"]]
    biggest = [max(s["faces"], key=lambda f: f[3]) for s in with_face]
    face = {
        "samples": len(samples),
        "pct_with_face": round(100 * len(with_face) / len(samples), 1) if samples else 0,
        "max_faces": max((len(s["faces"]) for s in samples), default=0),
        "median_face_height": round(float(np.median([f[3] for f in biggest])), 3) if biggest else None,
        "median_face_center": [round(float(np.median([f[0] + f[2] / 2 for f in biggest])), 3),
                               round(float(np.median([f[1] + f[3] / 2 for f in biggest])), 3)] if biggest else None,
    }
    if sig:
        p1, p99, sat = (float(np.median([s[k] for s in sig])) for k in range(3))
        # Log signature: lifted blacks, no real whites, low saturation. Uncalibrated
        # against every log curve; it only raises a "possible log" flag.
        flat = (p99 - p1) < 0.62 and p1 > 0.06 and sat < 0.3
        face["image"] = {"luma_p1": round(p1, 3), "luma_p99": round(p99, 3), "saturation": round(sat, 3),
                         "flat": flat}
    return face, samples


# --------------------------------------------------------------------- audio

def pcm(path: Path, track: int | None, rate: int, seconds: float | None = None) -> np.ndarray:
    cmd = [tool("ffmpeg"), "-v", "error", *(["-t", str(seconds)] if seconds else []), "-i", str(path)]
    if track is not None:
        cmd += ["-map", f"0:a:{track}"]
    p = subprocess.run(cmd + ["-ac", "1", "-ar", str(rate), "-f", "s16le", "-"], capture_output=True)
    return np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768


def window_db(x: np.ndarray, win: int) -> np.ndarray:
    n = len(x) // win
    if n == 0:
        return np.array([-90.0])
    rms = np.sqrt((x[:n * win].reshape(n, win) ** 2).mean(1))
    return np.maximum(20 * np.log10(rms + 1e-9), -90.0)


def analyze_audio(meta: dict, log: Log) -> tuple[list, np.ndarray | None]:
    src, dur, results, first_db = Path(meta["file"]), meta["duration"], [], None
    for tr in meta["audio_tracks"]:
        i = tr["index"]
        err = ffmpeg_progress(["-i", str(src), "-map", f"0:a:{i}", "-af",
                               "silencedetect=noise=-35dB:d=0.5,ebur128=peak=true:framelog=verbose",
                               "-f", "null", "-"], dur, f"{meta['name']} audio track {i + 1}", log, step=50)
        summary = err[err.rfind("Summary:"):]
        num = lambda pat: (float(m.group(1)) if (m := re.search(pat, summary)) and m.group(1) != "-inf" else None)  # noqa: E731
        silence = sum(float(x) for x in re.findall(r"silence_duration: ([\d.]+)", err))
        starts = re.findall(r"silence_start: ([\d.]+)", err)
        if len(starts) > len(re.findall(r"silence_end", err)):
            silence += dur - float(starts[-1])
        db = window_db(pcm(src, i, 16000), 800)          # 50 ms windows
        floor, level = float(np.percentile(db, 10)), float(np.percentile(db, 95))
        if first_db is None:
            first_db = db
        results.append({"index": i, "integrated_lufs": num(r"I:\s+(-?[\d.]+|-inf) LUFS"),
                        "lra_lu": num(r"LRA:\s+([\d.]+) LU"), "true_peak_dbfs": num(r"Peak:\s+(-?[\d.]+|-inf) dBFS"),
                        "silence_pct": round(100 * silence / dur, 1) if dur else 0,
                        "noise_floor_db": round(floor, 1), "snr_db": round(level - floor, 1)})
    return results, first_db


def envelope(path: Path, track: int | None, seconds: float = 600) -> np.ndarray:
    x = np.abs(pcm(path, track, 8000, seconds))
    n = len(x) // 8
    env = x[:n * 8].reshape(n, 8).mean(1)                 # 1 kHz envelope
    k = 200
    if len(env) > k:
        env = env - np.convolve(env, np.ones(k) / k, mode="same")
    return (env - env.mean()) / (env.std() + 1e-9)


def sync_offset(video_env: np.ndarray, audio_env: np.ndarray) -> tuple[float, float]:
    """Seconds to add to external-audio time to get video time, and a confidence score."""
    n = 1 << int(np.ceil(np.log2(len(video_env) + len(audio_env))))
    c = np.fft.irfft(np.fft.rfft(video_env, n) * np.conj(np.fft.rfft(audio_env, n)), n)
    k = int(np.argmax(c))
    lag = k if k < n // 2 else k - n
    return lag / 1000, float((c[k] - c.mean()) / (c.std() + 1e-9))


# ------------------------------------------------------- transcript signals

def speech_signals(words: list, dur: float, db: np.ndarray | None) -> dict:
    spans: list[list[float]] = []
    for w in sorted(words, key=lambda w: w["start"]):
        if spans and w["start"] - spans[-1][1] < 0.3:
            spans[-1][1] = max(spans[-1][1], w["end"])
        else:
            spans.append([w["start"], w["end"]])
    speech = sum(e - s for s, e in spans)
    out = {"speech_pct": round(100 * speech / dur, 1) if dur else 0,
           "words_per_min": round(len(words) / (speech / 60)) if speech > 1 else 0}
    if db is not None and len(db) > 1:
        t = np.arange(len(db)) * 0.05
        in_speech = np.zeros(len(db), bool)
        for s, e in spans:
            in_speech |= (t > s - 0.2) & (t < e + 0.2)
        gaps = ~in_speech
        if gaps.sum() > 20:
            floor = float(np.percentile(db, 10))
            out["sound_in_pauses_pct"] = round(100 * float(np.mean(db[gaps] > floor + 15)), 1)
    return out


# ------------------------------------------------------------------- report

def color_line(m: dict) -> str:
    c = f"{m['bit_depth']}-bit {m['color']['transfer'] or 'untagged'}"
    if m.get("hdr"):
        c += f" · HDR {m['hdr']}"
    if m.get("log_hint"):
        c += f" · LOG per metadata: {m['log_hint']}"
    elif m.get("faces", {}).get("image", {}).get("flat"):
        c += " · flat image: POSSIBLE LOG" + (" (10-bit)" if m["bit_depth"] >= 10 else
                                               " (8-bit: could also be fog/low contrast)")
    return c


def write_report(out: Path, metas: list, externals: list, transcript_note: str | None) -> Path:
    lines = ["# Material", ""]
    for m in metas:
        if m["kind"] != "video":
            continue
        lines.append(f"## {m['name']}")
        lines.append(f"- {m['codec']} {m['display_width']}×{m['display_height']} ({m['orientation']}) · "
                     f"{m['fps']} fps{' (VFR)' if m['vfr'] else ''} · {tc(m['duration'])}"
                     + (f" · {m['camera']}" if m.get("camera") else "")
                     + (f" · recorded {m['recorded'][:19].replace('T', ' ')}" if m.get("recorded") else ""))
        lines.append(f"- color: {color_line(m)}")
        sig, f, sh = m.get("speech", {}), m.get("faces", {}), m.get("shots", {})
        parts = []
        if sig:
            parts += [f"speech {sig['speech_pct']}%", f"{sig['words_per_min']} words/min"]
            if "sound_in_pauses_pct" in sig:
                parts.append(f"sound in pauses {sig['sound_in_pauses_pct']}%"
                             + (" (music/ambience likely)" if sig["sound_in_pauses_pct"] > 60 else ""))
        if f:
            parts.append(f"face in {f['pct_with_face']}% of samples"
                         + (f" (median height {f['median_face_height'] * 100:.0f}% of frame, up to "
                            f"{f['max_faces']} faces)" if f.get("median_face_height") else ""))
        if sh:
            parts.append(f"{sh['shot_changes_per_min']} shot changes/min")
        if m.get("broll_candidate"):
            parts.append("→ B-ROLL CANDIDATE")
        lines.append("- signals: " + " · ".join(parts))
        for a in m.get("audio", []):
            lines.append(f"- audio track {a['index'] + 1}: {a['integrated_lufs']} LUFS · peak "
                         f"{a['true_peak_dbfs']} dBTP · noise floor {a['noise_floor_db']} dB · SNR {a['snr_db']} dB · "
                         f"silence {a['silence_pct']}%")
        if m.get("warnings"):
            lines.append("- warnings: " + " · ".join(m["warnings"]))
        if sh.get("sheets"):
            lines.append(f"- contact sheets: {', '.join('contact/' + s for s in sh['sheets'])}")
        lines.append("")
    if externals:
        lines.append("## External audio")
        lines += [f"- {e['name']}: " + (f"matches {e['video']} — place at video time {e['offset_s']:+.3f} s "
                                         f"(confidence {e['confidence']:.0f})" if e.get("video")
                                         else "no reliable match with any video") for e in externals]
        lines.append("")
    if transcript_note:
        lines += [f"Transcript: {transcript_note}", ""]
    path = out / "material.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    (out / "material.json").write_text(json.dumps({"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                                                   "files": metas, "external_audio": externals},
                                                  indent=1, ensure_ascii=False), encoding="utf-8")
    return path


# --------------------------------------------------------------------- main

def discover(inputs: list[str]) -> list[Path]:
    files: list[Path] = []
    for i in inputs:
        p = Path(i)
        if p.is_dir():
            files += sorted(f for f in p.rglob("*") if f.suffix.lower() in VIDEO_EXT | AUDIO_EXT)
        elif p.is_file():
            files.append(p)
        else:
            die(f"input not found: {p}")
    return files


def start_transcription(files: list[Path], out: Path, args, log: Log) -> subprocess.Popen | None:
    if args.no_transcribe:
        return None
    if not args.lang:
        die("pass --lang (always set the transcription language explicitly) or --no-transcribe")
    cmd = [tool("uv"), "run"]
    if shutil.which("nvidia-smi"):
        cmd += ["--with", "nvidia-cublas-cu12", "--with", "nvidia-cudnn-cu12>=9,<10"]
    cmd += [str(Path(__file__).parent / "transcribe.py"), *map(str, files), "--out", str(out), "--lang", args.lang]
    if args.model:
        cmd += ["--model", args.model]
    if args.glossary:
        cmd += ["--glossary", args.glossary]
    log(f"transcription started in parallel (progress: {out / 'transcribe.log'})")
    return subprocess.Popen(cmd, stdout=open(out / "transcribe.log", "w", encoding="utf-8"),
                            stderr=subprocess.STDOUT)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="*", default=["00-raw"])
    ap.add_argument("--out", default="01-ingest")
    ap.add_argument("--lang", help="transcription language code, e.g. es, en, pt")
    ap.add_argument("--model", help="Whisper model (default chosen by transcribe.py)")
    ap.add_argument("--glossary", help="comma-separated names, brands and terms to spell right")
    ap.add_argument("--no-transcribe", action="store_true")
    ap.add_argument("--force", action="store_true", help="ignore cached results")
    args = ap.parse_args()

    tool("ffmpeg"), tool("ffprobe")
    out = Path(args.out)
    (out / "contact").mkdir(parents=True, exist_ok=True)
    log = Log(out / "ingest.log")
    cache = Cache(out / ".cache.json", args.force)
    files = discover(args.inputs)
    if not files:
        die("no video or audio files found")

    log(f"probing {len(files)} file(s)")
    # Recording order (metadata creation time), falling back to the file name.
    metas = sorted((detect(f) for f in files), key=lambda m: (m["recorded"] or "~", m["name"]))
    seen: dict[str, int] = {}
    for m in metas:
        stem = Path(m["name"]).stem
        seen[stem] = seen.get(stem, 0) + 1
        m["stem"] = stem if seen[stem] == 1 else f"{stem}_{seen[stem]}"
    videos = [m for m in metas if m["kind"] == "video"]
    externals = [m for m in metas if m["kind"] == "audio"]
    tx = start_transcription([Path(m["file"]) for m in (videos or externals)], out, args, log)

    font = find_font()
    faces_all, first_db = {}, {}
    for m in videos:
        m["warnings"] = []
        if m["vfr"]:
            m["warnings"].append("VFR (normalized to CFR)")
        if m["hdr"]:
            m["warnings"].append(f"HDR {m['hdr']} (converted to SDR for analysis/editing; grade from the original)")
        if m["rotation"]:
            m["warnings"].append(f"rotation metadata {m['rotation']}°")
        if len(m["audio_tracks"]) > 1:
            m["warnings"].append(f"{len(m['audio_tracks'])} audio tracks")
        if not m["audio_tracks"]:
            m["warnings"].append("no audio")
        src = normalize(m, out, cache, log) or Path(m["file"])
        m["analysis_source"] = str(src)

        cached = out / ".analysis" / f"{m['stem']}.json"
        if cached.exists() and cache.fresh(f"analysis:{m['stem']}", src):
            saved = json.loads(cached.read_text(encoding="utf-8"))
            m.update(saved["meta"])
            faces_all[m["name"]] = saved["faces"]
            log(f"{m['name']}: analysis is up to date")
        else:
            with ThreadPoolExecutor(3) as pool:
                f_sheets = pool.submit(sheets_and_scenes, m, src, out / "contact", font, log)
                f_frames = pool.submit(analyze_frames, m, src, log)
                f_audio = pool.submit(analyze_audio, m, log)
                m["shots"] = f_sheets.result()
                m["faces"], faces_all[m["name"]] = f_frames.result()
                m["audio"], db = f_audio.result()
                if db is not None:
                    first_db[m["name"]] = db
            cached.parent.mkdir(exist_ok=True)
            cached.write_text(json.dumps({"meta": {k: m[k] for k in ("shots", "faces", "audio")},
                                          "faces": faces_all[m["name"]]}), encoding="utf-8")
            cache.mark(f"analysis:{m['stem']}", src)
        if m["audio_tracks"] and m["name"] not in first_db:
            first_db[m["name"]] = window_db(pcm(Path(m["file"]), 0, 16000), 800)
        if len(m.get("audio", [])) > 1:
            best = max(m["audio"], key=lambda a: a["snr_db"])
            m["suggested_audio_track"] = best["index"]
            if best["index"] != 0:
                m["warnings"].append(f"best audio is track {best['index'] + 1} (transcript used track 1)")
        if m.get("faces", {}).get("image", {}).get("flat") or m.get("log_hint"):
            m["warnings"].append("possible LOG footage: confirm and apply the matching conversion LUT")
        log(f"{m['name']}: done")

    (out / "faces.json").write_text(json.dumps({"fps": FACE_FPS, "box": "x, y, w, h, score (0-1)",
                                                "files": faces_all}), encoding="utf-8")

    ext_report = []
    if videos and externals:
        log("syncing external audio")
        venv = {m["name"]: envelope(Path(m["file"]), 0) for m in videos if m["audio_tracks"]}
        for e in externals:
            aenv = envelope(Path(e["file"]), None)
            best = max(((name, *sync_offset(v, aenv)) for name, v in venv.items()), key=lambda r: r[2],
                       default=None)
            entry = {"name": e["name"], "file": e["file"]}
            if best and best[2] > 8:
                entry.update({"video": best[0], "offset_s": round(best[1], 3), "confidence": round(best[2], 1)})
            ext_report.append(entry)

    note = None
    if tx:
        log("waiting for transcription…")
        tx.wait()
        tjson = out / "transcript.json"
        if tx.returncode == 0 and tjson.exists():
            data = json.loads(tjson.read_text(encoding="utf-8"))["files"]
            for m in videos:
                t = data.get(m["name"])
                if t:
                    words = [w for s in t["segments"] for w in s.get("words", [])]
                    m["speech"] = speech_signals(words, m["duration"], first_db.get(m["name"]))
            note = "transcript.md (condensed) / transcript.json (word level)"
        else:
            note = f"FAILED — see {out / 'transcribe.log'}"
            log(f"transcription failed, see {out / 'transcribe.log'}")

    # B-roll candidates: little speech, and no face talking to camera.
    for m in videos:
        speech = m.get("speech", {}).get("speech_pct")
        f = m.get("faces", {})
        talking_face = f.get("pct_with_face", 0) > 50 and (f.get("median_face_height") or 0) > 0.12
        if (speech is not None and speech < 25 and not talking_face) or (speech is None and not talking_face):
            m["broll_candidate"] = True

    report = write_report(out, metas, ext_report, note)
    log(f"ingest finished → {report}")


if __name__ == "__main__":
    main()
