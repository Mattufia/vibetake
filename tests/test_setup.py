# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26"]
# ///
"""Setup tests (setup.py, and the tool lookup the other scripts use).

    uv run tests/test_setup.py

1. check writes a machine profile (in VIBETAKE_HOME, never in the plugin) with the
   tools' absolute paths, the GPU list, the working encoder and the ffmpeg filters.
2. install without --yes only prints the plan, with the right package manager command.
3. A tool missing from PATH but recorded in the profile (winget installed it a minute
   ago) is still found by the scripts — ffprobe next to ffmpeg too.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "vibetake" / "skills" / "edit" / "scripts"


def run(env: dict, *a: str) -> str:
    p = subprocess.run(["uv", "run", str(SCRIPTS / "setup.py"), *a], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return p.stdout + p.stderr


def main() -> None:
    checks = {}
    home = Path(tempfile.mkdtemp(prefix="vibetake-home-"))
    env = {**os.environ, "VIBETAKE_HOME": str(home)}

    out = run(env, "check")
    prof_path = home / "machine.json"
    prof = json.loads(prof_path.read_text(encoding="utf-8")) if prof_path.exists() else {}
    checks["check: profile written in VIBETAKE_HOME"] = bool(prof)
    checks["check: nothing written inside the plugin"] = not list(ROOT.joinpath("plugins").rglob("machine.json"))
    ff = prof.get("tools", {}).get("ffmpeg", {}).get("path")
    checks["check: ffmpeg found with its absolute path"] = bool(ff) and Path(ff).is_absolute()
    checks["check: encoder tested, filters listed"] = bool(prof.get("ffmpeg", {}).get("master_encoder")) and \
        "subtitles" in prof.get("ffmpeg", {}).get("filters", {})
    checks["check: GPUs, RAM, disk reported"] = "gpus" in prof and prof.get("ram_gb") and "disk_free_gb" in prof
    checks["check: readable table"] = "required" in out and "profile →" in out
    checks["check: yt-dlp listed, no auto-editor"] = "yt-dlp" in out and "auto-editor" not in out

    # 2. Pretend exiftool is missing: install shows the plan and doesn't run it.
    prof["tools"]["exiftool"] = {"path": None, "version": None}
    prof["package_manager"] = {"Windows": "winget", "Darwin": "brew"}.get(sys.platform.title()
                                                                        .replace("Win32", "Windows"), "apt")
    prof_path.write_text(json.dumps(prof), encoding="utf-8")
    out = run(env, "install", "--only", "exiftool")
    want = {"winget": "OliverBetz.ExifTool", "brew": "brew install exiftool", "apt": "libimage-exiftool-perl"}
    checks[f"install: dry-run plan with {prof['package_manager']}"] = want[prof["package_manager"]] in out and \
        "dry run" in out

    prof["tools"]["yt-dlp"] = {"path": None, "version": None}
    prof_path.write_text(json.dumps(prof), encoding="utf-8")
    out = run(env, "install", "--only", "yt-dlp")
    checks["install: yt-dlp as an isolated Python tool, plan only"] = "uv tool install yt-dlp" in out and         "dry run" in out

    # 3. Tool lookup through the profile when PATH doesn't have it.
    if ff:
        fake = Path(tempfile.mkdtemp(prefix="vibetake-tools-"))
        ext = Path(ff).suffix
        for n in ("ffmpeg", "ffprobe"):
            shutil.copy(Path(ff).with_name(n + ext), fake / (n + ext))
        prof["tools"]["ffmpeg"]["path"] = str(fake / ("ffmpeg" + ext))
        prof_path.write_text(json.dumps(prof), encoding="utf-8")
        code = ("import sys; sys.path.insert(0, r'%s'); import _common; "
                "print(_common.tool('ffmpeg')); print(_common.tool('ffprobe'))" % SCRIPTS)
        env2 = {**env, "PATH": os.pathsep.join(p for p in os.environ["PATH"].split(os.pathsep)
                                              if not (Path(p) / ("ffmpeg" + ext)).exists())}
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env2)
        found = p.stdout.split()
        checks[f"lookup: ffmpeg and ffprobe found through the profile ({found})"] = (
            len(found) == 2 and all(str(fake) in f for f in found))
        shutil.rmtree(fake, ignore_errors=True)

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(home, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
