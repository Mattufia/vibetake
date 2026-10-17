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
