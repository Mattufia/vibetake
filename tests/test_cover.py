# /// script
# requires-python = ">=3.10,<3.14"
# dependencies = ["numpy>=1.26", "pillow>=10"]
# ///
"""Cover tests (cover.py) on a synthetic cut-out person.

    uv run tests/test_cover.py

1. Head size is measured above the neck, not across the shoulders.
2. Each platform gets its size; in vertical, the headline sits above the head and the
   head inside the central 3:4 (profile grid); in YouTube, the headline stays left of
   the head and out of the duration-badge corner. No layout warnings.
3. Without a cutout, the frame goes where the text isn't (the text never lands on it).
4. `make` writes cover_<platform>_<n>.jpg and overwrites it on the next run.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from argparse import Namespace
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins" / "vibetake" / "skills" / "edit" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import cover  # noqa: E402

SKIN = (224, 172, 140)


def person(head_w: int = 400) -> Image.Image:
    """Head (ellipse), neck, wide shoulders — a selfie-like cutout, 3× wider at the shoulders."""
    W, H = 1400, 1500
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    cx = W // 2
    d.ellipse((cx - head_w // 2, 100, cx + head_w // 2, 100 + int(head_w * 1.3)), fill=SKIN + (255,))
    d.rectangle((cx - 110, 100 + int(head_w * 1.25), cx + 110, 800), fill=SKIN + (255,))
    d.rounded_rectangle((cx - 650, 760, cx + 650, H), radius=250, fill=(30, 40, 90, 255))
    return im


def args(**kw) -> Namespace:
    base = dict(title="Edito videos solo con IA", keyword="IA", big="100%", brand="Marca", logo=None,
                colors="#101828,#FFD400,#FFFFFF", no_pattern=False)
    base.update(kw)
    return Namespace(**base)


def skin_rows(img: Image.Image) -> np.ndarray:
    a = np.asarray(img.convert("RGB")).astype(int)
    m = (np.abs(a - np.array(SKIN)).sum(2) < 30)
    return np.nonzero(m.any(1))[0]


def main() -> None:
    checks = {}
    font = cover.find_font(None)

    # 1. Head width.
    p = person(400)
    a = np.array(p.split()[-1]) > 128
    ys, xs = np.nonzero(a)
    hw, hc = cover.head_width(a[ys.min():ys.max() + 1, xs.min():xs.max() + 1])
    checks[f"head measured above the neck ({hw} px for a 400 px head)"] = abs(hw - 400) <= 6

    frame = Image.new("RGB", (1920, 1080), (90, 110, 130))
    for plat in ("reels", "youtube"):
        img, core, avoid, problems = cover.compose(plat, frame, p, args(), font)
        W, H = cover.PLATFORMS[plat][0]
        checks[f"{plat}: size {img.size}"] = img.size == (W, H)
        checks[f"{plat}: no layout warnings {problems}"] = not problems
        rows = skin_rows(img)
        a_img = np.asarray(img.convert("RGB")).astype(int)
        white = (a_img.min(2) > 235)                         # headline letters
        if plat == "reels":
            head_top = rows.min() if len(rows) else H
            text_rows = np.nonzero(white[:, int(0.1 * W):int(0.9 * W)].sum(1) > 20)[0]
            text_rows = text_rows[text_rows < head_top - 5]
            checks["reels: headline above the head"] = len(text_rows) > 0 and text_rows.max() < head_top
            checks["reels: head inside the central 3:4"] = core[1] <= head_top <= core[3] - 0.12 * H
        else:
            skin_cols = np.nonzero((np.abs(a_img - np.array(SKIN)).sum(2) < 30).any(0))[0]
            text_cols = np.nonzero(white[: int(0.6 * H)].sum(0) > 6)[0]
            text_cols = text_cols[text_cols < skin_cols.min()] if len(skin_cols) else text_cols
            checks["youtube: headline left of the face"] = len(text_cols) > 0 and text_cols.max() < skin_cols.min()
            corner = white[int(0.82 * H):, int(0.80 * W):]
            checks["youtube: duration-badge corner free of text"] = corner.sum() < 50

    # 3. No cutout: the frame only where the text isn't.
    img, core, avoid, problems = cover.compose("reels", Image.new("RGB", (1080, 1920), SKIN), None, args(), font)
    rows = skin_rows(img)
    a_img = np.asarray(img.convert("RGB")).astype(int)
    text_rows = np.nonzero((a_img.min(2) > 235).sum(1) > 20)[0]
    checks["no cutout: the frame starts below the headline"] = len(rows) and len(text_rows) and \
        rows.min() > text_rows.max()

    # 4. make → files, overwritten.
    tmp = Path(tempfile.mkdtemp(prefix="vibetake-cover-"))
    frame.save(tmp / "f.png")
    p.save(tmp / "cut.png")
    cmd = ["uv", "run", str(SCRIPTS / "cover.py"), "make", "--image", "f.png", "--cutout", "cut.png",
           "--title", "Hola mundo", "--platform", "tiktok", "--platform", "youtube", "--variant", "2"]
    subprocess.run(cmd, cwd=tmp, check=True, capture_output=True)
    first = (tmp / "03-delivery" / "cover_tiktok_2.jpg").stat().st_mtime_ns
    subprocess.run(cmd, cwd=tmp, check=True, capture_output=True)
    outs = sorted(x.name for x in (tmp / "03-delivery").iterdir())
    checks[f"make: one file per platform and variant ({outs})"] = outs == ["cover_tiktok_2.jpg",
                                                                          "cover_youtube_2.jpg"]
    checks["make: overwritten, not versioned"] = (tmp / "03-delivery" / "cover_tiktok_2.jpg").stat().st_mtime_ns > first
    checks["make: guides copy to check"] = (tmp / "02-work" / "covers" / "cover_tiktok_2.guides.jpg").exists()

    for name, ok in checks.items():
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    sys.exit(0 if all(checks.values()) else 1)


if __name__ == "__main__":
    main()
