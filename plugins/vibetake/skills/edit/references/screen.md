# Zooming into screen recordings

On a phone, a full screen recording is unreadable. Zoom where the action is — the menu that
opens, the button that's clicked, the field being filled, the value that matters — and come
back out to show where it is. Never reframe or punch into a screen recording otherwise.

```
uv run ${CLAUDE_SKILL_DIR}/scripts/screen.py find [--files rec.mp4] [--max 2.5]
uv run ${CLAUDE_SKILL_DIR}/scripts/screen.py keep Z1,Z3,Z4
uv run ${CLAUDE_SKILL_DIR}/scripts/screen.py set Z3 --box 0.62,0.08,0.3,0.2 [--from 12.1] [--to 15.0] [--max 2]
uv run ${CLAUDE_SKILL_DIR}/scripts/screen.py add --file rec.mp4 --from 40.2 --to 43.5 --box x,y,w,h
uv run ${CLAUDE_SKILL_DIR}/scripts/screen.py drop Z4 | list | clear
```

## 1. Proposals

`find` looks at every screen recording in the edit (files with almost no face, or `--files`)
for what changes — compared with a second before, so typing adds up to a word and a moving
cursor stays two specks. It ignores the cursor, a blinking caret and whole-page changes, joins
changes in the same place a few seconds apart (open a menu, pick, close: one zoom), starts each
zoom just before the change and holds it 1.5 s after, to read. Sheets in `02-work/screen/`: the
frame, what changed (green), what the zoom will show (yellow); `proposals.md` says what's being
said at each.

## 2. You choose

**Look at every sheet.** Keep the zooms that help the viewer follow: a click they must see, a
value, a small menu, code being typed. Drop hovers, tooltips and anything already readable.
Too many zooms tire; between steps, the full screen shows where things are. What's said is the
best guide: "click Export here" wants a zoom on Export.

- A box that's off (too big, missed the button): `set Zn --box` (0-1 of the picture, read it
  off the sheet), or `--from/--to` to hold it longer.
- A moment it missed (a mouse pointing at something that doesn't change): `add`.
- `--max` caps the zoom (2.5× by default; less on a low-resolution recording: past ~2× of a
  1080p screen the text softens).

## 3. Render

The zoom eases in (0.45 s), holds, eases out; two zooms in a row glide from one to the other.
Times are seconds of the source file, so re-cuts keep them. In a vertical edit, the narrow crop
goes to the action — that's what makes a screen readable on a phone; check each zoom keeps the
whole button/value inside. A camera shot in the same edit keeps its own framing (reframe.py).
Callouts (a ring on the click, the shortcut) go in the graphics layer, over the zoom.
