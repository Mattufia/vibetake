# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26", "pillow>=10"]
# ///
"""Designed covers and thumbnails, one per platform, from a frame of the material.

    uv run cover.py candidates [--n 12]                       # frames worth a cover → contact sheet
    uv run cover.py make --video <file> --time 12.4 --title "Cómo editar con IA" --keyword IA \\
        --platform youtube --platform reels [--big "3×"] [--brand "Name"] [--logo logo.png] \\
        [--colors "#101828,#FFD400,#FFFFFF"] [--font <ttf>] [--variant 1]

`candidates` ranks moments with a big, steady face and writes a numbered contact
sheet (02-work/covers/candidates.jpg) to pick from: look for the gesture that
matches the headline, eyes open, mouth not mid-syllable.

`make` composes, per platform, the person cut out of the frame (rembg,
birefnet-portrait; --cutout to pass one, --no-cutout for the frame as background),
large in the lower part and leaving the canvas through its bottom edge, with an
outline and a soft shadow; a headline in capitals with the keyword boxed and larger;
the brand above it; a background in the brand color with a glow behind the person,
darker edges and a faint dot pattern; and an optional giant, half-transparent
element (a number, a word) between the headline and the head. The face is never
under text. Sizes and the area that must survive each platform's crop:

    youtube  1280×720   keep clear of the bottom-right corner (duration badge)
    reels    1080×1920  headline and face inside the central 3:4 (profile grid)
    tiktok   1080×1920  same rule (the profile grid crops the center)
    shorts   1080×1920  same rule

Files go to 03-delivery/cover_<platform>_<variant>.jpg and are overwritten on each
run; 02-work/covers/ keeps a copy with the crop guides drawn, to check.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, str(Path(__file__).parent))
from _common import die, filter_path, run, tc, tool  # noqa: E402

PLATFORMS = {
    # size, core box (x0, y0, x1, y1 as 0-1: what must survive the platform's crop), avoid boxes
    "youtube": ((1280, 720), (0.04, 0.06, 0.96, 0.94), [(0.80, 0.82, 1.0, 1.0)]),
    "reels": ((1080, 1920), (0.06, 0.125, 0.94, 0.875), []),
    "tiktok": ((1080, 1920), (0.06, 0.125, 0.94, 0.875), []),
    "shorts": ((1080, 1920), (0.06, 0.125, 0.94, 0.875), []),
}
def find_font(name: str | None) -> str:
    """The headline font: a .ttf path given, else the project's display font (fonts.py /
    design.json). System fonts are never a fallback."""
    if name and Path(name).exists():
        return name
    if name:
        die(f"font {name} not found: pass a .ttf path (fonts.py get downloads the project's fonts)")
    from _common import project_font
    pf = project_font("display")
    if not pf:
        die("no project font: choose this video's typefaces first (fonts.py get …)")
    return pf["file"]


def hexcolor(c: str) -> tuple[int, int, int]:
    c = c.strip().lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


# ---------- frame and cutout ----------

def grab(video: Path, t: float, out: Path) -> Path:
    """Full-resolution frame at t, with the file's color grade if there is one."""
    vf = []
    gp = Path("02-work/color/grade.json")
    if gp.exists():
        g = json.loads(gp.read_text(encoding="utf-8")).get(video.name) or {}
        vf += [x for x in (g.get("pre"), f"lut3d=file='{filter_path(g['lut'])}'" if g.get("lut") else None) if x]
    run([tool("ffmpeg"), "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1",
         *(["-vf", ",".join(vf)] if vf else []), str(out)])
    return out


