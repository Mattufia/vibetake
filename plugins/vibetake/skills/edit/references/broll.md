# B-roll

```
uv run ${CLAUDE_SKILL_DIR}/scripts/broll.py list
uv run ${CLAUDE_SKILL_DIR}/scripts/broll.py place --clip <name> --line L5 [--word <word>] [--in 2.0] [--dur 2.5] [--offset -0.2]
uv run ${CLAUDE_SKILL_DIR}/scripts/broll.py place --clip <name> --cut 3 [--dur 2.0]
uv run ${CLAUDE_SKILL_DIR}/scripts/broll.py check
uv run ${CLAUDE_SKILL_DIR}/scripts/broll.py clear
```

## Sources, in order

1. **The user's own material.** Ingest flags clips with little speech and no face
   talking to camera as candidates (`→ B-ROLL CANDIDATE` in `material.md`). `list`
   gathers them into `02-work/broll.json` with their contact sheets. **Look at the
   sheets and write in the catalog what each clip shows** (`description`) and which
   ranges are usable (`usable`: skip shaky starts, focus hunting, the hand reaching
   for the stop button).
2. Stock the user downloads by hand (Pexels, Pixabay: commercial use without
   attribution, no resale; in ads avoid recognizable brands or people). Keep the source
   URL of every clip in `project.md`.

## Placement

Each placement is anchored to a moment of the source — a word of a transcript line,
or the cut before a segment — not to a timeline second, so re-cutting doesn't move
it off its word. `render.py` lays it over the edit: the speech keeps playing, the
clip is framed to the output, graded like its file in `grade.json`, with a slow 4%
push-in. `check` shows where each one lands, overlaps and total coverage.

## Rules

- **Illustrate what's being said**: start the clip on (or a beat before) the word
  that names it. Never random filler.
- **Cover cuts**: a b-roll centered on a jump cut (`--cut N`) hides it as well as a
  punch-in, and varies the rhythm. Alternate the two.
- **1.5–4 s each.** Shorter flickers; longer loses the speaker.
- **Never over the hook** and not over the key emotional moments: the person has
  to be seen saying the important lines.
- **Coverage**: talking head 15–35% of the runtime; tutorial: screen captures count
  as the main picture, b-roll is rare; vlog and montage: much more.
- **Don't reuse the same clip** in one video unless it's a deliberate callback.
- After placing, render a preview and look at stills on each b-roll: framing,
  nothing important cropped by the vertical crop, color matching the speaker shots.
