# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""Organize tests: a folder of loose files becomes a project, and --undo restores it.

    uv run tests/test_organize.py
"""
from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SCRIPT = Path(__file__).resolve().parents[1] / "plugins" / "vibetake" / "skills" / "edit" / "scripts" / "organize.py"


def png(path: Path, alpha: bool) -> None:
    """A 1×1 PNG, RGBA (color type 6) or RGB (2)."""
    def chunk(t: bytes, d: bytes) -> bytes:
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    ct = 6 if alpha else 2
    raw = b"\x00" + (b"\x00" * (4 if alpha else 3))
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, ct, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def run(cwd: Path, *a: str) -> str:
    p = subprocess.run(["uv", "run", str(SCRIPT), *a], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return p.stdout + p.stderr


def tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)).replace("\\", "/") for p in root.rglob("*") if p.is_file()
            and not p.name.startswith(".")}


def main() -> None:
    checks = {}
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-org-"))
    for n in ("DJI_0001.MP4", "DJI_0002.MP4", "mic.wav", "foto.jpg", "guion.docx", "Montserrat-Black.ttf",
              "camara.cube", "musica-fondo.mp3", "referencia-portada.jpg"):
        (tmp / n).write_bytes(b"x")
    png(tmp / "logo-acme.png", alpha=False)
    png(tmp / "sello.png", alpha=True)
    png(tmp / "captura.png", alpha=False)
    (tmp / "Camara B").mkdir()
    (tmp / "Camara B" / "C0001.MP4").write_bytes(b"x")
    (tmp / "notas").mkdir()
    (tmp / "notas" / "ideas.txt").write_text("x")
    before = tree(tmp)

    out = run(tmp, "--dry-run")
    checks["dry run moves nothing"] = tree(tmp) == before and "would move" in out

    run(tmp)
    after = tree(tmp)
    checks["video and audio → 00-raw/"] = {"00-raw/DJI_0001.MP4", "00-raw/DJI_0002.MP4", "00-raw/mic.wav"} <= after
    checks["a folder with footage moves whole"] = "00-raw/Camara B/C0001.MP4" in after
    checks["photos → 00-raw/"] = {"00-raw/foto.jpg", "00-raw/captura.png"} <= after
    checks["logo by name or transparency, fonts, LUTs → brand/"] = {
        "brand/logo-acme.png", "brand/sello.png", "brand/Montserrat-Black.ttf", "brand/camara.cube"} <= after
    checks["documents and other folders stay"] = {"guion.docx", "notas/ideas.txt"} <= after
    checks["music and references go to their folders"] = {"music/musica-fondo.mp3",
                                                         "references/referencia-portada.jpg"} <= after
    checks["the user's folders exist, even empty"] = all((tmp / d).is_dir() for d in ("brand", "references", "music"))
    checks["move log written"] = (tmp / "00-raw" / ".organized.json").exists()

    # New file later: only it moves; a name clash never overwrites.
    (tmp / "DJI_0001.MP4").write_bytes(b"new")
    out = run(tmp)
    checks["new loose file moves, nothing overwritten"] = (tmp / "00-raw" / "DJI_0001 (2).MP4").read_bytes() == b"new" \
        and (tmp / "00-raw" / "DJI_0001.MP4").read_bytes() == b"x"
    checks["already organized → nothing to do"] = "already organized" in run(tmp)

    run(tmp, "--undo")
    restored = tree(tmp)
    checks["undo restores the original layout"] = before <= restored and not (tmp / "00-raw").exists() \
        and not (tmp / "brand").exists()

    empty = Path(tempfile.mkdtemp(prefix="vibetake-org-empty-"))
    (empty / "guion.docx").write_bytes(b"x")
    checks["no footage → says so, moves nothing"] = "no video or audio" in run(empty) and tree(empty) == {"guion.docx"}

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    shutil.rmtree(tmp, ignore_errors=True)
    shutil.rmtree(empty, ignore_errors=True)
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
