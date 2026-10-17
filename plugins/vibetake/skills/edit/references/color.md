# Color: commands and details

```
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py analyze            # measure only
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py propose            # Basic + Full with previews, recommendation
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py build --level basic|full|none
       [--look natural|cinematic|warm|cold|bw|vintage --intensity 60]
       [--input-lut <format>.cube] [--force-log]                 # log footage
       [--creative-lut <user>.cube --creative-intensity 100]    # the user's LUT
       [--reference <image> --reference-intensity 60]           # experimental
       [--match]                                                 # force shot matching
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py preview
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
- **Shot matching**: with several files whose median luma spreads more than 0.08,
  exposure is matched to the median automatically.

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
