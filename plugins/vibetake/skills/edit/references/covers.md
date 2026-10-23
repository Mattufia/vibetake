# Covers and thumbnails

**Three different concepts**, designed for this video — not one layout with three
texts. Each is a free canvas (a React component, rendered as a still per platform),
so a concept can be anything: the video's own idea turned into an image, the
person's expression with a short line, something bold. Made without asking first;
the user picks, or asks for changes.

| Platform | File | Size | What must survive |
|---|---|---|---|
| YouTube | thumbnail | 1280×720, ≤2 MB | the bottom-right corner is covered by the duration badge |
| Instagram Reels | cover | 1080×1920 | the reel shows it whole; the profile grid crops the central 3:4 (1080×1440) |
| TikTok | cover | 1080×1920 | the profile grid crops the center: same rule |
| YouTube Shorts | thumbnail | 1080×1920 | same rule; check whether the account can upload a custom one |

## The steps

```
uv run ${CLAUDE_SKILL_DIR}/scripts/cover.py candidates            # frames: face visible, big, sharp
uv run ${CLAUDE_SKILL_DIR}/scripts/cover.py cutout --time 14.5    # the person, cut out and located
# write 02-work/covers.json + the concepts in 02-work/remotion/src/covers/index.tsx
uv run ${CLAUDE_SKILL_DIR}/scripts/cover.py render                # → 03-delivery/cover_<platform>_<n>.jpg
uv run ${CLAUDE_SKILL_DIR}/scripts/cover.py preview               # at real size: profile grid / feed
```

1. **The frame.** `candidates` keeps only moments in the edit with a face that's
   visible, confidently detected, as big as the video's best and sharp — never a back,
   a profile or someone across the room. Choose among them for the **expression and
   gesture** that fit the concept (a laugh, surprise, pointing, hands up at chest
   height), eyes open, mouth not mid-syllable.
2. **The person, cut out — by default.** `cutout` grabs the frame with the grade,
   cuts the person out (rembg `birefnet-portrait`), trims it and records where the face
   is. If the cut fails or looks wrong, try another frame; never fall back silently.
   Without a cut-out only when the user asks (a concept can use the whole frame:
   `<Frame>`). Check hair, hands and edges.
3. **Three concepts** (`02-work/covers.json`):

   ```json
   {"platforms": ["reels", "youtube"],
    "concepts": [{"id": "1", "component": "ChatUnread", "idea": "the video's idea: …"},
                 {"id": "2", "component": "BigFace",    "idea": "the face, huge, one short question"},
                 {"id": "3", "component": "SeenTicks",  "idea": "bold: …"}]}
   ```

   Each component gets `platform`, `width`, `height` and `keep` (the area that
   survives the crop) and composes freely: `<Person file="cutout_014.50.png"
   faceCenter={[x, y]} faceHeight={px} align="balanced" />` places the person by the
   face (the body leaves through the bottom edge on its own). For a centered person use
   `align="visual" canvas={[width, height]}`: it centers what stays visible after the
   crop, mostly by the head (the eye reads the head, not the shoulders leaving through
   the edges); `align="balanced"` is the midpoint of face and shoulders. Brand assets
   as provided (`staticFile("brand/<file>")`), the video's design
   tokens (`font()`, `color()`), the project's icons, inline SVG drawn for the cover,
   CSS of any kind. Lay it out per platform (vertical and 16:9 are different
   compositions, not a resize).
   A short (`shorts.py`) gets its own concepts in the same file: `"short": "<name>"` (and its
   own `"platforms"` if they differ) → `cover_<name>_<platform>_<n>.jpg`; `candidates
   --variant <name>` and `preview --variant <name>` work on that short.
4. **Look at them** at real size (`preview`) and as a viewer — balance, layering, does each read in under
   a second, is the face big and expressive, does it stand out next to others, is
   anything cut by the grid. `render` already warns when there's no face, the face is
   small, or it falls outside the crop. Fix and render again.

## What makes a cover work

- **One idea, readable at thumbnail size**: a few words (2–4) at a size that reads on a
  phone, one of them emphasized. Never a sentence copied from the transcript.
- **A face, big** — a quarter of the height on YouTube, a good share on vertical — with
  an expression; eyes and mouth never under text.
- **Contrast and separation**: the person and the headline pop off the background
  (outline, glow, a darker or brighter backdrop, depth).
- **Balance by the person's visual weight, not the face's position**: a body that
  leans or a raised arm moves the weight; center the silhouette, or set it off-center
  on purpose with something on the other side (text, a graphic) that balances it.
  Never off-center by accident.
- **Layers for depth**: background → big type → the person → a small element in front
  (a sticker, a badge). The head or shoulders covering part of the big type (a number,
  the edge of a bubble) makes it read as 3D and ties the person to the idea. Cover only
  what still reads — never the key word — and add a soft shadow from the person onto
  what's behind.
- **The video's design as the starting point** — its typefaces and palette, so the
  cover and the video feel like one thing — but the cover is free to be louder:
  bigger type, more saturation, a bolder composition. Breaking from the video's
  design is fine when it makes a better cover; say why in the delivery.
- **Three ideas that really differ**: what's shown, how it's framed, what it says —
  not three colors of the same layout.
- **References**: if `references/` has covers or styles the user likes (`refs.py`), take
  their criteria — composition, intensity, how much text — never copy them.
- Brand assets only as provided (`brand/`); never searched for or recreated.

**Without Node** (Remotion can't run): draw each concept as a canvas page, like the
graphics (`from-scratch.md`), with the same design and the same checks; say so.
