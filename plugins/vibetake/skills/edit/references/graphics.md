# Graphics and sound design

What follows is craft (timing, legibility, bands, checks) and a set of starting
points. The look itself — how much is on screen, what kind of graphics, whether the
footage is full frame or placed inside a composition — comes from the grammar chosen
in `design.md`. A sober editorial piece and a loud feed explainer both follow this
file; they shouldn't look alike.

## How much: a starting point, not a quota

| Level | Usually for | Graphics | Sound effects |
|---|---|---|---|
| **Dynamic** | feed videos: talking head, ad, vlog, short tutorials | frequent support for what's said | often, on the events that matter |
| **Measured** | long tutorials, podcast clips, explainers, corporate for social | names, numbers, steps, key phrases | scene changes and the key reveal |
| **Minimal** | testimonial, interview, formal corporate, music-driven montage | lower thirds, titles, the close | none, or only transitions |

The profile suggests a level; the request and the grammar override it ("keep it
sober", "lots of motion", an editorial composition that holds one frame for a whole
section). Density is a design decision: a few strong elements usually beat many
weak ones. Colors and typefaces come from the brand when there is one (SKILL.md
section 7).

## Scene plan from the transcript

`graphics.py cues` writes `02-work/cues.json`: every kept line with each word's
timeline time and frame, the cuts, the hook's span, where the face is every 0.5 s,
the safe zone and the caption position. Plan on it:

- **One scene per idea** (often one per line or group of lines).
- **Elements enter one at a time, on the frame of the word that names them.**
  Never the whole scene at once.
- **Keywords, not sentences.** On screen long enough to read (~0.3 s per word +
  0.5 s), then out.
- Not every line needs a graphic: leave breathing room, or everything becomes noise.

## Ideas, if you don't have a better one

These are the obvious answers. Use them when they're right, but first ask what
graphic would come from *this* video's world (the chat, the receipt, the map, the
product's own UI) — the obvious answer on every video is what makes them all alike.

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

## The first frame (feed)

Autoplay starts on the first frame and it's the first impression: something there
should make the viewer stay. Often that's the hook as a headline that completes with
what's said in the first seconds, but it can be the composition itself, a striking
image or a question.

**A graphic that shows what's being said replaces the caption** — the same words twice
is noise. `graphics.py` does it on its own: while an element is on screen, a caption
group whose words are mostly in that element's text is left out of the layer (it
says where). `"captions": "hide"` on an element hides every caption under it (a
graphic that says the same thing in other words); `"captions": "show"` keeps them.
The `.srt` keeps everything. With `--engine ass` it's manual: `subtitles.py --off`
with timeline ranges.

## Animation

- Nothing that takes time to read: entries usually 0.2–0.4 s, exits faster.
  The character (springy and playful, stiff and precise, slow cinematic fades,
  hard cuts with no animation at all) comes from the design.
- One motion language across the video: the same easing, the same direction for the
  same kind of element.

## Bands that don't overlap

