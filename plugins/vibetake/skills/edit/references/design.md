# This video's design

Every video gets its own visual identity, decided for it — never a house style,
never a preset. Decide it yourself and show it in the delivery, where changes are
asked for. It lives in
`02-work/design.json` and everything the viewer sees follows it: the graphics layer,
the burned-in subtitles and the covers.

## 1. Read the video first

- **What it is and how it feels**: the type, the tone of what's said (a sketch, a
  confession, a tutorial, a launch), the audience, the platform.
- **What's already on screen**: `design.py palette` gives the footage's dominant
  colors and its most saturated one. A design that borrows from the image belongs to
  it; one that ignores it looks pasted on. A neutral image (white walls, grey floor)
  takes an accent that cuts through it; a colorful one takes a quiet design that
  doesn't fight.
- **The brand**, if there is one (logo, colors, typeface in the folder or the
  request): it rules over everything above.
- **The idea**: one line in `concept` that ties the look to the content ("a chat
  that never gets answered: dark bubbles, an acid-lime unread badge"). The best
  graphics borrow their language from what the video is about.

## 2. Decide (design.json)

```json
{
  "concept": "one line: why it looks like this",
  "fonts": {
    "display":  {"family": "…", "weight": 800, "file": "02-work/fonts/….ttf", "name": "<full name>"},
    "text":     {"family": "…", "weight": 500, "file": "…", "name": "…"},
    "captions": {"family": "…", "weight": 800, "file": "…", "name": "…"}
  },
  "colors":    {"ink": "#…", "paper": "#…", "accent": "#…", "muted": "#…", "…": "any role you need"},
  "type":      {"case": "upper|lower|title|sentence", "tracking": -0.02, "lineHeight": 1.0, "scale": 1},
  "surface":   {"style": "solid|gradient|glass|outline|none", "fill": "paper", "fill2": "accent", "angle": 135,
                "radius": 28, "padding": 0.55, "shadow": "none|soft|lift|glow|long", "border": "ink"},
  "highlight": {"style": "marker|box|underline|color|outline|none", "color": "accent", "text": "paper",
                "skew": -3, "pad": 0.1},
  "motion":    {"enter": "rise|drop|slide|scale|pop|blur|mask|typewriter", "stagger": 0.05,
                "stiffness": 200, "damping": 18, "distance": 0.5, "exitFrames": 7, "blur": 14}
}
```

- **Typefaces** (`fonts.py get`): one with presence for headlines, one that reads small
  on a phone for text — or one family in two weights. Choose for this video: a
  grotesque with character for humor, a condensed sans for energy, a serif for
  something editorial or premium, a rounded face for something friendly. They're
  downloaded for the project and checked for the language's characters. **Never
  system fonts** (Arial, Segoe, Helvetica, Calibri, Impact…).
- **Colors**: few, with roles. `ink` (text), `paper` (surfaces), `accent` (the one
  thing that pops), `muted` (secondary text). Text against what's behind it ≥4.5:1
  (`design.py contrast`), ≥3:1 only for very large headlines.
- **Surface**: often none — text with a highlight or a strong shadow can sit right
  on the image. When there is one, it's part of the idea, not a default box.
  `glass` is a dense translucent fill with a fine light edge (the layer is drawn over
  transparency: there's nothing behind it to blur).
- **Motion**: one personality for the whole video. Stiff and fast (stiffness 250+,
  damping 20+) feels precise; looser (stiffness 120–180, damping 10–14) feels playful
  and overshoots. `mask` reveals lines from below; `blur` feels soft and cinematic;
  `pop` and `scale` feel punchy.

## 3. What makes it look professional

- **Hierarchy**: one thing is the biggest on screen at a time. Size steps that are
  clearly different (×1.6 or more), not a ladder of similar sizes.
- **Type set with care**: tight tracking on big headlines (−0.02 to −0.04 em), tight
  line height (0.9–1.05) on headlines, never on running text. Line breaks decided by
  meaning ("no te / responde" is wrong; "tu compañero / no te responde" is right):
  the kit never re-wraps the lines you write.
- **Restraint**: one accent, one or two typefaces, one surface language, one motion
  language. The emphasis lands on one or two words per element, not five.
- **Alignment and space**: elements on a shared axis; generous inner padding; the
  same margin everywhere; everything inside the safe zone.
- **Depth that's believable**: soft, layered shadows or none. Never a hard, offset
  grey shadow.
- **Real icons**: brands' own marks (`icons.py brand`), one consistent set for
  generic icons (`icons.py ui`, one weight for the whole video), custom ones drawn
  as SVG in the same style. Never emoji or font symbols in place of an icon.

**What gives amateur work away** — check for it and don't ship it: a flat rounded box
with a hard grey drop shadow; two unrelated typefaces (or a system font); everything
centered and the same size; text over a busy area without a surface or shadow;
clipart; a colored box behind every line; animation that's slow to read or that
bounces everything at once; graphics that cover the face.

## 4. Check it on the video

Before rendering the whole layer: `graphics.py still <t> <t> …` at the moment each
element is fully in. It composites the layer over the real frame and warns when
graphics leave the safe zone or cover the face. Look at every still as a viewer:
does it read in under a second, does it belong to the image, is anything cramped or
cut. Fix and look again. In the delivery, one line on the design (`concept`, the
typefaces, the accent) so changing it is a concrete request.