def cut_out(frame: Path, out: Path) -> Path | None:
    """Person without background (rembg birefnet-portrait). None if it can't run here."""
    uvx = shutil.which("uvx")
    if not uvx:
        return None
    print("cutting out the person (rembg birefnet-portrait; the first run downloads the model, ~1 GB)…",
          flush=True)
    p = subprocess.run([uvx, "--from", "rembg[cpu,cli]", "rembg", "i", "-m", "birefnet-portrait", str(frame),
                        str(out)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode != 0 or not out.exists():
        print(f"WARNING: rembg failed ({(p.stderr or p.stdout).strip().splitlines()[-1:]}) — "
              "cover without cutout (or pass --cutout)")
        return None
    return out


# ---------- drawing helpers ----------

def background(size: tuple[int, int], color: tuple, glow_at: tuple[float, float], pattern: bool) -> Image.Image:
    W, H = size
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    base = np.array(color, np.float32)
    lum = 0.2126 * base[0] + 0.7152 * base[1] + 0.0722 * base[2]
    glow_col = base + (255 - base) * (0.35 if lum < 128 else 0.15)
    d = np.hypot((x - glow_at[0] * W) / W, (y - glow_at[1] * H) / W)
    glow = np.exp(-(d / 0.38) ** 2)[..., None]
    edge = np.hypot((x / W - 0.5) * 1.4, (y / H - 0.5) * 1.4)
    vignette = (1 - 0.45 * np.clip(edge - 0.3, 0, 1) ** 1.5)[..., None]
    img = (base * (1 - glow) + glow_col * glow) * vignette
    if pattern:
        step = max(14, W // 60)
        dots = ((x % step < 2.2) & (y % step < 2.2)).astype(np.float32)[..., None]
        img = img * (1 - 0.05 * dots) + 255 * 0.05 * dots
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8), "RGB")


def text_size(font: ImageFont.FreeTypeFont, s: str) -> tuple[int, int]:
    b = font.getbbox(s)
    return b[2] - b[0], b[3] - b[1]


def layout_headline(words: list[str], keyword: set[str], font_path: str, box_w: int, max_h: int,
                    max_lines: int = 3) -> tuple[int, list[list[str]]]:
    """Largest font size at which the words fit in ≤ max_lines lines of box_w and max_h."""
    for size in range(int(max_h / 1.6), 12, -2):
        f, fk = ImageFont.truetype(font_path, size), ImageFont.truetype(font_path, int(size * 1.18))
        space = text_size(f, "M")[0] * 0.3
        lines, cur, cur_w = [], [], 0.0
        for w in words:
            ww = text_size(fk if w.strip(".,!?¡¿:;").upper() in keyword else f, w)[0] + \
                (size * 0.36 if w.strip(".,!?¡¿:;").upper() in keyword else 0)
            if cur and cur_w + space + ww > box_w:
                lines.append(cur)
                cur, cur_w = [], 0.0
            cur.append(w)
            cur_w += (space if len(cur) > 1 else 0) + ww
        lines.append(cur)
        if any(text_size(fk, w)[0] > box_w for w in words):
            continue
        height = sum(size * (1.3 if any(x.strip(".,!?¡¿:;").upper() in keyword for x in ln) else 1.08)
                     for ln in lines)
        if len(lines) <= max_lines and height <= max_h:
            return size, lines
    return 14, [words]


def draw_headline(canvas: Image.Image, lines: list[list[str]], keyword: set[str], font_path: str, size: int,
                  x0: int, x1: int, y: int, align: str, text_col: tuple, accent: tuple) -> int:
    """Draws the headline with a soft shadow; returns its bottom y."""
    f, fk = ImageFont.truetype(font_path, size), ImageFont.truetype(font_path, int(size * 1.18))
    space = text_size(f, "M")[0] * 0.3
    shadow = Image.new("L", canvas.size, 0)
    sd = ImageDraw.Draw(shadow)
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    lum = 0.2126 * accent[0] + 0.7152 * accent[1] + 0.0722 * accent[2]
    on_accent = (15, 15, 15) if lum > 140 else (255, 255, 255)
    for ln in lines:
        is_k = [w.strip(".,!?¡¿:;").upper() in keyword for w in ln]
        widths = [text_size(fk if k else f, w)[0] + (size * 0.36 if k else 0) for w, k in zip(ln, is_k)]
        total = sum(widths) + space * (len(ln) - 1)
        x = x0 + ((x1 - x0) - total) / 2 if align == "center" else x0
        lh = size * (1.3 if any(is_k) else 1.08)
        for w, k, ww in zip(ln, is_k, widths):
            font = fk if k else f
            top = y + (lh - (size * 1.18 if k else size)) / 2
            if k:
                pad = size * 0.18
                bb = font.getbbox(w)
                rect = (x, top + bb[1] - pad * 0.7, x + ww, top + bb[3] + pad * 0.7)
                d.rounded_rectangle(rect, radius=size * 0.12, fill=accent + (255,))
                sd.rounded_rectangle(rect, radius=size * 0.12, fill=200)
                d.text((x + pad, top), w, font=font, fill=on_accent + (255,))
            else:
                sd.text((x + size * 0.04, top + size * 0.06), w, font=font, fill=220)
                d.text((x, top), w, font=font, fill=text_col + (255,))
            x += ww + space
        y += lh
    blur = shadow.filter(ImageFilter.GaussianBlur(size * 0.08))
    canvas.paste(Image.new("RGB", canvas.size, (0, 0, 0)), (0, 0), blur.point(lambda v: int(v * 0.55)))
    canvas.paste(layer, (0, 0), layer)
    return int(y)


def head_width(m: np.ndarray) -> tuple[int, float]:
    """Width and center (px) of the head in a figure mask cropped to its box: scanning down
    from the crown, the widest row before the neck (where the outline narrows by 15% or
    more, before the shoulders). Without a visible neck, the widest row of the top 30%."""
    rows = []
    for r in m:
        c = np.nonzero(r)[0]
        rows.append((c.min(), c.max()) if len(c) else None)
    best, best_row = 0, None
    for i, rc in enumerate(rows):
        if rc is None:
            continue
        w = rc[1] - rc[0] + 1
        if w > best:
            best, best_row = w, rc
        elif best and w < 0.85 * best and i > 0.08 * len(rows):     # stray hairs at the crown don't count
            return best, (best_row[0] + best_row[1]) / 2          # the neck: the head ends above it
    top = [rc for rc in rows[: max(1, int(0.3 * len(rows)))] if rc]
    if not top:
        return m.shape[1], m.shape[1] / 2
    rc = max(top, key=lambda x: x[1] - x[0])
    return rc[1] - rc[0] + 1, (rc[0] + rc[1]) / 2


def place_person(canvas: Image.Image, person: Image.Image, top_min: int, cx: float, head_w: float,
                 outline: tuple) -> tuple[int, int, int, int]:
    """Cutout scaled by its head (head_w px wide), the head centered on cx and its top not
    above top_min; the body bleeds out of the bottom (and the sides, if it's a close-up).
    White outline and soft shadow. Returns its box."""
    W, H = canvas.size
    a = np.array(person.split()[-1]) > 128
    ys, xs = np.nonzero(a)
    if not len(ys):
        die("the cutout is empty")
    crop = person.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    m = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    head_px, head_cx = head_width(m)
    scale = head_w / head_px
    # A close-up is short: grow it (up to 1.4× the head size) so it fills from top_min down
    # and still leaves through the bottom edge, instead of floating low with a gap above.
    fill = (H * 1.04 - top_min) / crop.height
    scale = max(scale, min(fill, scale * 1.4))
    p = crop.resize((max(1, int(crop.width * scale)), max(1, int(crop.height * scale))), Image.LANCZOS)
    top = max(top_min, H - p.height + int(0.04 * H))           # at least a little bleed past the bottom
    left = int(cx * W - head_cx * scale)
    alpha = p.split()[-1]
    ring = max(3, W // 220)
    grown = alpha.filter(ImageFilter.MaxFilter(ring * 2 + 1))
    shadow = grown.filter(ImageFilter.GaussianBlur(W // 60)).point(lambda v: int(v * 0.5))
    canvas.paste(Image.new("RGB", p.size, (0, 0, 0)), (left + W // 160, top + W // 120), shadow)
    canvas.paste(Image.new("RGB", p.size, outline), (left, top), grown)
    canvas.paste(p.convert("RGB"), (left, top), alpha)
    return left, top, left + p.width, top + p.height


def giant(canvas: Image.Image, text: str, font_path: str, center: tuple[int, int], height: int,
          color: tuple, max_w: float) -> None:
    f = ImageFont.truetype(font_path, max(20, int(height)))
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    w, h = text_size(f, text)
    if w > max_w:
        f = ImageFont.truetype(font_path, max(20, int(height * max_w / w)))
        w, h = text_size(f, text)
    bb = f.getbbox(text)
    d.text((center[0] - w / 2 - bb[0], center[1] - h / 2 - bb[1]), text, font=f, fill=color + (70,))
    canvas.paste(layer, (0, 0), layer)


def brand_row(canvas: Image.Image, name: str | None, logo: Path | None, font_path: str, at: tuple[int, int],
              h: int, align: str, color: tuple) -> int:
    """Logo and/or name, small; returns the row's bottom y."""
    if not (name or logo):
        return at[1]
    parts, total = [], 0
    if logo:
        lg = Image.open(logo).convert("RGBA")
        lg = lg.resize((max(1, int(lg.width * h / lg.height)), h), Image.LANCZOS)
        parts.append(("img", lg, lg.width))
        total += lg.width
    if name:
        f = ImageFont.truetype(font_path, int(h * 0.7))
        w = text_size(f, name)[0]
        parts.append(("txt", (name, f), w))
        total += w + (int(h * 0.3) if logo else 0)
    x = at[0] - total // 2 if align == "center" else at[0]
    d = ImageDraw.Draw(canvas)
    for kind, obj, w in parts:
        if kind == "img":
            canvas.paste(obj, (x, at[1]), obj)
            x += w + int(h * 0.3)
        else:
            s, f = obj
            bb = f.getbbox(s)
            d.text((x, at[1] + (h - (bb[3] - bb[1])) / 2 - bb[1]), s, font=f, fill=color)
            x += w
    return at[1] + h


# ---------- compose ----------

def compose(platform: str, frame: Image.Image, person: Image.Image | None, args, font_path: str) -> tuple:
    (W, H), core, avoid = PLATFORMS[platform]
    bg_col, accent, text_col = (hexcolor(c) for c in args.colors.split(","))
    vertical = H > W
    cx0, cy0, cx1, cy1 = (int(core[0] * W), int(core[1] * H), int(core[2] * W), int(core[3] * H))
    keyword = {k.upper() for k in (args.keyword or "").split(",") if k}
    words = args.title.upper().split()
    glow = (0.5, 0.62) if vertical else (0.72, 0.55)
    canvas = background((W, H), bg_col, glow, not args.no_pattern)

    if vertical:
        brand_h = int(0.03 * H) if (args.brand or args.logo) else 0
        y = brand_row(canvas, args.brand, Path(args.logo) if args.logo else None, font_path,
                      (W // 2, cy0), brand_h, "center", text_col)
        y += int(0.015 * H) if brand_h else 0
        size, lines = layout_headline(words, keyword, font_path, cx1 - cx0, int(0.25 * H))
        text_top, x0, x1, align = y, cx0, cx1, "center"
    else:
        brand_h = int(0.06 * H) if (args.brand or args.logo) else 0
        y = brand_row(canvas, args.brand, Path(args.logo) if args.logo else None, font_path,
                      (cx0, cy0), brand_h, "left", text_col)
        y += int(0.03 * H) if brand_h else 0
        x1 = int(0.56 * W) if person is not None else int(0.47 * W)
        size, lines = layout_headline(words, keyword, font_path, x1 - cx0, cy1 - y - int(0.05 * H))
        text_top, x0, align = y, cx0, "left"
    # Measure the headline's height before drawing anything that goes under it.
    est_h = int(sum(size * (1.3 if any(w.strip(".,!?¡¿:;").upper() in keyword for w in ln) else 1.08)
                    for ln in lines))
    text_bottom = text_top + est_h

    if args.big and person is not None:
        if vertical:
            giant(canvas, args.big, font_path, (W // 2, text_bottom + int(0.11 * H)), int(0.2 * H), accent,
                  0.9 * W)
        else:
            room = cy1 - text_bottom - int(0.04 * H)
            if room > 0.18 * H:          # the free space under the headline, reaching toward the person
                giant(canvas, args.big, font_path, ((cx0 + int(0.66 * W)) // 2, text_bottom + int(0.02 * H) + room // 2),
                      int(room * 1.1), accent, 0.62 * W)
            else:                        # no room: behind the head, half hidden
                giant(canvas, args.big, font_path, (int(0.76 * W), int(0.30 * H)), int(0.45 * H), accent,
                      0.44 * W)

    pbox = None
    if person is None:
        # No cutout: the frame fills the area the text doesn't use (below it in vertical, to
        # its right in horizontal), anchored at its top so the head stays, fading into the
        # background where they meet — the text never lands on the picture.
        if vertical:
            area = (0, text_bottom + int(0.02 * H), W, H)
            fade_axis, fade_len = "top", int(0.08 * H)
        else:
            area = (int(0.50 * W), 0, W, H)
            fade_axis, fade_len = "left", int(0.12 * W)
        aw, ah = area[2] - area[0], area[3] - area[1]
        s = max(aw / frame.width, ah / frame.height)
        ph = frame.resize((int(frame.width * s) + 1, int(frame.height * s) + 1), Image.LANCZOS)
        lx = (ph.width - aw) // 2
        ph = ph.crop((lx, 0, lx + aw, ah))
        ramp = np.clip(np.arange(ah if fade_axis == "top" else aw) / max(1, fade_len), 0, 1) ** 1.5
        mask = (np.broadcast_to(ramp[:, None], (ah, aw)) if fade_axis == "top"
                else np.broadcast_to(ramp[None, :], (ah, aw)))
        canvas.paste(ph, area[:2], Image.fromarray((mask * 255).astype(np.uint8), "L"))
        pbox = area
    else:
        if vertical:
            pbox = place_person(canvas, person, text_bottom + int(0.05 * H), 0.5, 0.42 * W, (255, 255, 255))
        else:
            pbox = place_person(canvas, person, int(0.08 * H), 0.76, 0.24 * W, (255, 255, 255))
    bottom = draw_headline(canvas, lines, keyword, font_path, size, x0, x1, text_top, align, text_col, accent)

    problems = []
    if bottom > cy1:
        problems.append("headline runs past the area that survives the crop: shorten it")
    for ax0, ay0, ax1, ay1 in avoid:
        if x1 > ax0 * W and bottom > ay0 * H:
            problems.append("headline reaches the duration badge corner")
    if pbox and vertical and pbox[1] > cy1 - int(0.12 * H):
        problems.append("the person's head falls below the grid crop: use a shorter headline")
    return canvas, (cx0, cy0, cx1, cy1), avoid, problems


def guides(img: Image.Image, core: tuple, avoid: list) -> Image.Image:
    g = img.copy()
    d = ImageDraw.Draw(g)
    d.rectangle(core, outline=(0, 255, 0), width=max(2, img.width // 400))
    for ax0, ay0, ax1, ay1 in avoid:
        d.rectangle((ax0 * img.width, ay0 * img.height, ax1 * img.width - 1, ay1 * img.height - 1),
                    outline=(255, 0, 0), width=max(2, img.width // 400))
    return g


# ---------- commands ----------

def cmd_candidates(args) -> None:
    ing = Path(args.ingest)
    faces = json.loads((ing / "faces.json").read_text(encoding="utf-8"))["files"] if (ing / "faces.json").exists() \
        else {}
    material = {m["name"]: m for m in json.loads((ing / "material.json").read_text(encoding="utf-8"))["files"]}
    scored = []
    for name, samples in faces.items():
        for i, s in enumerate(samples):
            if not s["faces"]:
                continue
            x, y, w, h = max(s["faces"], key=lambda f: f[3])[:4]
            # Steady: the face barely moved since the previous sample (a sharp frame, not mid-gesture blur).
            prev = samples[i - 1]["faces"] if i else []
            move = min((abs(p[0] - x) + abs(p[1] - y) for p in prev), default=1.0)
            centered = 1 - abs(x + w / 2 - 0.5)
            scored.append((h * centered - move, name, s["t"]))
    if not scored:
        die("no faces in faces.json: pick the frame from the contact sheets in 01-ingest/contact/")
    scored.sort(reverse=True)
    picks = []
    for sc, name, t in scored:
        if all(n != name or abs(t - pt) >= args.spacing for _, n, pt in picks):
            picks.append((sc, name, t))
        if len(picks) == args.n:
            break
    out = Path("02-work/covers")
    out.mkdir(parents=True, exist_ok=True)
    thumbs = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, (_, name, t) in enumerate(picks, 1):
            f = grab(Path(material[name]["file"]), t, Path(tmp) / f"{i}.png")
            im = Image.open(f).convert("RGB")
            im.thumbnail((360, 360))
            d = ImageDraw.Draw(im)
            d.rectangle((0, 0, 150, 28), fill=(0, 0, 0))
            d.text((6, 6), f"{i}  {tc(t)}", fill=(255, 255, 0))
            thumbs.append(im)
    cols = 4
    cw, ch = max(t.width for t in thumbs), max(t.height for t in thumbs)
    sheet = Image.new("RGB", (cols * cw, ((len(thumbs) + cols - 1) // cols) * ch), (20, 20, 20))
    for i, t in enumerate(thumbs):
        sheet.paste(t, ((i % cols) * cw, (i // cols) * ch))
    sheet.save(out / "candidates.jpg", quality=90)
    for i, (_, name, t) in enumerate(picks, 1):
        print(f"{i:2d}. {name} @ {t:.2f} s")
    print(f"contact sheet → {out / 'candidates.jpg'}")


def cmd_make(args) -> None:
    font_path = find_font(args.font)
    if not args.colors:
        dj = Path("02-work/design.json")
        c = json.loads(dj.read_text(encoding="utf-8")).get("colors", {}) if dj.exists() else {}
        if not all(k in c for k in ("paper", "accent", "ink")):
            die("no colors: pass --colors bg,accent,text or write the video's design (design.json)")
        args.colors = f"{c['paper']},{c['accent']},{c['ink']}"
    work = Path("02-work/covers")
    work.mkdir(parents=True, exist_ok=True)
    if args.image:
        frame_path = Path(args.image)
    else:
        if not (args.video and args.time is not None):
            die("make needs --image, or --video and --time")
        frame_path = grab(Path(args.video), args.time, work / f"frame_{args.variant}.png")
    frame = Image.open(frame_path).convert("RGB")
    person = None
    if args.cutout:
        person = Image.open(args.cutout).convert("RGBA")
    elif not args.no_cutout:
        c = cut_out(frame_path, work / f"cutout_{args.variant}.png")
        person = Image.open(c).convert("RGBA") if c else None
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for p in args.platform or ["youtube"]:
        img, core, avoid, problems = compose(p, frame, person, args, font_path)
        dst = out_dir / f"cover_{p}_{args.variant}.jpg"
        q = 92
        img.save(dst, quality=q, optimize=True)
        while p == "youtube" and dst.stat().st_size > 2_000_000 and q > 60:     # YouTube's 2 MB limit
            q -= 8
            img.save(dst, quality=q, optimize=True)
        guides(img, core, avoid).save(work / f"cover_{p}_{args.variant}.guides.jpg", quality=85)
        print(f"{dst} ({img.width}×{img.height}, {dst.stat().st_size / 1e3:.0f} kB)"
              f"{'' if person is not None else ' — no cutout: frame as background'}")
        for pr in problems:
            print("WARNING:", pr)
    print(f"check them with the crop guides: {work}/cover_*_{args.variant}.guides.jpg")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ingest", default="01-ingest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("candidates")
    c.add_argument("--n", type=int, default=12)
    c.add_argument("--spacing", type=float, default=1.5, help="seconds between picks from the same file")
    m = sub.add_parser("make")
    m.add_argument("--video")
    m.add_argument("--time", type=float)
    m.add_argument("--image", help="a still to use instead of --video/--time")
    m.add_argument("--cutout", help="the person already cut out (PNG with alpha)")
    m.add_argument("--no-cutout", action="store_true", help="use the frame as background")
    m.add_argument("--title", required=True, help="the headline (written in the video's language)")
    m.add_argument("--keyword", help="word(s) of the headline to box and enlarge, comma-separated")
    m.add_argument("--big", help="giant half-transparent element behind the head: a number, a word, an emoji")
    m.add_argument("--brand", help="brand name above the headline")
    m.add_argument("--logo", help="brand logo (PNG with alpha)")
    m.add_argument("--colors", help="background,accent,text (default: the video's design: paper, accent, ink)")
    m.add_argument("--font", help="headline font (.ttf path); default: the project's display font")
    m.add_argument("--no-pattern", action="store_true")
    m.add_argument("--platform", action="append", choices=tuple(PLATFORMS))
    m.add_argument("--variant", type=int, default=1)
    m.add_argument("--out-dir", default="03-delivery")
    args = ap.parse_args()
    {"candidates": cmd_candidates, "make": cmd_make}[args.cmd](args)


if __name__ == "__main__":
    main()
