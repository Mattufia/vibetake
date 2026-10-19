# Color: commands and details

```
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py analyze            # measure only
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py propose            # Basic + Full with previews, recommendation
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py build --level basic|full|none
       [--look natural|cinematic|warm|cold|bw|vintage --intensity 60]
       [--input-lut <format>.cube] [--force-log]                 # log footage
       [--creative-lut <user>.cube --creative-intensity 100]    # the user's LUT
       [--reference <image> --reference-intensity 60]           # experimental
       [--ambient balanced|neutral|keep] [--no-match] [--match-target <file>] [--zones auto|on|off]
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py preview
uv run ${CLAUDE_SKILL_DIR}/scripts/match.py solve                     # sheet + matching (again after each change)
uv run ${CLAUDE_SKILL_DIR}/scripts/match.py group --name <scene> <files...> [--no-skin]
uv run ${CLAUDE_SKILL_DIR}/scripts/match.py skip <files...> [--restore]
uv run ${CLAUDE_SKILL_DIR}/scripts/match.py add --name <ref> --file <file> --t <s> --at <x>,<y>
uv run ${CLAUDE_SKILL_DIR}/scripts/match.py drop --name <ref> [--file <file>] [--restore]
uv run ${CLAUDE_SKILL_DIR}/scripts/match.py set --file <file> exposure=+0.2 black=-2 temp=-4 ... [--clear]
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py look --look cinematic --intensity 60 --file 03-delivery/<name>.cube
```

Without files, every video in `01-ingest/material.json` is used. Output in
`02-work/color/` (`propose` uses `basic/` and `full/` subfolders; after the answer,
`build` the chosen level into `02-work/color/`, which is what `render.py` reads).

## What is measured (`color.md`)

- **Exposure**: median luma, 1st/99th percentiles, % burnt whites (on luma, not per
  channel: a saturated red isn't "burnt").
- **Cast**: mean, in linear light, of the least saturated third of the mid-tones
  (neutral surfaces, even under a cast), in Lab, split into temperature and tint;
  what is removed depends on the ambient setting (below).
- **Skin**: hue of cheeks and nose (no lips or eyes) inside detected faces, against
  the vectorscope skin line (~123°). Only Full moves it: half the offset, capped at
  8°, rotating only hues near the measured skin hue. Similar surfaces (wood) barely
  move; check it.
- **Black/white points**: milky blacks (p1 > 0.06) or dull whites (p99 < 0.88)
  pulled ~70% toward full range.

## Shot matching (`match.py`) — by looking, then measuring

Matching runs first, right after the input transform, **at every level — "Only match
shots" too** (matching isn't a look; revert with `--no-match` if the user asks). It
doesn't guess from where things usually are in a frame: **you decide by looking**, the
code measures, fits and checks. With several shots, before the color question:

1. `match.py solve` → look at `02-work/color/match/sheet.jpg` (every shot, before |
   after, with its numbers).
2. **Group what shares place and light** — the same room, the same set, the same
   moment: `match.py group --name kitchen A.mp4 B.mp4`. A controlled shoot is usually
   one group; a vlog, one group per place. **Different places aren't matched to each
   other**: the beach and the kitchen should look different. A shot in no group keeps
   its own look. Different people in a group: `--no-skin` (skin is only shared by the
   same person).
3. **Skip what isn't camera footage** — screen recordings, graphics, archive, slides:
   `match.py skip C.mp4` (no correction, no look).
4. Optional — **point at what repeats** when skin isn't enough or there's no person (a
   wall, a sign, hair, a garment): `match.py add --name wall --file A.mp4 --t 3.2 --at
   0.42,0.31`, once per file of the group (x, y as 0–1 of the frame; the point is
   snapped to the surface around it — flat, evenly lit spots). `drop` what you see is
   lit differently in one shot.
5. `match.py solve` again → check `sheet.jpg`, `faces.jpg` (faces at full resolution:
   flat or plastic skin shows there) and `match.md`. If a shot still looks off, nudge
   it: `match.py set --file B.mp4 exposure=+0.2 black=-2 white=+3 temp=-4 tint=+2
   contrast=+0.1` (capped to gentle ranges; `--clear` resets). Then `grade.py build`.

**Inside a group**, every shot goes to the group's base: white balance (gains) →
exposure → a smooth tone curve (crushed or lifted blacks, contrast; past white, a
shoulder so nothing new clips) → a small color offset per tonal zone when the group
mixes cameras. References: the skin (automatic, from the face boxes) and what you
added. **Target: a neutral base with headroom**, so the creative grade has room later:
skin and mids at the shots' middle value; darks as deep as the deepest shot that isn't
crushed (black hair stays black, not a gray average); lights as bright as the brightest
that isn't burnt. `--match-target <file>` matches the group to one shot instead.
**One correction per group** follows (grade.py), so the group stays alike.

**Light that changes within a shot**: one correction for the whole shot, set by its most
common light (measured on what the edit keeps, when the edit exists). A shot split half
and half between two lights is flagged — say so in the delivery.

**Rules the code enforces** on everything applied (it rejects or flags instead):

1. The order of brightness never inverts — what was lighter stays lighter.
2. Detail is kept: skin and references' texture measured before and after.
3. No new crushed blacks or burnt whites.
4. Gentle changes: bounded slopes, gains, exposure, color offsets and nudges.
5. What can't be matched well is flagged, not forced: nothing shared → "not matched";
   detail lost at the source → not forced; a reference that contradicts the others →
   dropped in that shot, lowest priority first (skin > darks and lights > the rest).

Warnings go in the delivery note.

## Ambient light (white balance)

`--ambient` (grade.py), per video — written in `project.md` when the user asks for one:

- **balanced** (default): the temperature of the light (warm ↔ cool) is the place's
  mood and stays — a sunset stays warm, a kitchen under warm bulbs too (only an extreme
  one is eased); the tint (green ↔ magenta) is almost always an error (fluorescents,
  cheap LEDs, mixed light) and is removed.
- **neutral**: both removed ("neutralize everything").
- **keep**: both left ("leave the light as it is").

The user picks it in the color question's free text; never carried over from another video.

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
