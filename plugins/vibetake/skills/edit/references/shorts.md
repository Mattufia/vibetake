# Shorts from a long video

Several vertical shorts (Reels, TikTok, Shorts) out of one talk, class, podcast or YouTube
video. Each short is a **variant** with its own cut, framing, captions, graphics, cover and
copy, rendered as `<video>_v<N>_<name>.mp4` next to the long one.

```
uv run ${CLAUDE_SKILL_DIR}/scripts/shorts.py add --name precio --keep "L14-L22" [--hook L18] [--title "…"]
uv run ${CLAUDE_SKILL_DIR}/scripts/shorts.py list
uv run ${CLAUDE_SKILL_DIR}/scripts/shorts.py make [--only precio] [--preview]     # vertical, --pace tight
uv run ${CLAUDE_SKILL_DIR}/scripts/qa.py --variant precio --platform reels --platform tiktok
```

## Choosing them (read the whole `transcript.md` first)

A short is **one idea that stands on its own** for someone who never saw the long video:

- **It starts clean**: the first line needs nothing said before ("and", "so", "that one…" are
  a sign it does: start a line earlier, or open with the hook).
- **It lands**: it ends on a conclusion, a punchline, a number, a turn — never mid-thought or on
  a "so, next…".
- **It has a hook**: a line inside it that works alone in 1.5–4 s and opens a loop (a promise,
  a surprising claim, a conflict, a question) — `--hook` shows it first and again in its place.
  If the first line already hooks, no `--hook`.
- **20–60 s** is the usual sweet spot; up to ~90 s when the story needs it (every platform takes
  up to 3 min, attention doesn't).
- **The strongest moments, not an even spread**: 3–5 good shorts beat 10 weak ones. Different
  ideas each (two shorts on the same point only on purpose: `add` flags shared lines).
- Ranges leave their retakes out (the kept take stays); name a single line to force one in.

Name them by their idea (`precio`, `error-comun`): letters, digits, hyphens.

## Making them

`make` runs, per short: `cutlist.py --variant <name>` (its lines; not the long video's chapters
or montage block), `reframe.py`, `subtitles.py`, the short's own graphics layer (captions only
until its graphics are planned: `graphics_<name>.json`, never the long video's plan), and
`render.py`. Then, per short:

- **Look at it** start to end: does it stand alone? Is the first second strong?
- **Graphics** when the type calls for them: plan `02-work/graphics_<name>.json` for this short
  (`references/graphics.md`), `graphics.py cues --variant <name>`, `graphics.py render --variant <name>`, render again.
- **Cover**: concepts with `"short": "<name>"` in `02-work/covers.json` (each can list its own
  `"platforms"`) → `cover_<name>_<platform>_<n>.jpg`; `cover.py candidates --variant <name>` takes
  frames from the short's cut, `cover.py preview --variant <name>`.
- **Copy**: `03-delivery/copy_<name>.txt`, same sections as `copy.txt` (`references/copy.md`),
  written for this short (its hook, its idea).
- **QA**: `qa.py --variant <name>` with its platforms.

In the delivery: a line per short (name, length, its idea, its hook).
