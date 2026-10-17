# Graphics and sound design

## How much: set by the video, not a house style

| Level | When | Graphics | Sound effects |
|---|---|---|---|
| **Dynamic** | feed videos: talking head, ad, vlog, short tutorials | every idea gets visual support; an element every 2–5 s | on most visual events |
| **Measured** | long tutorials, podcast clips, explainers, corporate for social | names, numbers, steps, key phrases; an element every 10–20 s | on scene changes and the key reveal |
| **Minimal** | testimonial, interview, formal corporate, music-driven montage | lower thirds, titles, the close | none, or only transitions (in a montage the music is the sound design) |

The type profile names the level; the request overrides it ("keep it sober", "lots
of motion"). Colors, typefaces and look come from the brand (SKILL.md section 7);
what follows is *how* to edit, which holds for any brand.

## Scene plan from the transcript

`graphics.py cues` writes `02-work/cues.json`: every kept line with each word's
timeline time and frame, the cuts, the hook's span, where the face is every 0.5 s,
the safe zone and the caption position. Plan on it:

- **One scene per idea** (often one per line or group of lines).
- **Elements enter one at a time, on the frame of the word that names them.**
  Never the whole scene at once.
- **Keywords, not sentences.** On screen long enough to read (~0.3 s per word +
  0.5 s), then out.
- Not every line needs a graphic: follow the level's rhythm and leave breathing
  room, or everything becomes noise.

## A graphic for each kind of idea

Pick what fits the content; these are starting points, not a checklist.

| Idea | Graphic |
|---|---|
| a number, price, percentage | counter rolling up to the value; comparative bars |
| "not X but Y", before/after, myth vs fact | strike-through; split comparison |
| a process or steps | numbered steps, or a flow with arrows |
| yes/no, pros/cons, do/don't | ✓ / ✕ list |
| a list | items stacking in, one per mention |
| a person introduced | lower third with name and role |
| a place or date | label, map pin |
| a product, tool or brand | logo or product card |
| a key phrase or quote | large kinetic text |
| a question to the viewer | question card |
| the close | call to action with icons of what to do (follow, comment, link) |

## The hook as a graphic (feed)

The headline is on screen **from the first frame** (autoplay starts there, and it's
the first impression) and completes with what's said in the first seconds. While
it's up, no burned-in captions — the text is already the graphic:
`subtitles.py --off hook` (or timeline ranges, for any other graphic that shows the
same words). The `.srt` keeps everything.

## Animation

- Entries 0.2–0.4 s with a spring (a slight overshoot reads as energy); exits fast
  (~0.15 s) when the scene ends. Nothing that takes time to read.
- The same motion language across the video: the same easing, the same direction
  for the same kind of element.

## Bands that don't overlap

The face, the graphics and the captions each keep their own band, all inside the
safe zone (`safe-margins.md` for 9:16). `cues.json` → `face` says where the head
is; captions sit low unless they had to move around the face (check `subs.ass`
placement). In vertical talking heads the graphics band is usually chest height or
the top third, whichever the face leaves free. **Check stills after the zooms**:
the face moves with punch-ins and the impact zoom. A small brand mark in a safe
corner from the moment the brand is introduced is optional, per brand.

## Building and rendering the layer

1. **Design** (`references/design.md`): `02-work/design.json`, made for this video —
   typefaces (`fonts.py get`), colors, surface, highlight, motion. Icons with
   `icons.py brand|ui` or drawn as SVG into `02-work/icons/custom-*.svg`.
2. **Scene plan**: `02-work/graphics.json`, one entry per element:

```json
{"elements": [
  {"type": "headline", "from": {"t": 0}, "until": {"t": 2.9}, "x": 0.46, "y": 0.4, "width": 0.7, "size": 0.05,
   "lines": ["POV:", "tu compañero", "no te responde"], "highlight": ["responde"], "surface": true},
  {"type": "label",   "from": {"word": "sistema"}, "until": {"t": 13.6}, "title": "…", "subtitle": "…", "surface": true},
  {"type": "counter", "from": {"t": 3}, "to": 37, "fromValue": 1, "suffix": " sin leer", "color": "accent"},
  {"type": "list",    "from": {"line": 4}, "items": [{"icon": "ui-check", "text": "…", "at": {"word": "…"}}]},
  {"type": "cta",     "from": {"word": "escribinos"}, "icon": "brand-whatsapp", "lines": ["Escribinos", "por WhatsApp"]},
  {"type": "icon",    "from": {"word": "mensajes"}, "name": "ui-chat-circle-dots-duotone", "anim": "draw"},
  {"type": "custom",  "from": {"t": 5}, "component": "MyChart", "props": {}}
]}
```

   Timing: `{"t": seconds}`, `{"word": "x", "after": seconds}` (when it's said),
   `{"line": n, "word": i}`; `"sync": true` on a headline makes each word enter as
   the voice says it. Position: `x`, `y` (center, 0–1 of the frame), `width`, `size`
   (font size, share of the height), `align`, `rotate`, `wipe`. Per element, `enter`,
   `unit` (word/char/line), `color`, `surface` (true, or overrides) change the design
   locally. Anything the kit doesn't cover (a chart, an illustrated scene, a mock
   phone screen) is a React component of its own in `02-work/remotion/src/custom/`,
   built on the kit's pieces and the design tokens — SVG is written inline exactly as
   in HTML, and its paths can be animated.
3. **Look before rendering it all**: `graphics.py init` once, then `graphics.py still
   <t> …` — the layer over the real frame, with warnings for the safe zone and the
   face. Fix and look again.
4. `graphics.py render` → `02-work/graphics.mov` (ProRes 4444 with alpha);
   `render.py` lays it over b-roll and under subtitles. Re-run `graphics.py cues` and
   the render whenever the edit changes.

**Without Node** (Remotion can't run): say so; graphics are then drawn as a canvas page
with a deterministic `frame(t)` (`references/from-scratch.md`), with the same design.

## Sound design

| Event | Sound (`sfx.py --kind`) |
|---|---|
| scene change | `whoosh` — its peak on the cut |
| an element enters | `pop` or `click` |
| punchline, reveal | `hit` |
| build-up to a key moment | `riser` — peaks on the moment |
| ✓, success, the call to action | `ding` |

```
uv run ${CLAUDE_SKILL_DIR}/scripts/sfx.py place --kind whoosh --cut 3
uv run ${CLAUDE_SKILL_DIR}/scripts/sfx.py place --kind pop --line L5 --word ventas
uv run ${CLAUDE_SKILL_DIR}/scripts/sfx.py place --file <sound> --event 0.2 --line L7   # the user's own
uv run ${CLAUDE_SKILL_DIR}/scripts/sfx.py check
```

- **Anchor the sound to the same word as the graphic**: picture and sound land on
  the same frame, and survive re-cuts.
- **The voice rules**: sounds sit ~10 dB under it by default (`--gain` to adjust). A
  sound on a stressed word: lower it or move it into the pause.
- **Don't stack**: two sounds closer than ~120 ms blur into one (`check` warns).
  Five elements in two seconds don't need five pops — pick the one that matters.
- The synthesized set is clean, license-free and generic. Brand or scripted work
  deserves real effects: the user's, or a library without attribution (same rules
  as music, `music.md`); write each source and license in `project.md`.