The face, the graphics and the captions each keep their own band, all inside the
safe zone (`safe-margins.md` for 9:16). `cues.json` → `face` says where the head
is; captions sit low unless they had to move around the face (check `captions.json` or `subs.ass`
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

   **A stage** (the composition grammar, `design.md` section 2) turns the frame into a
   designed page for a span of the video: its own background, and the edit's picture
   placed in a box. Everything else in the plan during that span (headlines, labels,
   custom components) is drawn on top of it, and the captions on top of all.

```json
{"type": "stage", "from": {"t": 12}, "until": {"word": "gracias"}, "background": "paper", "enter": "shrink",
 "footage": {"x": 0.34, "y": 0.5, "width": 0.56, "height": 0.62, "radius": 14, "shadow": "soft",
             "fit": "cover", "position": "50% 30%", "frame": "MacWindow", "props": {"title": "Demo · MaxiBici"}}},
{"type": "custom", "from": {"t": 12}, "until": {"word": "gracias"}, "component": "PageChrome", "page": true}
```

   - `footage`: the box (`x`, `y` center, `width`, `height`, 0–1 of the frame),
     `radius`, `shadow` (`soft`, `lift`, `glow`…), `fit` (`cover` crops to fill,
     `contain` shows the whole picture) and `position` (which part stays when it
     crops). `"footage": false` hides the picture: a full-page title card or chapter
     break.
   - `frame`: a custom component that dresses the picture (a window with its title bar,
     a phone, a monitor, a torn-paper edge); it receives the picture as `children`, plus
     `startFrame`, `endFrame`, `sizePx` and `progress` (0→1 as the stage enters), and
     fills the box. The page around it (crop marks, a section counter, a running title,
     margins with notes) is ordinary custom elements on top, with `"page": true` so the
     component gets the whole frame instead of a centered box. Both are written for this
     video, in its design — not taken from a stock set.
   - `enter`: `shrink` (the picture starts full frame and settles into its box while the
     page fades in — seamless, since it's the same picture), `fade` or `cut`. The exit
     plays it backwards and is done by `until`.
   - `Footage` (`../kit/Footage`) is the same picture as a component: a custom element
     can use it for a split, a picture-in-picture or a zoomed detail.
   - Use stages for a section or the whole video; outside them the layer is the usual
     transparent overlay. The first render with a stage also renders the clean edit
     (`02-work/footage.mp4`, again when the cut or the grade changes), so it takes
     longer. Check stills: the face and safe-zone warnings are skipped while a stage
     is up — the composition is yours to judge.

   **Before writing a custom component, load the installed Remotion skill that covers
   it** (if it's in the skill list; by hand otherwise), take its technique and keep
   the kit's conventions (`src/custom/`, design tokens, one transparent layer):
   - a map, a route, a city, a "we're here" pin, a flyover → `remotion-maps`
     (real tiles and an animated camera; MapLibre needs no key, Mapbox only with the
     user's key) — far better than a drawn map;
   - 3D, light leaks, motion blur, text highlights, audio visualization, Lottie, GIFs,
     transitions between scenes → `remotion-markup` (one file per topic: `3d.md`,
     `light-leaks.md`, `motion-blur.md`, `text-highlights.md`, `lottie.md`…);
   - an API detail none of them answers → `remotion-docs`; `remotion-best-practices`
     routes the rest.
   Captions come from `subtitles.py` (by default this layer draws them,
   `src/kit/Captions.tsx` — not `remotion-captions`) and the layer renders with `graphics.py`; the other Remotion
   skills (create, studio, saas, render) assume a standalone project — not this one.
3. **Look before rendering it all**: `graphics.py init` once, then `graphics.py still
   <t> …` — the layer over the real frame, with warnings for the safe zone and the
   face. Fix and look again.
4. `graphics.py render` → `02-work/graphics.mov` (ProRes 4444 with alpha);
   `render.py` lays it over b-roll and under subtitles. Re-run `graphics.py cues` and
   the render whenever the edit changes (`subtitles.py`
   first: `sync` refuses captions made for another cut).
5. **Captions in the layer** (`subtitles.py`, default engine): drawn on top of every
   element, except where a graphic already shows the words (above). The Graphics composition's `captionStyle` adjusts them without touching the
   timing: `animation` (pop · karaoke · bounce · none), `yShift`, `scale`,
   `highlightColor`, `textColor`, `box`, `upper`. In Studio (`npm run studio` in
   `02-work/remotion`) they're in the props panel; saving writes them into `src/Root.tsx`,
   which `graphics.py init` then leaves alone.

**Without Node** (Remotion can't run): say so; graphics are then drawn as a canvas page
with a deterministic `frame(t)` (`references/from-scratch.md`), with the same design.

## Sound design

Sound follows the same style as the picture: a loud feed edit can put a sound on
most events; a sober or editorial one may use one soft texture, only transitions, or
nothing. Typical pairings when there is sound:

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
