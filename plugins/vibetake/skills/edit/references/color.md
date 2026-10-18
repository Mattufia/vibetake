# Color: commands and details

```
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py analyze            # measure only
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py propose            # Basic + Full with previews, recommendation
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py build --level basic|full|none
       [--look natural|cinematic|warm|cold|bw|vintage --intensity 60]
       [--input-lut <format>.cube] [--force-log]                 # log footage
       [--creative-lut <user>.cube --creative-intensity 100]    # the user's LUT
       [--reference <image> --reference-intensity 60]           # experimental
       [--no-match] [--match-target <file>] [--zones auto|on|off] [--no-person]
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py preview
uv run ${CLAUDE_SKILL_DIR}/scripts/match.py add --name <ref> --file <file> --t <s> --at <x>,<y>
uv run ${CLAUDE_SKILL_DIR}/scripts/match.py drop --name <ref> [--file <file>] [--restore]
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py look --look cinematic --intensity 60 --file 03-delivery/<name>.cube
```

Without files, every video in `01-ingest/material.json` is used. Output in
`02-work/color/` (`propose` uses `basic/` and `full/` subfolders; after the answer,
`build` the chosen level into `02-work/color/`, which is what `render.py` reads).

## What is measured (`color.md`)

- **Exposure**: median luma, 1st/99th percentiles, % burnt whites (on luma, not per
  channel: a saturated red isn't "burnt").
- **Cast**: mean, in linear light, of the least saturated third of the mid-tones
  (neutral surfaces, even under a cast), in Lab. Chroma ≥ 6 strong (removed
  fully), 3–6 mild (removed ~60%), < 3 left alone.
- **Skin**: hue of cheeks and nose (no lips or eyes) inside detected faces, against
  the vectorscope skin line (~123°). Only Full moves it: half the offset, capped at
  8°, rotating only hues near the measured skin hue. Similar surfaces (wood) barely
  move; check it.
- **Black/white points**: milky blacks (p1 > 0.06) or dull whites (p99 < 0.88)
  pulled ~70% toward full range.
## Shot matching (`match.py`, automatic with several files)

Every shot should look the same, from the same camera or not. It runs first, right
after the input transform, **at every level — "Only match shots" too** (matching isn't a look;
revert with `--no-match` if the user asks). Then **one** correction is measured on the
matched set and applied the same to all of them.

**What is measured: things present in every shot, never whole-frame statistics.**
Backgrounds differ between shots (white wall, dark room, a window), so frame medians
or percentiles match the numbers and leave the picture different.

- **The person** (automatic): skin (cheeks and nose), hair and clothes (dark and light
  garments apart), from the face boxes and a rembg cut-out on a few frames per shot.
- **Anything you see repeated** — a sign, a wall, a table, a garment: look at the
  frames and point at it, once per file. The point is snapped to the surface around
  it (similar color, connected). Pick flat, evenly lit spots; with a person, skin is
  already the strongest reference.
  `match.py add --name sign --file A.mp4 --t 3.2 --at 0.42,0.31` (x, y as 0–1 of the frame).
- **Leave out** what you see is lit differently in one shot (a jacket in the sun in
  one, in shade in the other): `match.py drop --name clothes_light --file B.mp4`.
- Only as a last resort (no shared references): low-saturation surfaces per tonal
  zone — low confidence, flagged.

**Per shot**: white balance (gains) → exposure → a smooth tone curve (crushed or
lifted blacks, contrast; past white, a shoulder so nothing new clips) → a small color
offset per tonal zone, only when the cameras differ (`--zones on` to force).

**Target: a neutral base with headroom**, so the creative grade has room later: skin
and mids at the shots' middle value; darks as deep as the deepest shot that still has
texture (black hair stays black, not a gray average); lights as bright as the
brightest one that isn't clipped, below pure white. `--match-target <file>` matches to
one shot instead.

**Rules the code enforces** (it rejects or flags instead of applying):

1. The order of brightness never inverts — what was lighter stays lighter.
2. Detail is kept: each reference's texture is measured before and after.
3. No new clipped blacks or whites.
4. Gentle changes: bounded slopes, gains, exposure and color offsets.
5. A shot that can't be matched well is flagged, not forced: no shared references →
   "not matched" and its own correction; detail lost at the source (crushed blacks,
   burnt whites) → not forced; a reference that contradicts the others (lit
   differently, or too close in brightness to a stronger one) → dropped in that shot,
   lowest priority first (skin > darks and lights > the rest).

**Check before rendering** (`02-work/color/match/`): `match.md` (each reference per
shot before → after, the spread between shots, what was dropped and why, warnings),
`sheet.jpg` (each reference in every shot, before | after), `faces.jpg` (faces at full
resolution — flat or plastic skin shows there) and `regions.jpg` (what was measured:
if a region landed on the background, `drop` it and `add` the right one). Then
`grade.py build` again. Warnings go in the delivery note.

## How the grade is built

- **Log**: correction in log, before the format's official LUT (exposure and
  balance as offsets; the size of one stop is measured on the LUT itself, since
  log curves differ). See `log-formats.md` for where LUTs come from.
- **HDR**: tone mapping as a pre-filter (stored in `grade.json`), then the LUT.
- After building, it re-measures through the grade (cast and exposure before →
  after) and refines when needed.
- `grade.json` says, per file, the pre-filter and LUT that `render.py` applies.
- Previews: before/after with waveform and vectorscope, a few frames per file.
- Faces are only used to measure. A person mask for skin (dilated outward, with a
  wide feather) is planned, not available yet.

## Repairs (only when needed)

Pre-filters, before the correction (correcting amplifies noise and false color),
stored in `grade.json` like the HDR tone mapping:

- `--moire`: false color — color blotches on fine fabrics, stripes or meshes that
  aren't in the cloth. It depends on the camera and the clothes and comes from the
  raw, so confirm it on a 100% still of the raw before anything is corrected; if it
  isn't there, nothing is touched. It blurs only the chroma (the detail stays);
  check that face, hair and background edges don't smear.
- `--denoise light|medium`: low-light noise (`hqdn3d`, temporal + spatial). Not by
  default: grain is sometimes wanted. When noise is visible and nobody asked, offer
  it in the delivery instead of applying it; when applied, check the skin doesn't go
  plastic.
