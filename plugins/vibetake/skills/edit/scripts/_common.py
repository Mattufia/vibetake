"""Shared helpers for the vibetake scripts (no third-party imports here)."""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

# Windows consoles and pipes default to a legacy codepage; our output has →, ×, °…
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

VIDEO_EXT ={".mp4", ".mov", ".m4v", ".mkv", ".avi", ".mts", ".m2ts", ".mxf", ".webm", ".lrv", ".insv"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".aif", ".aiff"}

HOME = Path(os.environ.get("VIBETAKE_HOME", Path.home() / ".vibetake"))


def die(msg: str) -> None:
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def tool(name: str, required: bool = True) -> str | None:
    path = shutil.which(name) or _profile_tool(name)
    if not path and required:
        die(f"'{name}' not found. Run /vibetake:setup (or setup.py check / install) and retry.")
    return path


def _profile_tool(name: str) -> str | None:
    """A tool setup.py found outside the PATH (e.g. installed by winget a minute ago, before
    the terminal's PATH refreshes). ffprobe lives next to ffmpeg."""
    try:
        tools = json.loads((HOME / "machine.json").read_text(encoding="utf-8")).get("tools", {})
    except (OSError, ValueError):
        return None
    key = "ffmpeg" if name == "ffprobe" else name
    p = (tools.get(key) or {}).get("path")
    if not p:
        return None
    if name == "ffprobe":
        q = Path(p).with_name("ffprobe" + Path(p).suffix)
        return str(q) if q.exists() else None
    return p if Path(p).exists() else None


class Log:
    """Prints progress lines and mirrors them to a log file."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(path, "a", encoding="utf-8")
        self._lock = threading.Lock()

    def __call__(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        with self._lock:
            print(line, flush=True)
            self._f.write(line + "\n")
            self._f.flush()


def tc(seconds: float) -> str:
    """Seconds -> MM:SS.s (or H:MM:SS.s)."""
    seconds = max(0.0, seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h >= 1:
        return f"{int(h)}:{int(m):02d}:{s:04.1f}"
    return f"{int(m):02d}:{s:04.1f}"


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        raise RuntimeError(f"Command failed ({p.returncode}): {' '.join(cmd[:6])}...\n{p.stderr[-2000:]}")
    return p


def ffprobe(path: Path) -> dict:
    p = run([tool("ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    return json.loads(p.stdout)


def ffmpeg_progress(args: list[str], duration: float, label: str, log: Log, step: int = 10) -> str:
    """Runs ffmpeg showing percentage progress. Returns stderr. Raises on failure."""
    cmd = [tool("ffmpeg"), "-hide_banner", "-nostats", "-progress", "pipe:1", "-y", *args]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    err: list[str] = []
    t = threading.Thread(target=lambda: err.extend(proc.stderr), daemon=True)
    t.start()
    next_pct = step
    for line in proc.stdout:
        if line.startswith("out_time_us=") and duration > 0:
            try:
                pct = int(line.split("=")[1]) / 1e6 / duration * 100
            except ValueError:
                continue
            if pct >= next_pct:
                log(f"{label}: {min(pct, 100):.0f}%")
                next_pct = (int(pct) // step + 1) * step
    proc.wait()
    t.join()
    stderr = "".join(err)
    if proc.returncode != 0:
        raise RuntimeError(f"{label} failed:\n{stderr[-2000:]}")
    return stderr


def filter_path(p: Path | str) -> str:
    """Escapes a path for use inside an ffmpeg filter option value."""
    return str(p).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def find_font() -> str | None:
    candidates = {
        "Windows": ["C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/segoeui.ttf"],
        "Darwin": ["/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc"],
    }.get(platform.system(), ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                               "/usr/share/fonts/TTF/DejaVuSans.ttf"])
    return next((c for c in candidates if Path(c).exists()), None)


class Cache:
    """Skips steps whose source file and parameters haven't changed."""

    def __init__(self, path: Path, force: bool = False):
        self.path = path
        self.force = force
        try:
            self.data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(src: Path, params: str = "") -> str:
        st = src.stat()
        return f"{st.st_size}:{int(st.st_mtime)}:{params}"

    def fresh(self, step: str, src: Path, params: str = "") -> bool:
        return not self.force and self.data.get(step) == self.key(src, params)

    def mark(self, step: str, src: Path, params: str = "") -> None:
        with self._lock:
            self.data[step] = self.key(src, params)
            self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")


