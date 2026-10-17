# Covers and thumbnails

**One per platform it's published on**, because each shows it differently:

| Platform | File | Size | What must survive |
|---|---|---|---|
| YouTube | thumbnail | 1280×720, ≤2 MB | the bottom-right corner is covered by the duration badge |
| Instagram Reels | cover | 1080×1920 | the reel shows it whole; the profile grid crops the central 3:4 (1080×1440) |
| TikTok | cover | 1080×1920 | the profile grid crops the center: same rule |
| YouTube Shorts | thumbnail | 1080×1920 | same rule; check whether the account can upload a custom one |

`cover.py make` builds every requested platform from the same frame and headline,
each with its own layout. **2–3 variants** to choose from (`--variant 1|2|3`), each
with a different frame and gesture; the composition stays. Files are overwritten
on every run (no versions for covers); `02-work/covers/*.guides.jpg` shows the crop
areas to check.

## Choosing the frame

`cover.py candidates` ranks moments with a big, steady face into a numbered sheet.
Pick with judgment: a gesture that matches the headline (pointing, surprise, hands
up at chest height — hands that end up low get lost under the crop), eyes open, no
mouth mid-syllable. The frame comes out with the file's color grade.

## Composition (defaults; colors, fonts and elements come from the brand)

- **Headline**: capitals, large, 2–3 lines, nearly edge to edge of the area that
  survives the crop, with a soft shadow. The keyword boxed in the accent color and
  larger. No subtitle: it has to read as a thumbnail. Written in the video's
  language, from the hook or the promise of the video, never a sentence copied from
  the transcript.
- **Brand** small above the headline (vertical) or top-left (YouTube): logo and/or
  name.
- **The person cut out** (rembg, `birefnet-portrait`; the first run downloads ~1 GB),
  large, leaving the canvas through its bottom edge — no visible cut at a table or
  the waist — with a light outline and a soft shadow. Vertical: centered under the
  headline. YouTube: on the right, the headline on the left.
- **Background in the brand color** with depth: a glow behind the person, darker
  edges, a faint dot pattern.
- **One giant, half-transparent element** (`--big`): the number, the keyword or an
  icon of the topic, between the headline and the head (in YouTube, under the
  headline), partly hidden by the person.
- **The face is never under text or logos**; headline and face inside the area that
  survives the crop. `make` warns when the headline is too long for it.
- **Check the cutout**: hair, hands and edges against clothes are where it fails. If
  it fails, try another frame, or `--no-cutout` (the frame goes where the text
  isn't, fading into the background).
- No brand assets: the default palette is a neutral dark background with a yellow
  accent; say so in the delivery so changing it is a concrete request.
