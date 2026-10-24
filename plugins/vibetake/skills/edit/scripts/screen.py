# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26", "opencv-python-headless>=4.9"]
# ///
"""Zooms into screen recordings, where the action is: a menu that opens, a field being filled, a dialog.

    uv run screen.py find [--files rec.mp4] [--max 2.5]     # proposals + sheets → 02-work/screen/
    uv run screen.py keep Z1,Z3,Z4                           # those go into the edit (edl.json screen_zooms)
    uv run screen.py set Z3 [--box x,y,w,h] [--from 12.1] [--to 15.0] [--max 2]
    uv run screen.py add --file rec.mp4 --from 40.2 --to 43.5 --box 0.62,0.08,0.3,0.2
    uv run screen.py drop Z4 | list | clear

find: for every screen recording in the edit (or --files), where the picture changes — not the
whole screen (a page change), not a speck (the cursor, a blinking caret) — and groups it into
moments, each with the box of what changed, held long enough to read, and what's being said
then. Sheets (02-work/screen/sheet_NN.jpg): the frame, the box (green) and what the zoom will show
(yellow). Look at every one and keep only those that help — a click the viewer must see, a value,
a small menu — not every hover. Fix a box that's off with `set`; a moment it missed: `add`.
Times are seconds of the source file (re-cuts keep the zooms); boxes are 0-1 of the picture.
render.py eases in, holds, eases out, fitting the box with a margin, up to --max (2.5×).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from _common import die, load_json, material_index, sdr_source, tc, tool  # noqa: E402

OUT = Path("02-work/screen")
FPS = 5               # analysis samples per second
W = 480               # analysis width
MIN_AREA = 0.002      # smaller changes are the cursor or a caret
MAX_AREA = 0.45       # bigger ones are a page/scene change: nothing to zoom into
HOLD = 1.5            # seconds held after the change, to read it
LEAD = 0.25           # the zoom starts this much before the change


def changes(m: dict, a: float, b: float) -> list[tuple[float, tuple | None, bool]]:
    """Per sample: (time, box of what changed or None, whole-screen change)."""
    src, filt = sdr_source(m)
    h = max(2, round(W * m["display_height"] / m["display_width"] / 2) * 2)
    vf = ",".join(x for x in (filt, f"fps={FPS}", f"scale={W}:{h}", "format=gray") if x)
    raw = subprocess.run([tool("ffmpeg"), "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", src, "-vf", vf,
                          "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    f = np.frombuffer(raw, np.uint8)[: len(raw) // (W * h) * W * h].reshape(-1, h, W)
    out, kernel = [], np.ones((9, 9), np.uint8)
    for i in range(1, len(f)):
        # Against a second ago, not the last frame: typing piles up into a word, while a moving
        # cursor stays two specks (where it was, where it is).
        raw = (cv2.absdiff(f[i], f[max(0, i - FPS)]) > 14).astype(np.uint8)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(cv2.dilate(raw, kernel), connectivity=8)
        counts = np.bincount(labels[raw > 0].ravel(), minlength=n)       # changed pixels, before growing
        keep = [stats[j] for j in range(1, n) if counts[j] >= MIN_AREA * W * h]
        t = a + i / FPS
        if not keep:
            out.append((t, None, False))
            continue
        x0, y0 = min(s[0] for s in keep), min(s[1] for s in keep)
        x1, y1 = max(s[0] + s[2] for s in keep), max(s[1] + s[3] for s in keep)
        area = (x1 - x0) * (y1 - y0) / (W * h)
        out.append((t, None if area > MAX_AREA else (x0 / W, y0 / h, (x1 - x0) / W, (y1 - y0) / h), area > MAX_AREA))
    return out


def union(p: tuple, q: tuple) -> tuple:
    x0, y0 = min(p[0], q[0]), min(p[1], q[1])
    x1, y1 = max(p[0] + p[2], q[0] + q[2]), max(p[1] + p[3], q[1] + q[3])
    return (x0, y0, x1 - x0, y1 - y0)


def iou(p: tuple, q: tuple) -> float:
    ix = max(0.0, min(p[0] + p[2], q[0] + q[2]) - max(p[0], q[0]))
    iy = max(0.0, min(p[1] + p[3], q[1] + q[3]) - max(p[1], q[1]))
    inter = ix * iy
    return inter / (p[2] * p[3] + q[2] * q[3] - inter or 1)


def moments(samples: list, a: float, b: float) -> list[dict]:
    """Changes close in time and place grouped; each held HOLD s; a page change ends one."""
    ev, cur = [], None
    for t, box, big in samples:
        if big or (box is None and cur and t - cur["last"] > 1.0):
            if cur:
                ev.append(cur)
            cur = None
            continue
        if box is None:
            continue
        if cur and t - cur["last"] <= 1.0:
            u = union(cur["box"], box)
            if u[2] * u[3] <= MAX_AREA:
                cur.update(box=u, last=t)
                continue
        if cur:
            ev.append(cur)
        cur = {"start": t, "last": t, "box": box}
    if cur:
        ev.append(cur)
    # The same place changing again soon (a menu opens, an item is picked, it closes): one zoom.
    merged = []
    for e in ev:
        p = merged[-1] if merged else None
        if p and e["start"] - p["last"] <= 3.0 and iou(p["box"], e["box"]) > 0.5:
            p.update(box=union(p["box"], e["box"]), last=e["last"])
        else:
            merged.append(dict(e))
    out = []
    for e in merged:
        f, to = max(a, e["start"] - LEAD), min(b, e["last"] + HOLD)
        if to - f < 1.0:
            continue
        out.append({"from": round(f, 2), "to": round(to, 2), "box": [round(v, 4) for v in e["box"]],
                    "peak": round((e["start"] + e["last"]) / 2, 2)})
    return out


def spoken(tr: dict | None, name: str, a: float, b: float) -> str:
    if not tr or name not in tr.get("files", {}):
        return ""
    return "".join(w["word"] for s in tr["files"][name]["segments"] for w in s["words"]
                   if a - 1.0 <= w["start"] <= b + 0.5).strip()


def view(m: dict, z: dict, out: dict) -> tuple:
    """What the zoom will show (0-1 of the picture): the output's aspect, fitted like render.py."""
    sw, sh = m["display_width"], m["display_height"]
    asp = out["width"] / out["height"]
    cw, ch = min(1.0, sh * asp / sw), min(1.0, sw / asp / sh)
    x, y, w, h = z["box"]
    need = max(w * 1.4 / cw, h * 1.4 / ch)
    zb = min(max(1.0 / need, 1.0), float(z.get("max", 2.5)))
    vw, vh = cw / zb, ch / zb
    vx = min(max(x + w / 2 - vw / 2, 0), 1 - vw)
    vy = min(max(y + h / 2 - vh / 2, 0), 1 - vh)
    return (vx, vy, vw, vh, zb)