# ---------- deliverable names and versions ----------

DELIVERY = Path("03-delivery")


def deliverable_name(edl: dict | None = None) -> str:
    """Base name of the deliverables: edl.json's "name", else the project folder's name."""
    import re
    import unicodedata
    raw = (edl or {}).get("name") or Path.cwd().name
    s = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode()
    s = re.sub(r"[^\w-]+", "-", s).strip("-_").lower()
    return s or "edit"


def versions(name: str, folder: Path = DELIVERY) -> list[tuple[int, Path]]:
    """Existing <name>_v<N>.mp4 in the delivery folder, oldest first."""
    import re
    out = []
    for p in folder.glob(f"{name}_v*.mp4"):
        m = re.fullmatch(re.escape(name) + r"_v(\d+)", p.stem)
        if m:
            out.append((int(m.group(1)), p))
    return sorted(out)


def latest_video(folder: Path = DELIVERY) -> Path | None:
    """The newest versioned video in the delivery folder (any name), or None."""
    import re
    best = None
    for p in folder.glob("*_v*.mp4"):
        m = re.search(r"_v(\d+)$", p.stem)
        if m and (best is None or (int(m.group(1)), p.stat().st_mtime) > best[0]):
            best = ((int(m.group(1)), p.stat().st_mtime), p)
    return best[1] if best else None


# ---------- the project's typefaces ----------

def project_font(role: str = "display") -> dict | None:
    """The font this video uses for a role — display (headlines, covers), text, captions —
    as {"file", "name", "family", "weight"}: from 02-work/design.json when the design names
    one, otherwise from the fonts downloaded for the project (fonts.py): the heaviest
    upright weight for display/captions, the lightest from 400 up for text. None if the
    project has no fonts yet (system fonts are never a fallback)."""
    design = Path("02-work/design.json")
    if design.exists():
        try:
            d = json.loads(design.read_text(encoding="utf-8")).get("fonts", {})
            f = d.get(role) or (d.get("display") if role == "captions" else None)
            if f and Path(f.get("file", "")).exists():
                return f
        except ValueError:
            pass
    idx = Path("02-work/fonts.json")
    if not idx.exists():
        return None
    fonts = [f for f in json.loads(idx.read_text(encoding="utf-8")) if Path(f["file"]).exists() and not f["italic"]]
    if not fonts:
        return None
    if role == "text":
        cands = [f for f in fonts if f["weight"] >= 400] or fonts
        return min(cands, key=lambda f: f["weight"])
    return max(fonts, key=lambda f: f["weight"])


# --------------------------------------------------------------- HDR → SDR

def filter_works(args: list[str], src: str = "testsrc2=s=320x180:d=0.3") -> bool:
    p = run([tool("ffmpeg"), "-hide_banner", "-v", "error", "-f", "lavfi", "-i", src, *args, "-f", "null", "-"],
            check=False)
    return p.returncode == 0


_TONEMAP: tuple | None = None
HDR_TEST_SRC = ("testsrc2=s=320x180:d=0.3,format=yuv420p10le,"
                "setparams=color_primaries=bt2020:color_trc=arib-std-b67:colorspace=bt2020nc")


def pick_tonemap() -> tuple[str, str]:
    """HDR -> SDR Rec.709. libplacebo (BT.2390, perceptual gamut mapping) or zscale+hable fallback."""
    global _TONEMAP
    if _TONEMAP is None:
        lp = ("libplacebo=tonemapping=bt.2390:gamut_mode=perceptual:colorspace=bt709:"
              "color_primaries=bt709:color_trc=bt709:range=tv:format=yuv420p10le")
        zs = ("zscale=t=linear:npl=203,format=gbrpf32le,zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
              "zscale=t=bt709:m=bt709:r=tv,format=yuv420p10le")
        _TONEMAP = ("libplacebo", lp) if filter_works(["-vf", lp], HDR_TEST_SRC) else ("zscale+hable", zs)
    return _TONEMAP


def material_index(ingest: str | Path = "01-ingest") -> dict:
    """material.json by file name ({} without an ingest)."""
    p = Path(ingest) / "material.json"
    return {m["name"]: m for m in json.loads(p.read_text(encoding="utf-8"))["files"]} if p.exists() else {}


