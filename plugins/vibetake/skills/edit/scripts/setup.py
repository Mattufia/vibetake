# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Machine setup: what's here, what's missing, install it, download the heavy models.

    uv run setup.py check                     # table + ~/.vibetake/machine.json (default)
    uv run setup.py install [--only ffmpeg,exiftool,node,realesrgan] [--yes]
    uv run setup.py prefetch [--only whisper,faces,rembg]   # heavy downloads now + a real GPU test

check looks at the OS, CPU, RAM, free disk, the GPU (any vendor) and its VRAM,
ffmpeg (version, x264, the filters the scripts use, which hardware encoder really
works here), uv, exiftool, Node (for Remotion), rembg, Real-ESRGAN and the cached
models. It writes the machine profile to ~/.vibetake/machine.json — outside the
plugin, one per machine — with the absolute path of every tool, so the scripts find
a tool installed a minute ago even before the terminal's PATH refreshes.

install uses the system's package manager (winget on Windows, Homebrew on macOS,
apt/dnf on Linux) for system tools, `uv tool install` for Python tools (yt-dlp),
and downloads Real-ESRGAN's official release to ~/.vibetake/tools/realesrgan.
Without --yes it only prints the plan: show it to the user and ask first.
Nothing already installed is reinstalled.

prefetch downloads, now instead of mid-edit: the Whisper model (~3 GB) plus, with
an NVIDIA GPU, the CUDA libraries (~1 GB) — and transcribes a test clip to prove the
GPU path works; the face detector (~0.3 MB); rembg's portrait model (~1 GB).
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import HOME  # noqa: E402

PROFILE = HOME / "machine.json"
OS = platform.system()                      # Windows / Darwin / Linux
REALESRGAN = ("https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/"
              "realesrgan-ncnn-vulkan-20220424-{}.zip")
REALESRGAN_OS = {"Windows": "windows", "Darwin": "macos", "Linux": "ubuntu"}
WHISPER_MODEL = "large-v3"

# How to install each system tool, per package manager.
PACKAGES = {
    "winget": {"ffmpeg": "Gyan.FFmpeg", "exiftool": "OliverBetz.ExifTool", "node": "OpenJS.NodeJS.LTS",
               "uv": "astral-sh.uv"},
    "brew": {"ffmpeg": "ffmpeg", "exiftool": "exiftool", "node": "node", "uv": "uv"},
    "apt": {"ffmpeg": "ffmpeg", "exiftool": "libimage-exiftool-perl", "node": "nodejs"},
    "dnf": {"ffmpeg": "ffmpeg", "exiftool": "perl-Image-ExifTool", "node": "nodejs"},
}
NEED = {  # tool: (level, what it's for)
    "ffmpeg": ("required", "everything: probe, cut, color, render"),
    "uv": ("required", "runs the scripts and their Python dependencies"),
    "exiftool": ("recommended", "camera and log-profile detection"),
    "node": ("optional", "Remotion graphics layer (Node 18+)"),
    "realesrgan": ("optional", "AI upscaling of stretched vertical crops"),
    "yt-dlp": ("optional", "download material from a link (YouTube, etc.)"),
}


def sh(cmd: list[str], timeout: float = 60) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return ""


# ---------------------------------------------------------------- finding tools

def winget_dirs() -> list[Path]:
    """Where winget puts tools — visible to new terminals only, so look there too."""
    if OS != "Windows":
        return []
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "Microsoft" / "WinGet"
    dirs = [base / "Links"]
    pk = base / "Packages"
    if pk.exists():
        for d in pk.iterdir():
            dirs += [b for b in d.rglob("bin") if b.is_dir()] + [d]
    dirs += [Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "nodejs",
             Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "ExifTool"]
    return dirs


def find(name: str) -> str | None:
    hit = shutil.which(name)
    if hit:
        return hit
    if name == "realesrgan":
        hit = shutil.which("realesrgan-ncnn-vulkan")
        if hit:
            return hit
        for p in (HOME / "tools" / "realesrgan").glob("realesrgan-ncnn-vulkan*"):
            if p.suffix.lower() in ("", ".exe"):
                return str(p)
        return None
    exe = name + (".exe" if OS == "Windows" else "")
    for d in winget_dirs() + [Path.home() / ".local" / "bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin")]:
        p = d / exe
        if p.exists():
            return str(p)
    return None