def sheets(props: list[dict], material: dict, out: dict) -> list[Path]:
    paths, cells = [], []
    for z in props:
        m = material[z["name"]]
        src, filt = sdr_source(m)
        vf = ",".join(x for x in (filt, "scale=640:-2") if x)
        png = subprocess.run([tool("ffmpeg"), "-v", "error", "-ss", f"{z['peak'] + 0.4:.2f}", "-i", src, "-frames:v", "1",
                              "-vf", vf, "-f", "image2pipe", "-c:v", "png", "-"], capture_output=True).stdout
        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR) if png else None
        if img is None:
            continue
        H, Wd = img.shape[:2]
        x, y, w, h = z["box"]
        cv2.rectangle(img, (int(x * Wd), int(y * H)), (int((x + w) * Wd), int((y + h) * H)), (0, 220, 0), 2)
        vx, vy, vw, vh, zb = view(m, z, out)
        cv2.rectangle(img, (int(vx * Wd), int(vy * H)), (int((vx + vw) * Wd), int((vy + vh) * H)), (0, 220, 255), 2)
        bar = np.full((26, Wd, 3), 32, np.uint8)
        cv2.putText(bar, f"{z['id']}  {tc(z['from'])}-{tc(z['to'])}  x{zb:.1f}", (6, 18), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, (255, 255, 255), 1)
        cells.append(np.vstack([bar, img]))
    for n, i in enumerate(range(0, len(cells), 4)):
        group = cells[i:i + 4]
        hh = max(c.shape[0] for c in group)
        group = [np.vstack([c, np.zeros((hh - c.shape[0], c.shape[1], 3), np.uint8)]) for c in group]
        while len(group) < 4:
            group.append(np.zeros_like(group[0]))
        sheet = np.vstack([np.hstack(group[:2]), np.hstack(group[2:])])
        p = OUT / f"sheet_{n + 1:02d}.jpg"
        p.write_bytes(cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes())
        paths.append(p)
    return paths


def cmd_find(args) -> None:
    edl = load_json(Path(args.edl))
    material = material_index(args.ingest)
    tp = Path(args.ingest) / "transcript.json"
    tr = load_json(tp) if tp.exists() else None
    want = set(args.files.split(",")) if args.files else None
    if want is None:
        # Screen recordings: files in the edit with (almost) no face.
        want = {s["name"] for s in edl["segments"]
                if (material.get(s["name"], {}).get("faces") or {}).get("pct_with_face", 0) < 20}
    spans = [(s["name"], s["in"], s["out"]) for s in edl["segments"] if s["name"] in want]
    if not spans:
        die("no screen recordings in the edit (pass --files)")
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("sheet_*.jpg"):
        old.unlink()
    props = []
    for name, a, b in spans:
        m = material.get(name) or die(f"{name} isn't in material.json")
        for z in moments(changes(m, a, b), a, b):
            props.append({"id": f"Z{len(props) + 1}", "name": name, **z, "max": args.max,
                          "said": spoken(tr, name, z["from"], z["to"])})
    (OUT / "proposals.json").write_text(json.dumps({"zooms": props}, indent=1, ensure_ascii=False), encoding="utf-8")
    paths = sheets(props, material, edl["output"])
    lines = ["# Screen zoom proposals", "", "Green: what changed. Yellow: what the zoom shows. Keep only what "
             "helps: `screen.py keep Z1,Z3`.", ""]
    lines += [f"- **{z['id']}** {z['name']} {tc(z['from'])}–{tc(z['to'])}" + (f" · “{z['said'][:80]}”" if z["said"] else "")
              for z in props]
    (OUT / "proposals.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(props)} proposals → {OUT / 'proposals.md'}")
    for p in paths:
        print(f"    {p}")


