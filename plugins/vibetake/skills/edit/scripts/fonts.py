# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["fonttools>=4.50"]
# ///
"""The project's typefaces: chosen for this video, downloaded for it, never the system's.

    uv run fonts.py info "Bricolage Grotesque"                 # exists? weights, category
    uv run fonts.py get "Bricolage Grotesque" --weights 400,800 --lang es
    uv run fonts.py list                                       # what the project has, and the names to use

Families come from Google Fonts (open licenses: OFL, Apache, UFL), downloaded at edit
time as static TTF files into 02-work/fonts/ — one per weight — so the same files
serve the graphics (Remotion), the burned-in subtitles (libass, by the full name
`list` prints) and the covers (Pillow). Each file is checked for the characters the
video's language needs (á é í ó ú ñ ¿ ¡ for Spanish, ç ã õ for Portuguese…): a family
that lacks them is refused. 02-work/fonts.json records family, weight, file,
full name and source, for project.md.

Pick families for the video — its tone, the brand, what each one has to do (a
headline with presence, text that reads small on a phone) — not a default. System
fonts (Arial, Segoe, Helvetica…) are never used for anything the viewer sees.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import HOME, die  # noqa: E402

DIR = Path("02-work/fonts")
INDEX = Path("02-work/fonts.json")      # outside fonts/: libass reads every file there
META_URL = "https://fonts.google.com/metadata/fonts"
META_CACHE = HOME / "cache" / "google-fonts-metadata.json"
CSS_URL = "https://fonts.googleapis.com/css2?family={family}:{axes}&display=swap"
UA = "Wget/1.21"            # an old client gets plain TTF files instead of woff2
LANG_CHARS = {
    "es": "áéíóúüñÁÉÍÓÚÜÑ¿¡",
    "pt": "áâãàçéêíóôõúÁÂÃÀÇÉÊÍÓÔÕÚ",
    "fr": "àâæçéèêëîïôœùûüÿÀÂÆÇÉÈÊËÎÏÔŒÙÛÜŸ«»",
    "it": "àèéìíòóùúÀÈÉÌÍÒÓÙÚ",
    "de": "äöüßÄÖÜ",
    "en": "",
}
BASE_CHARS = "AaBbCcDdEeFfGgHhIiJjKkLlMmNnOoPpQqRrSsTtUuVvWwXxYyZz0123456789.,;:!?-–—\"'()%$€@#&/+"


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def metadata() -> dict:
    """Google Fonts' public catalog (cached for a week; it's the list, not the fonts)."""
    if META_CACHE.exists() and time.time() - META_CACHE.stat().st_mtime < 7 * 86400:
        return json.loads(META_CACHE.read_text(encoding="utf-8"))
    raw = fetch(META_URL).decode("utf-8")
    raw = raw[raw.index("{"):]
    META_CACHE.parent.mkdir(parents=True, exist_ok=True)
    META_CACHE.write_text(raw, encoding="utf-8")
    return json.loads(raw)


def family_info(name: str) -> dict:
    fams = metadata()["familyMetadataList"]
    hit = next((f for f in fams if f["family"].lower() == name.lower()), None)
    if hit:
        return hit
    close = [f["family"] for f in fams if name.lower() in f["family"].lower()][:8]
    die(f"'{name}' isn't on Google Fonts" + (f" — maybe: {', '.join(close)}" if close else ""))


def missing_chars(path: Path, lang: str) -> str:
    from fontTools.ttLib import TTFont
    cmap = TTFont(str(path)).getBestCmap() or {}
    need = BASE_CHARS + LANG_CHARS.get(lang, "")
    return "".join(c for c in need if ord(c) not in cmap)


def names(path: Path) -> tuple[str, str]:
    """(family, full name) as stored in the font: the full name is what libass matches."""
    from fontTools.ttLib import TTFont
    n = TTFont(str(path))["name"]
    fam = n.getDebugName(16) or n.getDebugName(1) or path.stem
    full = n.getDebugName(4) or fam
    return fam, full


def load_index() -> list[dict]:
    return json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else []


def cmd_info(args) -> None:
    f = family_info(args.family)
    weights = sorted(f["fonts"], key=lambda w: (w.endswith("i"), int(w.rstrip("i"))))
    print(f"{f['family']} · {f['category']} · weights {', '.join(weights)} · subsets "
          f"{', '.join(s for s in f['subsets'] if s != 'menu')} · by {', '.join(f.get('designers', []))}")


def cmd_get(args) -> None:
    f = family_info(args.family)
    weights = [w.strip() for w in args.weights.split(",") if w.strip()]
    have = set(f["fonts"])
    bad = [w for w in weights if w not in have]
    if bad:
        die(f"{f['family']} has no weight {', '.join(bad)} (it has {', '.join(sorted(have))})")
    italic = [w for w in weights if w.endswith("i")]
    roman = [w for w in weights if not w.endswith("i")]
    if italic:
        tuples = [f"0,{w}" for w in roman] + [f"1,{w.rstrip('i')}" for w in italic]
        axes = "ital,wght@" + ";".join(sorted(tuples))
    else:
        axes = "wght@" + ";".join(roman)
    css = fetch(CSS_URL.format(family=urllib.parse.quote_plus(f["family"]), axes=axes)).decode("utf-8")
    faces = re.findall(r"font-style:\s*(\w+);\s*font-weight:\s*(\d+);.*?src:\s*url\((\S+?)\)", css, re.S)
    if not faces:
        die("Google Fonts returned no files (no network?) — the video can't use system fonts instead: retry")
    DIR.mkdir(parents=True, exist_ok=True)
    index = [e for e in load_index() if e["family"] != f["family"]]
    slug = re.sub(r"[^A-Za-z0-9]+", "", f["family"])
    for style, weight, url in faces:
        dst = DIR / f"{slug}-{weight}{'i' if style == 'italic' else ''}.ttf"
        dst.write_bytes(fetch(url))
        miss = missing_chars(dst, args.lang)
        if miss:
            dst.unlink()
            die(f"{f['family']} {weight} lacks characters the video needs: {miss} — pick another family")
        fam, full = names(dst)
        index.append({"family": f["family"], "weight": int(weight), "italic": style == "italic", "file": str(dst),
                      "name": full, "source": f"https://fonts.google.com/specimen/{f['family'].replace(' ', '+')}",
                      "license": "Google Fonts (open license: OFL/Apache/UFL)"})
        print(f"{dst}  ·  {full}")
    INDEX.write_text(json.dumps(index, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{f['family']}: {len(faces)} file(s) → {DIR} (subtitles: --font \"<full name>\"; render.py finds them)")


def cmd_list(args) -> None:
    idx = load_index()
    if not idx:
        print("no project fonts yet: choose families for this video and run fonts.py get")
        return
    for e in idx:
        print(f"{e['family']:<28} {e['weight']}{'i' if e['italic'] else ' '}  {e['name']:<34} {e['file']}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("info")
    i.add_argument("family")
    g = sub.add_parser("get")
    g.add_argument("family")
    g.add_argument("--weights", default="400,700", help="e.g. 400,800 or 400,400i,700")
    g.add_argument("--lang", default="es", help="the video's language: its characters must exist")
    sub.add_parser("list")
    args = ap.parse_args()
    {"info": cmd_info, "get": cmd_get, "list": cmd_list}[args.cmd](args)


if __name__ == "__main__":
    main()