def version(name: str, path: str) -> str:
    args = {"ffmpeg": ["-version"], "uv": ["--version"], "exiftool": ["-ver"], "node": ["--version"],
            "realesrgan": ["-h"], "yt-dlp": ["--version"]}[name]
    out = sh([path, *args], timeout=20).strip().splitlines()
    if name == "ffmpeg":
        m = re.search(r"ffmpeg version (\S+)", out[0] if out else "")
        return m.group(1) if m else "?"
    if name == "realesrgan":
        return "ncnn-vulkan"
    if not out:
        return "?"
    m = re.search(r"\d+(\.\d+)+", out[0])
    return m.group(0) if m else out[0].strip()[:30]


def package_manager() -> str | None:
    if OS == "Windows":
        return "winget" if find("winget") else None
    if OS == "Darwin":
        return "brew" if find("brew") else None
    for pm in ("apt", "dnf"):
        if shutil.which(pm):
            return pm
    return None


# ---------------------------------------------------------------- hardware

def gpus() -> list[dict]:
    out = []
    if shutil.which("nvidia-smi"):
        for ln in sh(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                      "--format=csv,noheader,nounits"]).strip().splitlines():
            parts = [x.strip() for x in ln.split(",")]
            if len(parts) == 3 and parts[1].isdigit():
                out.append({"vendor": "NVIDIA", "name": parts[0], "vram_gb": round(int(parts[1]) / 1024, 1),
                            "driver": parts[2]})
    if OS == "Windows":
        raw = sh(["powershell", "-NoProfile", "-Command",
                  "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json"])
        try:
            items = json.loads(raw)
            for it in items if isinstance(items, list) else [items]:
                name = (it.get("Name") or "").strip()
                if not name or any(g["name"] in name or name in g["name"] for g in out):
                    continue
                if re.search(r"basic|virtual|remote|parsec|meta|idd", name, re.I):
                    continue
                vendor = next((v for k, v in (("nvidia", "NVIDIA"), ("amd", "AMD"), ("radeon", "AMD"),
                                              ("intel", "Intel")) if k in name.lower()), "other")
                ram = it.get("AdapterRAM") or 0
                out.append({"vendor": vendor, "name": name,
                            "vram_gb": round(ram / 2 ** 30, 1) if ram else None,
                            "note": "VRAM as reported by Windows (capped at 4 GB)" if ram else ""})
        except ValueError:
            pass
    elif OS == "Darwin":
        try:
            data = json.loads(sh(["system_profiler", "SPDisplaysDataType", "-json"]))
            for it in data.get("SPDisplaysDataType", []):
                out.append({"vendor": "Apple" if "Apple" in it.get("sppci_model", "") else "other",
                            "name": it.get("sppci_model", "?"), "vram_gb": None,
                            "note": "unified memory" if "Apple" in it.get("sppci_model", "") else ""})
        except ValueError:
            pass
    elif not out:
        for ln in sh(["lspci"]).splitlines():
            if re.search(r"VGA|3D|Display", ln):
                name = ln.split(":", 2)[-1].strip()
                vendor = next((v for k, v in (("nvidia", "NVIDIA"), ("amd", "AMD"), ("ati", "AMD"),
                                              ("intel", "Intel")) if k in name.lower()), "other")
                out.append({"vendor": vendor, "name": name, "vram_gb": None})
    return out