def edit(args, fn) -> None:
    edl_p = Path(args.edl)
    edl = load_json(edl_p)
    zs = edl.setdefault("screen_zooms", [])
    fn(edl, zs)
    zs.sort(key=lambda z: (z["name"], z["from"]))
    if not zs:
        edl.pop("screen_zooms")
    edl_p.write_text(json.dumps(edl, indent=1, ensure_ascii=False), encoding="utf-8")


def parse_box(s: str) -> list[float]:
    try:
        x, y, w, h = (float(v) for v in s.split(","))
    except ValueError:
        die("--box is four numbers 0-1: x,y,w,h")
    if not (0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0):
        die("--box: x,y inside the picture (0-1), w and h above 0")
    return [x, y, min(w, 1 - x), min(h, 1 - y)]


def cmd_keep(args) -> None:
    pp = OUT / "proposals.json"
    if not pp.exists():
        die("no proposals: screen.py find first")
    props = {z["id"]: z for z in load_json(pp)["zooms"]}
    ids = [x.strip().upper() for x in args.ids.split(",") if x.strip()]
    missing = [i for i in ids if i not in props]
    if missing:
        die(f"not among the proposals: {missing}")

    def fn(edl, zs):
        have = {z["id"] for z in zs}
        for i in ids:
            if i not in have:
                zs.append({k: v for k, v in props[i].items() if k not in ("peak", "said")})
    edit(args, fn)
    print(f"{len(ids)} zoom(s) in the edit · render.py --preview to see them")


def cmd_set(args) -> None:
    def fn(edl, zs):
        z = next((z for z in zs if z["id"] == args.id.upper()), None) or die(f"{args.id} isn't in the edit (keep it first)")
        if args.box:
            z["box"] = parse_box(args.box)
        for k in ("from_", "to", "max"):
            if getattr(args, k) is not None:
                z[k.rstrip("_")] = getattr(args, k)
        if z["to"] - z["from"] < 0.5:
            die("a zoom needs at least 0.5 s between --from and --to")
    edit(args, fn)
    print(f"{args.id.upper()} updated")


def cmd_add(args) -> None:
    def fn(edl, zs):
        if args.to - args.from_ < 0.5:
            die("a zoom needs at least 0.5 s between --from and --to")
        n = 1 + max([int(z["id"][1:]) for z in zs if z["id"][1:].isdigit()] + [0] + [
            int(z["id"][1:]) for z in (load_json(OUT / "proposals.json")["zooms"] if (OUT / "proposals.json").exists() else [])])
        zs.append({"id": f"Z{n}", "name": args.file, "from": args.from_, "to": args.to, "box": parse_box(args.box),
                   "max": args.max})
        print(f"Z{n} added")
    edit(args, fn)


def cmd_drop(args) -> None:
    def fn(edl, zs):
        zs[:] = [z for z in zs if z["id"] != args.id.upper()]
    edit(args, fn)
    print(f"{args.id.upper()} out")


def cmd_list(args) -> None:
    for z in load_json(Path(args.edl)).get("screen_zooms", []):
        print(f"{z['id']}  {z['name']}  {tc(z['from'])}–{tc(z['to'])}  box {z['box']}  max ×{z.get('max', 2.5)}")


def cmd_clear(args) -> None:
    edit(args, lambda edl, zs: zs.clear())
    print("no screen zooms")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ingest", default="01-ingest")
    ap.add_argument("--edl", default="02-work/edl.json")
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("find")
    f.add_argument("--files")
    f.add_argument("--max", type=float, default=2.5, help="closest zoom")
    sub.add_parser("keep").add_argument("ids")
    s = sub.add_parser("set")
    s.add_argument("id")
    s.add_argument("--box")
    s.add_argument("--from", dest="from_", type=float)
    s.add_argument("--to", type=float)
    s.add_argument("--max", type=float)
    a = sub.add_parser("add")
    a.add_argument("--file", required=True)
    a.add_argument("--from", dest="from_", type=float, required=True)
    a.add_argument("--to", type=float, required=True)
    a.add_argument("--box", required=True)
    a.add_argument("--max", type=float, default=2.5)
    sub.add_parser("drop").add_argument("id")
    sub.add_parser("list")
    sub.add_parser("clear")
    args = ap.parse_args()
    {"find": cmd_find, "keep": cmd_keep, "set": cmd_set, "add": cmd_add, "drop": cmd_drop, "list": cmd_list,
     "clear": cmd_clear}[args.cmd](args)


if __name__ == "__main__":
    main()
