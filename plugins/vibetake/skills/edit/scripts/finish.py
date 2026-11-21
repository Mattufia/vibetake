# /// script
# requires-python = ">=3.10,<3.14"
# ///
"""Close the project when the user says it's all done: free the disk, keep the finals.

    uv run finish.py          # lists what would go and how much it frees (deletes nothing)
    uv run finish.py --yes    # deletes it — only after the user confirmed

Kept: 00-raw/ (the originals, never touched), brand/, project.md, the latest version of each
video in 03-delivery/ with its .srt, covers, copy and QA report, and every small text file in
01-ingest/ and 02-work/ (edit, transcript, captions, graphics code): a later change starts
from the edit, not from zero (heavy steps like color or AI upscaling run again).
Removed: media and other heavy files in 01-ingest/ and 02-work/, node_modules, older
versions of the videos.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

TEXT = {".json", ".md", ".txt", ".srt", ".ass", ".vtt", ".csv", ".tsx", ".ts", ".js", ".jsx", ".css", ".html", ".cube"}
TEXT_MAX = 5_000_000          # a text file bigger than this is data, not a recipe


def doomed(root: Path) -> list[Path]:
    """Files and folders to delete under the project root."""
    out = []
    for d in ("01-ingest", "02-work"):
        for p in sorted((root / d).rglob("*")) if (root / d).is_dir() else []:
            if any(q.name == "node_modules" for q in p.parents):
                continue                                   # inside a folder already listed
            if p.is_dir():
                if p.name == "node_modules":
                    out.append(p)
            elif p.suffix.lower() not in TEXT or p.stat().st_size > TEXT_MAX:
                out.append(p)
    newest: dict[str, tuple[int, Path]] = {}
    for p in (root / "03-delivery").glob("*_v*.*"):
        m = re.search(r"_v(\d+)(_[^_.]+)?$", p.stem)
        if not m:
            continue
        key = p.stem[:m.start()] + (m.group(2) or "") + p.suffix
        n = int(m.group(1))
        if key in newest and newest[key][0] > n:
            out.append(p)
        else:
            if key in newest:
                out.append(newest[key][1])
            newest[key] = (n, p)
    return out


def size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--yes", action="store_true", help="delete (the user confirmed)")
    ap.add_argument("--root", default=".")
    args = ap.parse_args()
    root = Path(args.root)
    if not any((root / "03-delivery").glob("*_v*.mp4")):
        sys.exit("ERROR: no final video in 03-delivery/ — nothing to close yet")
    items = doomed(root)
    sizes = {p: size(p) for p in items}
    by_dir: dict[str, int] = {}
    for p, s in sizes.items():
        top = p.relative_to(root).parts
        k = "/".join(top[:2]) if top[0] != "03-delivery" else "03-delivery (older versions)"
        by_dir[k] = by_dir.get(k, 0) + s
    for k, s in sorted(by_dir.items(), key=lambda kv: -kv[1]):
        print(f"  {s / 1e9:7.2f} GB  {k}")
    total = sum(sizes.values())
    raw = size(root / "00-raw") if (root / "00-raw").is_dir() else 0
    print(f"{'freed' if args.yes else 'would free'}: {total / 1e9:.2f} GB in {len(items)} items · "
          f"kept: finals, project.md, the edit's text files; 00-raw ({raw / 1e9:.2f} GB) untouched")
    if not args.yes:
        print("nothing deleted: run again with --yes once the user confirms")
        return
    for p in items:
        shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
    for d in ("01-ingest", "02-work"):                     # folders left empty
        for p in sorted((root / d).rglob("*"), key=lambda q: -len(q.parts)) if (root / d).is_dir() else []:
            if p.is_dir() and not any(p.iterdir()):
                p.rmdir()


if __name__ == "__main__":
    main()