def sdr_source(m: dict) -> tuple[str, str]:
    """(file, filter) giving the SDR picture of a file: HDR → its normalized copy, or the
    original tone-mapped on the fly when the copy was cleaned up; anything else → as is."""
    if not m.get("hdr"):
        return m["file"], ""
    norm = (m.get("normalized") or {}).get("file")
    if norm and Path(norm).exists():
        return norm, ""
    return m["file"], pick_tonemap()[1]


def pre_filter(m: dict | None, g: dict | None) -> str:
    """What goes before a file's LUT at render time: the grade's pre-filter, or — for HDR
    material without a grade — the tone mapping, so it never renders washed out."""
    pre = (g or {}).get("pre") or ""
    if m and m.get("hdr") and pick_tonemap()[1] not in pre:
        pre = ",".join(x for x in (pick_tonemap()[1], pre) if x)
    return pre


# ------------------------------------------------------------------- small

def seconds(t: str) -> float:
    """'12.5', '1:03.2' or '1:02:03' → seconds."""
    parts = [float(x) for x in t.strip().split(":")]
    return sum(x * 60 ** i for i, x in enumerate(reversed(parts)))


def frac(s: str | None) -> float:
    """ffprobe's '30000/1001' → 29.97 (0 when missing or malformed)."""
    try:
        n, d = (s or "0/0").split("/")
        return float(n) / float(d) if float(d) else 0.0
    except ValueError:
        return 0.0


def load_json(p: Path) -> dict:
    return json.loads(Path(p).read_text(encoding="utf-8"))


def wcag_luminance(rgb) -> float:
    """WCAG relative luminance of an sRGB color given as 0-255 values (0 black, 1 white)."""
    r, g, b = (c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in (float(v) / 255 for v in rgb))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def channel_pan(layout: dict | None) -> str:
    """A mic on one channel only → that channel in the center; one mic per channel (one per
    person) → both mixed to the center; real stereo → untouched."""
    if not layout or layout.get("layout") == "stereo" or not layout.get("live"):
        return ""
    live = layout["live"]
    return "pan=mono|c0=" + "+".join(f"{1 / len(live):.3f}*c{c}" for c in live)


def work_paths(variant: str | None = None) -> dict[str, Path]:
    """Where a version of the edit keeps its pieces. A variant (hookB, 4x5…) has its own cut,
    captions and graphics layer next to the main video's, never over them."""
    v = f"_{variant}" if variant else ""
    w = Path("02-work")
    return {"edl": w / f"edl{v}.json", "subs": w / f"subs{v}.ass", "cues": w / f"cues{v}.json",
            "plan": w / f"graphics{v}.json", "layer": w / f"graphics{v}.mov"}


# ------------------------------------------------------ writing systems

SENTENCE_END = (".", "?", "!", "…", "。", "！", "？", "｡", "؟", "۔", "।")
CLAUSE_END = (",", ";", ":", "，", "、", "；", "：", "،", "؛")
# Caption rules per writing system: how words join (Chinese, Japanese and Thai have no spaces),
# characters per line, reading speed (characters per second), glyph width (share of the font
# size; CJK glyphs are full width) and direction. Values from common streaming subtitle guides
# (per-language timed-text style guides) — verify when a language matters.
WRITING = {
    "default": {"join": " ", "line": 42, "cps": 17, "glyph": 0.68, "rtl": False},
    "zh": {"join": "", "line": 16, "cps": 9, "glyph": 1.0},
    "ja": {"join": "", "line": 13, "cps": 4, "glyph": 1.0},
    "ko": {"line": 16, "cps": 12, "glyph": 1.0},
    "th": {"join": "", "line": 35, "cps": 15, "glyph": 0.6},
    "ar": {"rtl": True, "glyph": 0.6}, "fa": {"rtl": True, "glyph": 0.6},
    "ur": {"rtl": True, "glyph": 0.6}, "he": {"rtl": True, "glyph": 0.6},
}


def writing(lang: str | None) -> dict:
    """The caption rules for a language code (es, zh, ar…): its writing system's, else Latin-like."""
    base = (lang or "").lower().split("-")[0]
    return {**WRITING["default"], **WRITING.get(base, {})}
