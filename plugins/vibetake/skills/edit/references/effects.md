# Effects: time, transitions, picture, sound

Segment fields in `02-work/edl.json` (`output` ones cover the whole edit). A re-cut keeps them on
the segment that still covers most of the same piece (one new segment per old one); subtitles,
graphics, b-roll, sfx, chapters and bleeps follow the new timing. Effects are for a reason (a
reveal, a beat, a gag, a time jump), not decoration; most talking-head cuts stay straight cuts.
Check the preview at each one.

## Time and transitions

| Field | Does |
|---|---|
| `"speed": 2` · `0.5` | faster / slow motion; the voice keeps its pitch between ×0.5 and ×2.5 (`"audio": "keep\|pitch\|mute"` to choose) |
| `"speed": [[0, 1], [0.8, 0.3], [1.6, 1]]` | a ramp: `[s, speed]` points over the segment's material, the speed gliding between them; muted (put a whoosh or the music there) |
| `"hold": [[1.2, 0.8]]` | freeze the picture 0.8 s at 1.2 s of the segment (at its length: the last frame) |
| `"reverse": true` · `"loop": 3` · `"boomerang": true` | backwards · played 3 times · forward then back |
| `"smooth": true` | interpolated frames for slow motion, blended ones for fast (time-lapse) — slower to render |
| `"j": 0.5` · `"l": 0.5` | this segment's sound starts 0.5 s before its picture (J) / runs 0.5 s under the next one (L) |
| `"transition": {"type": "dissolve", "dur": 0.4}` | from the previous segment, centered on the cut (the timeline doesn't move); any ffmpeg xfade name: `fadeblack`, `fadewhite`, `wipeleft`, `slideup`, `circleopen`, `radial`, `pixelize`, `smoothleft`… Plus `whip` (`whip-left\|right\|up\|down`): the next shot slides in with motion blur along the move; `push` (`push-left`…): the same slide, no blur; `zoom`. A whip or push wants a whoosh on the cut (`sfx.py place --kind whoosh --cut N`) |
| `"fade_in"` / `"fade_out": {"dur": 0.6, "color": "black"}` | from/to a color, picture and sound |

J/L as played: never on the first segment's J or the last one's L, never on a cut with a
transition (the transition wins), and each segment keeps at least 0.1 s of its own sound.
Captions and bleeps follow the sound that is really heard.

## Picture

| Field | Does |
|---|---|
| `"stabilize": true` · `0.5` | steadies a shaky handheld shot (vid.stab, two passes; a slight zoom hides the edges); 0–1 = how smooth |
| `"fit": "blur"` · `"black"` | the whole picture inside the frame instead of cropped (a horizontal shot in a vertical edit): over a blurred copy of itself, or bars of a color; wins over reframe.py's framing. `output.fit` for every segment |
| `"redact": [...]` | blurred/pixelated boxes that follow what they hide: made by `redact.py` (below), don't write keys by hand |
| `"key": {"color": "green", "bg": "02-work/bg.jpg"}` | green/blue screen out, over a picture, a video (looped) or a color; `similarity` (0.15) / `blend` (0.05) if edges show |
| `"flip": "h\|v\|both"` · `"rotate": 90\|180\|270` · `"rotate": -3` | mirror · turn (a clip recorded sideways; 90/270 use a center crop) · tilt a few degrees (zoomed so no corner shows) |
| `"pip": {"source": "<file>", "in": 3.0, "corner": "top-right", "scale": 0.32}` | another clip in a corner over this segment (a reaction, the screen being talked about), muted; in a vertical video the corners sit inside the safe band |
| `"split": {"source": "<file>", "in": 3.0, "side": "left\|right\|top\|bottom"}` | the screen in two: the other clip on that side, this one's center on the other |
| `"behind": true` | the graphics layer goes behind the person in this segment (a big title they stand in front of): `references/graphics.md` |
| `output.look: {"vignette": 0.4, "grain": 0.3, "bw": true}` | over the whole edit, b-roll included (0–1 each) |

### Blur a face, a plate, a screen

`uv run ${CLAUDE_SKILL_DIR}/scripts/redact.py <seg> --show
[--at t]` (`<seg>` counted from 1, as listed in edl.json) gives a frame with a 0–1 grid; read the box off it, then `redact.py <seg>
--box x,y,w,h [--at t] [--mode pixelate] [--static]` tracks it over the segment and
writes a contact sheet with the box drawn: look at it before rendering, and redo
from the moment it lost it (another `--box --at`). `--clear` removes them.

A tracked box covers its whole segment (past the tracked stretch it stays at the edge position);
when a re-cut makes the segment longer, cutlist.py warns: look at it again.

## Sound

- **A voice effect on a segment**: `"voice": "phone|radio|megaphone|echo|hall|robot|deep|high|whisper"`
  (a call, a flashback, a gag, a disguised voice), or any ffmpeg audio filter chain.
- **Censor words**: `edl.json` `"bleep": {"words": ["puta*", "boludo"], "sound": "beep|mute"}`
  (accents and case ignored, ñ kept: `ano` doesn't catch `año`; `*` = any ending). render.py beeps or silences each
  time they're said (from the transcript, so it follows re-cuts), subtitles star them.
  Check by ear: a word the transcript missed isn't bleeped.
