# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["resvg-py>=0.1"]
# ///
"""Icons as real SVG: official brand marks, a quality icon set, and the ones drawn for the video.

    uv run icons.py brand whatsapp                    # official mark + its brand color
    uv run icons.py ui check [--set lucide|phosphor] [--weight regular|bold|fill|duotone|light|thin]
    uv run icons.py search whats [--set brand|lucide|phosphor]
    uv run icons.py png 02-work/icons/brand-whatsapp.svg --size 512 [--color "#ffffff"]
    uv run icons.py list

Brand marks (WhatsApp, Instagram, TikTok, YouTube…) come from Simple Icons — the
official shapes, CC0 — with each brand's own color; use them as they are, to point at
the platform (they're trademarks: don't redraw or distort them). Generic icons (check,
arrow, phone, clock…) come from Lucide (ISC) or Phosphor (MIT), consistent sets with
weights to match the video's design. Anything the video needs that no set has is drawn
as SVG by hand into 02-work/icons/custom-*.svg — in Remotion an SVG is written inline
exactly as in HTML (and its paths can be animated); `png` turns any of them into a PNG
(for covers and other raster uses) without system libraries.

Everything lands in 02-work/icons/, logged in 02-work/icons.json with source and
license for project.md. Never emoji or font symbols standing in for an icon.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import HOME, die  # noqa: E402

DIR = Path("02-work/icons")
INDEX = Path("02-work/icons.json")
CDN = "https://cdn.jsdelivr.net/npm/"
SETS = {
    "brand": {"pkg": "simple-icons@16", "older": ["simple-icons@13", "simple-icons@11"],
              "path": "icons/{name}.svg", "license": "CC0 (Simple Icons); the mark is the brand's trademark"},
    "lucide": {"pkg": "lucide-static@1", "path": "icons/{name}.svg", "license": "ISC (Lucide)"},
    "phosphor": {"pkg": "@phosphor-icons/core@2", "path": "assets/{weight}/{name}{suffix}.svg",
                 "license": "MIT (Phosphor Icons)"},
}


def fetch(url: str) -> bytes | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "vibetake"}),
                                    timeout=30) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def brand_data() -> list[dict]:
    cache = HOME / "cache" / "simple-icons.json"
    if not cache.exists():
        raw = fetch(CDN + SETS["brand"]["pkg"] + "/data/simple-icons.json")
        if raw is None:
            die("couldn't read Simple Icons' brand list")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(raw)
    return json.loads(cache.read_text(encoding="utf-8"))


def slug_of(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", title.lower().replace("+", "plus").replace(".", "dot").replace("&", "and"))


def log(entry: dict) -> None:
    idx = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else []
    idx = [e for e in idx if e["file"] != entry["file"]] + [entry]
    INDEX.parent.mkdir(parents=True, exist_ok=True)
    INDEX.write_text(json.dumps(idx, indent=1, ensure_ascii=False), encoding="utf-8")


def cmd_brand(args) -> None:
    data = brand_data()
    q = args.name.lower()
    hit = next((b for b in data if b.get("slug", slug_of(b["title"])) == q or b["title"].lower() == q), None)
    if not hit:
        close = [b["title"] for b in data if q in b["title"].lower()][:10]
        die(f"no brand '{args.name}' in Simple Icons" + (f" — maybe: {', '.join(close)}" if close else ""))
    slug = hit.get("slug") or slug_of(hit["title"])
    svg = None
    for pkg in [SETS["brand"]["pkg"]] + SETS["brand"]["older"]:
        svg = fetch(CDN + pkg + "/" + SETS["brand"]["path"].format(name=slug))
        if svg:
            break
    if not svg:
        die(f"{hit['title']}'s mark couldn't be downloaded")
    color = "#" + hit["hex"]
    text = svg.decode("utf-8").replace("<svg ", f'<svg fill="{color}" ', 1)
    DIR.mkdir(parents=True, exist_ok=True)
    dst = DIR / f"brand-{slug}.svg"
    dst.write_text(text, encoding="utf-8")
    log({"file": str(dst), "name": hit["title"], "set": "simple-icons", "color": color,
         "source": hit.get("source", ""), "license": SETS["brand"]["license"]})
    print(f"{dst}  ·  {hit['title']} · brand color {color}")


def cmd_ui(args) -> None:
    s = SETS[args.set]
    if args.set == "phosphor":
        weight = args.weight or "regular"
        path = s["path"].format(weight=weight, name=args.name, suffix="" if weight == "regular" else f"-{weight}")
    else:
        path = s["path"].format(name=args.name)
    svg = fetch(CDN + s["pkg"] + "/" + path)
    if not svg:
        die(f"no icon '{args.name}' in {args.set} — try: icons.py search {args.name} --set {args.set}")
    DIR.mkdir(parents=True, exist_ok=True)
    dst = DIR / f"ui-{args.name}{'-' + args.weight if args.weight and args.set == 'phosphor' else ''}.svg"
    dst.write_bytes(svg)
    log({"file": str(dst), "name": args.name, "set": args.set, "source": CDN + s["pkg"] + "/" + path,
         "license": s["license"]})
    print(f"{dst}  ·  {args.set} {args.name}")


def cmd_search(args) -> None:
    q = args.query.lower()
    if args.set == "brand":
        hits = [f"{b['title']} ({b.get('slug') or slug_of(b['title'])})" for b in brand_data()
                if q in b["title"].lower()]
    else:
        s = SETS[args.set]
        pkg = s["pkg"]
        meta = fetch(f"https://data.jsdelivr.com/v1/package/npm/{pkg.rsplit('@', 1)[0]}")
        version = json.loads(meta)["tags"]["latest"] if meta else pkg.rsplit("@", 1)[1]
        flat = fetch(f"https://data.jsdelivr.com/v1/package/npm/{pkg.rsplit('@', 1)[0]}@{version}/flat")
        files = [f["name"] for f in json.loads(flat)["files"]] if flat else []
        sub = "/icons/" if args.set == "lucide" else "/assets/regular/"
        hits = sorted({Path(f).stem for f in files if f.startswith(sub) and f.endswith(".svg") and q in Path(f).stem})
    print("\n".join(hits[:40]) if hits else "nothing found")


def cmd_png(args) -> None:
    import resvg_py
    src = Path(args.svg)
    text = src.read_text(encoding="utf-8")
    if args.color:
        text = text.replace("currentColor", args.color)
        if "fill=" not in text.split(">", 1)[0] and 'stroke="currentColor"' not in src.read_text(encoding="utf-8"):
            text = text.replace("<svg ", f'<svg fill="{args.color}" ', 1)
    # Size it: width/height on the root element, keeping the viewBox.
    def resize(m: re.Match) -> str:
        tag = re.sub(r'\s(width|height)="[^"]*"', "", m.group(0))
        return tag.replace("<svg", f'<svg width="{args.size}" height="{args.size}"', 1)
    text = re.sub(r"<svg\b[^>]*>", resize, text, count=1)
    out = Path(args.out) if args.out else src.with_suffix(".png")
    out.write_bytes(bytes(resvg_py.svg_to_bytes(svg_string=text)))
    print(f"{out}  ·  {args.size}px")


def cmd_list(args) -> None:
    idx = json.loads(INDEX.read_text(encoding="utf-8")) if INDEX.exists() else []
    for e in idx:
        print(f"{e['file']:<44} {e['set']:<12} {e['license']}")
    if not idx:
        print("no icons yet")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("brand")
    b.add_argument("name")
    u = sub.add_parser("ui")
    u.add_argument("name")
    u.add_argument("--set", choices=("lucide", "phosphor"), default="lucide")
    u.add_argument("--weight", choices=("regular", "bold", "fill", "duotone", "light", "thin"))
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--set", choices=("brand", "lucide", "phosphor"), default="brand")
    p = sub.add_parser("png")
    p.add_argument("svg")
    p.add_argument("--size", type=int, default=512)
    p.add_argument("--color", help="for icons drawn in currentColor (Lucide, Phosphor, custom)")
    p.add_argument("--out")
    sub.add_parser("list")
    args = ap.parse_args()
    {"brand": cmd_brand, "ui": cmd_ui, "search": cmd_search, "png": cmd_png, "list": cmd_list}[args.cmd](args)


if __name__ == "__main__":
    main()