def ram_gb() -> float | None:
    try:
        if OS == "Windows":
            v = sh(["powershell", "-NoProfile", "-Command",
                    "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory"]).strip()
            return round(int(v) / 2 ** 30, 1)
        if OS == "Darwin":
            return round(int(sh(["sysctl", "-n", "hw.memsize"]).strip()) / 2 ** 30, 1)
        m = re.search(r"MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text())
        return round(int(m.group(1)) / 2 ** 20, 1) if m else None
    except (ValueError, OSError):
        return None


# ---------------------------------------------------------------- ffmpeg

def ffmpeg_profile(path: str) -> dict:
    enc = sh([path, "-hide_banner", "-encoders"])
    fil = sh([path, "-hide_banner", "-filters"])
    has = lambda name, text: re.search(rf"^\s*\S+\s+{name}\s", text, re.M) is not None  # noqa: E731
    prof = {"x264": has("libx264", enc),
            "filters": {f: has(f, fil) for f in ("subtitles", "drawtext", "lut3d", "zscale", "libplacebo",
                                                 "afftdn", "sidechaincompress", "loudnorm")}}
    # The hardware encoder that really works here (the same test render.py does).
    os.environ["PATH"] = str(Path(path).parent) + os.pathsep + os.environ.get("PATH", "")
    try:
        import render
        prof["master_encoder"] = render.pick(render.MASTER)[0]
        prof["dynamic_crop"] = render.dynamic_crop_ok()
    except Exception as e:  # noqa: BLE001
        prof["master_encoder"] = f"test failed: {e}"
    return prof


# ---------------------------------------------------------------- models

def hf_cached(repo: str) -> bool:
    base = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    return (base / f"models--{repo.replace('/', '--')}").exists()


def models() -> dict:
    # rembg keeps its models in ~/.rembg/models (recent) or ~/.u2net (older, or U2NET_HOME).
    dirs = [Path(os.environ["U2NET_HOME"])] if os.environ.get("U2NET_HOME") else []
    dirs += [Path.home() / ".rembg" / "models", Path.home() / ".u2net"]
    return {"whisper": hf_cached(f"Systran/faster-whisper-{WHISPER_MODEL}"),
            "faces": (HOME / "models" / "face_detection_yunet_2023mar.onnx").exists(),
            "rembg_portrait": any(any(d.glob("birefnet-portrait*")) for d in dirs if d.exists())}


# ---------------------------------------------------------------- commands

def cmd_check(args) -> dict:
    prof = {"checked": time.strftime("%Y-%m-%d %H:%M"), "os": f"{OS} {platform.release()} {platform.machine()}",
            "cpu_cores": os.cpu_count(), "ram_gb": ram_gb(), "gpus": gpus(), "tools": {},
            "package_manager": package_manager()}
    prof["disk_free_gb"] = {"here": round(shutil.disk_usage(Path.cwd()).free / 1e9),
                            "home": round(shutil.disk_usage(Path.home()).free / 1e9)}
    rows = []
    for name, (level, why) in NEED.items():
        path = find(name)
        prof["tools"][name] = {"path": path, "version": version(name, path) if path else None}
        rows.append((name, level, f"{prof['tools'][name]['version']}" if path else "MISSING", why))
    if prof["tools"]["ffmpeg"]["path"]:
        prof["ffmpeg"] = ffmpeg_profile(prof["tools"]["ffmpeg"]["path"])
    prof["models"] = models()
    old = json.loads(PROFILE.read_text(encoding="utf-8")) if PROFILE.exists() else {}
    if old.get("transcription"):
        prof["transcription"] = old["transcription"]
    PROFILE.parent.mkdir(parents=True, exist_ok=True)
    PROFILE.write_text(json.dumps(prof, indent=1, ensure_ascii=False), encoding="utf-8")

    # Report.
    print(f"Machine: {prof['os']} · {prof['cpu_cores']} cores · {prof['ram_gb']} GB RAM · "
          f"free disk {prof['disk_free_gb']['here']} GB here, {prof['disk_free_gb']['home']} GB in home")
    for g in prof["gpus"] or [{"vendor": "none", "name": "no GPU found", "vram_gb": None}]:
        print(f"GPU: {g['vendor']} {g['name']}" + (f" · {g['vram_gb']} GB" if g.get("vram_gb") else "")
              + (f" ({g['note']})" if g.get("note") else ""))
    print()
    print(f"{'tool':<11} {'need':<12} {'status':<34} for")
    for name, level, status, why in rows:
        print(f"{name:<11} {level:<12} {status[:34]:<34} {why}")
    problems = []
    ff = prof.get("ffmpeg")
    if ff:
        missing = [f for f in ("subtitles", "drawtext", "lut3d", "afftdn", "sidechaincompress") if not ff["filters"][f]]
        hdr = "libplacebo" if ff["filters"]["libplacebo"] else "zscale" if ff["filters"]["zscale"] else None
        print(f"\nffmpeg: master encoder {ff['master_encoder']} · x264 {'yes' if ff['x264'] else 'NO'} · "
              f"HDR tone mapping {hdr or 'NONE'} · impact zoom {'yes' if ff.get('dynamic_crop') else 'no'}")
        if missing:
            problems.append(f"ffmpeg lacks {', '.join(missing)}: install a full build (setup.py install --only ffmpeg)")
        if not hdr:
            problems.append("ffmpeg has neither libplacebo nor zscale: HDR footage can't be converted")
        if not ff["x264"] and "libx264" in str(ff["master_encoder"]):
            problems.append("ffmpeg has no working encoder")
    m = prof["models"]
    print(f"models: Whisper {WHISPER_MODEL} {'cached' if m['whisper'] else 'not yet (~3 GB)'} · faces "
          f"{'cached' if m['faces'] else 'not yet'} · rembg portrait {'cached' if m['rembg_portrait'] else 'not yet (~1 GB)'}")
    if prof.get("transcription"):
        print(f"transcription last tested: {prof['transcription']['device']} ({prof['transcription']['date']})")
    for name, (level, _) in NEED.items():
        if level == "required" and not prof["tools"][name]["path"]:
            problems.append(f"{name} is required and missing")
    free = prof["disk_free_gb"]["here"]
    if free < 50:
        problems.append(f"only {free} GB free here: video work eats hundreds of GB")
    if prof["gpus"] and all(g["vendor"] == "NVIDIA" for g in prof["gpus"][:1]) and not m["whisper"]:
        problems.append("run setup.py prefetch to download the models and test transcription on the GPU")
    print()
    for p in problems:
        print("WARNING:", p)
    print(f"profile → {PROFILE}")
    return prof


def install_cmd(pm: str, tool: str) -> list[str] | None:
    pkg = PACKAGES.get(pm, {}).get(tool)
    if not pkg:
        return None
    if pm == "winget":
        return ["winget", "install", "--id", pkg, "-e", "--accept-source-agreements", "--accept-package-agreements"]
    if pm == "brew":
        return ["brew", "install", pkg]
    return ["sudo", pm, "install", "-y", pkg]


def install_realesrgan() -> str:
    dst = HOME / "tools" / "realesrgan"
    url = REALESRGAN.format(REALESRGAN_OS[OS])
    with tempfile.TemporaryDirectory() as tmp:
        z = Path(tmp) / "re.zip"
        print(f"downloading {url} (~45 MB)…", flush=True)
        urllib.request.urlretrieve(url, z)
        dst.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(z) as zf:
            zf.extractall(dst)
    for p in dst.rglob("realesrgan-ncnn-vulkan*"):
        if OS != "Windows":
            p.chmod(0o755)
    return str(dst)


def cmd_install(args) -> None:
    prof = cmd_check(args) if not PROFILE.exists() else json.loads(PROFILE.read_text(encoding="utf-8"))
    want = [x for x in (args.only.split(",") if args.only else NEED) if x in NEED]
    todo = [t for t in want if not prof["tools"].get(t, {}).get("path") or
            (t == "ffmpeg" and any(not v for k, v in prof.get("ffmpeg", {}).get("filters", {}).items()
                                   if k in ("subtitles", "drawtext", "lut3d", "afftdn")))]
    if not todo:
        print("nothing to install")
        return
    pm = prof.get("package_manager")
    plan = []
    for t in todo:
        if t == "realesrgan":
            plan.append((t, None, f"download Real-ESRGAN (official release) → {HOME / 'tools' / 'realesrgan'}"))
        elif t == "yt-dlp":
            # A Python tool: isolated but available system-wide, the same on every OS.
            plan.append((t, ["uv", "tool", "install", "yt-dlp"], "uv tool install yt-dlp"))
        elif t == "uv" and OS == "Linux":
            plan.append((t, ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"], "uv official installer"))
        else:
            c = install_cmd(pm, t) if pm else None
            plan.append((t, c, " ".join(c) if c else f"no package manager found: install {t} by hand"))
    print("Plan:")
    for t, _, how in plan:
        print(f"  {t:<11} {how}")
    if not args.yes:
        print("\n(dry run: re-run with --yes to do it)")
        return
    for t, c, how in plan:
        print(f"\n→ {t}", flush=True)
        try:
            if t == "realesrgan":
                install_realesrgan()
            elif c:
                subprocess.run(c, check=False)
        except Exception as e:  # noqa: BLE001
            print(f"WARNING: {t} failed: {e}")
    print()
    cmd_check(args)


def cmd_prefetch(args) -> None:
    want = args.only.split(",") if args.only else ["faces", "whisper", "rembg"]
    if "faces" in want:
        from ingest import YUNET_URL  # noqa: PLC0415
        p = HOME / "models" / "face_detection_yunet_2023mar.onnx"
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            print("downloading the face detector…", flush=True)
            urllib.request.urlretrieve(YUNET_URL, p)
        print("faces: ready")
    if "whisper" in want:
        ff = shutil.which("ffmpeg") or (json.loads(PROFILE.read_text()).get("tools", {}).get("ffmpeg", {})
                                        .get("path") if PROFILE.exists() else None)
        if not ff:
            sys.exit("ERROR: ffmpeg is needed first (setup.py install --only ffmpeg --yes)")
        with tempfile.TemporaryDirectory() as tmp:
            clip = Path(tmp) / "test.wav"
            subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i",
                            "sine=f=220:d=3,volume=0.3", str(clip)], check=True)
            print(f"Whisper {WHISPER_MODEL}: downloading if needed (~3 GB, plus ~1 GB of CUDA libraries with an "
                  "NVIDIA GPU) and testing on a short clip…", flush=True)
            t0 = time.time()
            p = subprocess.run(["uv", "run", str(Path(__file__).parent / "transcribe.py"), str(clip), "--lang", "en",
                                "--out", str(Path(tmp) / "out")])
            res = Path(tmp) / "out" / "transcript.json"
            if p.returncode or not res.exists():
                print("WARNING: the transcription test failed (see above)")
            else:
                dev = next(iter(json.loads(res.read_text(encoding="utf-8"))["files"].values())).get("device", "?")
                print(f"transcription works on {dev} ({time.time() - t0:.0f} s including downloads)")
                prof = json.loads(PROFILE.read_text(encoding="utf-8")) if PROFILE.exists() else {}
                prof["transcription"] = {"device": dev, "model": WHISPER_MODEL, "date": time.strftime("%Y-%m-%d")}
                PROFILE.parent.mkdir(parents=True, exist_ok=True)
                PROFILE.write_text(json.dumps(prof, indent=1, ensure_ascii=False), encoding="utf-8")
                if "cpu" in dev and any(g["vendor"] == "NVIDIA" for g in gpus()):
                    print("WARNING: there's an NVIDIA GPU but transcription ran on the CPU: check the driver "
                          "(nvidia-smi) — see references/gpu.md")
    if "rembg" in want:
        uvx = shutil.which("uvx")
        if not uvx:
            print("WARNING: uvx not found (it comes with uv)")
        else:
            with tempfile.TemporaryDirectory() as tmp:
                img = Path(tmp) / "t.png"
                ff = shutil.which("ffmpeg")
                if ff:
                    subprocess.run([ff, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=64x64:d=0.1",
                                    "-frames:v", "1", str(img)], check=True)
                    print("rembg portrait model: downloading if needed (~1 GB)…", flush=True)
                    p = subprocess.run([uvx, "--from", "rembg[cpu,cli]", "rembg", "i", "-m", "birefnet-portrait",
                                        str(img), str(Path(tmp) / "o.png")])
                    print("rembg: ready" if p.returncode == 0 else "WARNING: rembg failed (covers fall back to "
                          "no cutout)")
    cmd_check(args)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("check")
    i = sub.add_parser("install")
    i.add_argument("--only", help="comma-separated: " + ",".join(NEED))
    i.add_argument("--yes", action="store_true", help="really run it (otherwise a dry run)")
    p = sub.add_parser("prefetch")
    p.add_argument("--only", help="comma-separated: whisper,faces,rembg")
    args = ap.parse_args()
    {"check": cmd_check, None: cmd_check, "install": cmd_install, "prefetch": cmd_prefetch}[args.cmd](args)


if __name__ == "__main__":
    main()
