---
name: edit
description: Edit video from raw footage to a finished deliverable — ingest and analyze the material, cut on the transcript, mix voice/music/SFX, subtitles, motion graphics, thumbnails and final export. Use when the user asks to edit, cut, trim, subtitle, reframe, mix, export or create a video, reel, short or ad, or hands over footage to work on.
---

# Video editing instructions

**Every video you deliver is the creator's showreel — and yours.** Work the way the
best in each craft would if their name were on it: sweat the details nobody asked
for; check, fix what isn't right, check again — until it's genuinely good, not just
done. Go all out on quality, with the masters' restraint: great work serves the
video, never shows off over it.

**Tools:** ffmpeg (prepare, cut, render), yt-dlp (download material from a link),
Whisper (transcription; cuts come from it), Remotion with the `remotion-*` skills
(graphics layer only, see below), rembg (cut-outs for covers, see `covers.md`).

**Don't wait for intermediate approvals.** Deliver the first complete version and
adjust afterwards on concrete requests.

**Long processes with visible progress** (renders, transcriptions, downloads): never
filter the output (no `| tail`) or save it to a log you can check; asked how it's
going, answer with a percentage and the time left. No progress shown → say so first.

**This assumes you can see images** (stills, sheets): if you can't, say so and never
carry on as if you had looked.

## 0. Setup

**Read `~/.vibetake/machine.json` before starting** (tools and their paths, GPU and
VRAM, RAM, free disk, the encoder that works, ffmpeg's filters, where transcription
runs). Missing, older than ~30 days, or a script says a tool is missing → run
`uv run ${CLAUDE_SKILL_DIR}/scripts/setup.py check`; to install or download models,
the `/vibetake:setup` command walks the user through it (`setup.py install`,
`setup.py prefetch`; without it, the first run downloads several GB: say so). Don't
assume any GPU brand. Video eats hundreds of GB: if disk
is tight, flag it before starting.

**Ask before installing anything** (what, what for, how big; declined → work without
it). Tools install **globally** and once (isolated Python tools, the OS package
manager for binaries), models live in the shared user cache; check before installing
and note it in `project.md`. Only Remotion installs per project (pinned version).

**If installing isn't possible** (no permissions, no network, locked-down work
machine), make do with what's there and say so in one line: without Remotion, graphics
are done with canvas; without yt-dlp, the user downloads the material; without a
GPU, an optimized CPU runtime. The only non-negotiable is
ingest.

## 1. Ingest: never edit blind

Before touching filmed material, however trivial the request seems. The user opens
Claude Code in the folder where their files are: tidy it (it also creates `brand/`,
`references/`, `music/` for the user's own material), then **start ingest at once,
in the background**. As soon as its log says **analysis ready** (`material.md` and the
sheets; transcription keeps running), classify, run `grade.py propose` (and `match.py
solve` with several files) and ask the questions (section 2) — once, all together —
while the transcript finishes. After the answer, run both again: new files are picked up.

```
uv run ${CLAUDE_SKILL_DIR}/scripts/organize.py      # loose files → 00-raw/, brand/, references/, music/
uv run ${CLAUDE_SKILL_DIR}/scripts/ingest.py 00-raw --lang <code> --glossary "<names, brands, terms>" --out 01-ingest
uv run ${CLAUDE_SKILL_DIR}/scripts/organize.py --move references <files>   # where the user said they go
```

Only what's clear moves. Photos that could be material or a reference are left where
they are, listed as **unsure**, and used for nothing: they go in the one question
(section 2), and the answer moves them (`--move`). Never guess them into a folder.

**Always pass the language explicitly.** Build the **glossary** from the request
and what's known about the project (people, brands, products, jargon): it's what
gets proper names spelled right. The script orders the files by recording time,
launches transcription on the GPU in parallel with everything else, shows
progress, and skips what's already done if run again (`--force` redoes it). Then:

1. **Read `01-ingest/material.md`**: technical data, warnings and the
   classification signals for each file.
2. **Look at the contact sheets** in `01-ingest/contact/` (4×4, timecode on each
   frame; `*_grid_*` at regular intervals, `*_shots_*` one per shot change).
   Actually look: framing, where the face falls, zooms, blown or out-of-focus
   takes. For one frame in detail:
   `uv run ${CLAUDE_SKILL_DIR}/scripts/still.py <file> <time>`.
3. **Read `01-ingest/transcript.md`** to decide. It's the map of the video: one
   numbered sentence per line with timecodes and pauses, across all files in
   recording order. It's verbatim, so fillers are there to be cut. Marks are
   suggestions to judge with context, not orders:
   - `⟨…⟩` filler candidate ("Bueno," at the start is a filler; "a good video"
     isn't). A reaction — "¡Ah!", an "Ah" answering a question — isn't marked.
   - `↻ retake → L12` this line is an abandoned or repeated take of line 12,
     possibly across files. Keep the later take unless it's clearly worse.
     `(L12 softer)` / `(this take shakier)`: the two takes' picture compared with each
     other — a hint, the performance still wins; a kept flagged take goes in the delivery.
   - `⚠ low confidence` check that text by ear before it goes into subtitles.
   - `S2 … (no speech)` a stretch without words: look at its sheets — action, a
     walk, a look, a dramatic pause are part of the video, not dead air.

   Cuts use the word timestamps in `transcript.json` (already trimmed to where the
   sound is). **If a name, brand or term comes out wrong, add it to the glossary
   and redo the transcript** — it's seconds on a GPU:
   `uv run ${CLAUDE_SKILL_DIR}/scripts/transcribe.py <files in order> --lang <code> --glossary "…"`.
   To recompute only the marks: `transcribe.py --rebuild-md --lang <code>`.
   Filler words per language live in `${CLAUDE_SKILL_DIR}/references/fillers.json`.

It handles VFR, HDR, rotation, several audio tracks, separate audio and log footage;
**a warning in `material.md` → read `${CLAUDE_SKILL_DIR}/references/ingest.md`**.

**If the scripts can't run** (no uv, no Python), do the same by hand with
ffprobe/ffmpeg and say so: probe, VFR/HDR checks, scene-change stills plus a
regular grid, `silencedetect` and `ebur128`, word-level transcription.

## 2. What the video is, and what to ask

Two independent axes define the edit. Don't mix them:

- **Type: what the video is.** Sets structure, cleanup, music, graphics and extra
  deliverables.
- **Destination: how it's consumed.** Sets pacing, margins and duration.

The same podcast can go whole to YouTube or in clips to TikTok: same type,
different destination.

### Classify the type

Decide with judgment, weighing the evidence; there's no scoring formula. In order
of weight:

1. **What the request says.** "Edit this ad" settles it; nothing to detect.
2. **Ingest signals**: how many voices, how much of the time someone speaks,
   whether there's a face and how big, shot changes per minute, music in the raw
   footage, screens or UI in the stills, vocabulary ("link in bio", prices,
   "step 1", "today I'm…").
3. **Context**: duration, orientation, file names.

Types: `ad`, `talking-head`, `tutorial`, `vlog`, `podcast-interview`,
`testimonial`, `music-montage`, `corporate`, `narrative` (acted scenes). **What each covers and its typical
signals: `${CLAUDE_SKILL_DIR}/references/types.md`** — read it to classify.

The result is one line: **type + confidence + alternative**. If nothing fits, use
the closest type and say which. Mixed material gets a **main type plus segments
with their own treatment** (a sponsor segment in a vlog is treated as `ad`;
clips cut from a podcast for a feed are treated as `talking-head`).

Once classified, **read `${CLAUDE_SKILL_DIR}/profiles/<type>.md`** and apply its
defaults. The profile sets defaults; the request and the material can override
them.

### Questions: only real doubts, all at once

Ask only what can't be deduced with confidence **and** is expensive to fix if wrong:
usually the **destination** (unless the request says it) and the **color level**
(section 4); the type only with low confidence; subtitles never. **One moment**, every
open question together with **whether all the material is in** (`brand/`,
`references/`, `music/`) and where the **unsure** files go, in the user's language. Brand assets are never searched for
or recreated. Nothing to ask → don't stop; "your call" → the recommended options,
flagged in the delivery. How, wording, example: **`${CLAUDE_SKILL_DIR}/references/questions.md`**.

Margins, pacing and duration follow from the answers; don't ask about them
separately.

Pacing comes from the destination axis: **is it watched in a scrolling feed, or
did the person choose to watch it?** Feed: hook in the first 2 seconds, no
intro, hard cuts, safe margins. Chosen: let it breathe, lower thirds to
introduce people, chapters if it runs past ~5 minutes. The type profile adjusts
within that frame.

## 3. Editing

**Cut this like the editor everyone wants on their project** — the one whose edits
feel effortless. The strongest opening this type allows, every cut motivated and
invisible, tight where it drags and room to breathe where it lands, a rhythm that
carries the viewer to the end. Frame like a seasoned camera operator: the subject
where the eye expects them, moving only when it means something. Every insert shows
what's being said, or what words can't. Go all out on pacing and story: cut what
doesn't earn its place, protect what makes it human.

**The edit lives in `02-work/edl.json`**: which piece of which file, in what
order, and why. Everything renders from it, so a change ("drop the part about
X") is an edit to that list and a new render, never a redo.

```
uv run ${CLAUDE_SKILL_DIR}/scripts/cutlist.py --keep auto --format vertical   # or --keep "L9,L2-L7"
uv run ${CLAUDE_SKILL_DIR}/scripts/reframe.py                                # framing + punch-ins
uv run ${CLAUDE_SKILL_DIR}/scripts/render.py --preview                       # 540p, seconds
uv run ${CLAUDE_SKILL_DIR}/scripts/render.py                                 # master → 03-delivery/
```

- **Choose lines, not timestamps.** Decide on `transcript.md` which lines stay
  and in what order (`--keep auto` = everything except marked retakes; an
  explicit list can reorder, e.g. a cold open first). `cutlist.py` turns that into
  word-accurate cuts: dropped fillers (`--fillers drop|keep`, per the profile),
  long pauses inside a phrase shortened, and **a pause at every join that sounds
  like speech**: ~0.4 s after a sentence, ~0.25 s after a comma, ~0.15 s where a
  filler came out mid-phrase. That air is taken from clean silence (mostly after
  the previous phrase), never from the tail of a word that was dropped.
  `--pace tight|normal|relaxed` scales it all (feed/ad: tight · testimonial:
  relaxed); **`natural`** keeps pauses and silent stretches as recorded (acted
  scenes). `--keep` takes `S2` like a line. It reports every silent stretch it
  shortened and every filler it took out: check them, and say them in the delivery.
- **What the user asks to keep is protected**: `cutlist.py --protect S1|L3|0:03-0:07
  --why "…"` → every rebuild keeps it whole, over any automatic rule. A rebuild
  reuses the last run's settings. Note each protection in `project.md`.
- **Framing (`reframe.py`): the simplest one that works, per segment.** Same
  aspect as the output → untouched. The person barely moves (or a gimbal or
  tracking camera already kept them centered) → **one fixed crop** for the
  segment. They move too much for one position → **camera operator, not a
  tracker**: holds still while the face stays in a dead zone, pans with eased
  acceleration and a speed limit when it leaves it, cuts straight to the new
  framing if the face would leave the crop. `--mode fixed|operator` forces one.
  The face sits at ~40% from the top when there's room; the head is never cut.
  **Several people on screen**: the group when they all fit; otherwise who speaks,
  sentence by sentence — from a mic per person when there is one (it learns whose is
  whose), else from mouth movement; unsure → it stays. Check `02-work/speakers.md` and
  `people_<file>.jpg` (who is person 1, 2…); fix a line with `speaker.py --set L12=2`. It
  warns when a crop is smaller than the output (1080p horizontal cut to vertical
  is stretched ~1.8×, softer): say so, or once the segment is final recover detail
  with AI — `upscale.py run --seg N --test 2` first (time, comparison), never a
  plain resize to "gain" resolution.
- **Zoom at every cut**: at jump cuts the base zoom alternates between 1.0 and
  `--zoom` (1.12; half for a face that already fills the frame), so the jump reads
  as a closer shot; each segment after a cut enters with a short impact zoom
  (`--bump`, +6%, settles in ~0.15 s) and pushes in slowly while it lasts
  (`--drift`, +3%). `--punch off` disables all of it (testimonial, corporate,
  screen recordings); `--bump 0 --drift 0` keeps only the alternation.
- **Hook (feed)**: pick the line that works on its own and opens a loop (1.5–4 s) — a
  promise, a conflict, a surprise — reading `transcript.md` in context. First with
  `--keep "L9,L1-L12"` (also kept in place) or `"L9,L1-L8,L10-L12"` (moved).
- **B-roll**: own material first; `broll.py list` catalogs it, `place` anchors a
  clip to a word or a cut, `render.py` lays it over the speech. Commands and rules
  (what to illustrate, length, coverage, never over the hook): **`${CLAUDE_SKILL_DIR}/references/broll.md`**.
- Edit `edl.json` by hand for anything finer (trim a segment, move one, change a
  segment's `frame.zoom`) and re-render.
- **Time and transitions are fields of a segment** in `edl.json` (a re-cut keeps them
  on the segment that still covers that piece; subtitles, graphics, b-roll and sfx
  follow the new timing):

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

  Effects are for a reason (a reveal, a beat, a gag, a time jump), not decoration;
  most talking-head cuts stay straight cuts. Check the preview at each one.
- **The picture, too** (segment fields; `output` ones cover the whole edit):

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

  Blur a face, a plate, a screen: `uv run ${CLAUDE_SKILL_DIR}/scripts/redact.py <seg> --show
  [--at t]` gives a frame with a 0–1 grid; read the box off it, then `redact.py <seg>
  --box x,y,w,h [--at t] [--mode pixelate] [--static]` tracks it over the segment and
  writes a contact sheet with the box drawn: look at it before rendering, and redo
  from the moment it lost it (another `--box --at`). `--clear` removes them.
- **Nothing here for what's asked? Build it** with ffmpeg (any filter, on a segment
  or the master) or a Remotion component, and say how it was done — never answer
  that it can't be done when the tools can do it.
- `render.py` works from the **original files**: color from
  `02-work/color/grade.json`, framing to the output size, constant frame rate,
  12 ms audio fades so cuts never click, cuts on the source's frame grid so sync
  never drifts. It reports expected vs actual picture and sound durations.
- **After any re-cut**, run `reframe.py`, then subtitles and graphics again (b-roll, effects
  and music stay: they follow their words). `render.py` refuses a master with captions or
  graphics made for another cut.
- **Check the preview** (stills, the cuts around retakes) before the master.

- **Cleanup follows the type profile**: full in a talking head, light in a vlog,
  almost none in a testimonial, none inside an acted scene.
- **Sound across a cut**: b-roll keeps the speech running underneath, which hides the
  cut; place it there before reaching for anything else.

## 4. Color

**Grade like a feature-film colorist putting this in their reel**: shots of the same
place and light look like one film while each place keeps its own light, skin that
looks alive when there's a person, detail in the blacks and the highlights — natural
unless a look is asked for. Go all out on consistency and finesse, never on
saturation or effects: great grading is felt, not noticed.

The goal of the correction is the most natural, best-looking image; anything
stylized is the user's choice. **Always on the whole frame**: no masks, no
tracking (faces are only used to *measure* skin). It's asked, in the same moment as
the other questions, as levels:

| Option | What |
|---|---|
| **Basic** | shots matched + exposure (when clearly off), white balance (the light's warmth or coolness stays — it's the place's mood; green/magenta tints go), black and white points |
| **Full** | Basic + skin toward the skin line (global and gentle, capped at a few degrees: similar colors like wood barely move), contrast and vibrance tidy-up |
| **Only match shots** | the shots made alike, nothing else (one file: the material as is) — e.g. the user grades it elsewhere |
| **Custom** (free text) | a look (`natural`, `cinematic`, `warm`, `cold`, `bw`, `vintage`, with intensity), the user's LUT; a reference image: look at it and pick the closest look — on top of Basic unless told otherwise |

**Recommended**: Full when skin is clearly off the line (>10°); Basic when only
exposure, balance or matching need work; Only match shots (one file: untouched) when nothing else needs fixing.
Before asking, run `propose`, which builds Basic and Full with before/after
previews so the user can compare, and give the folders in the question's context
line (the options themselves can only show text). "Your call" → the recommended.

```
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py propose                      # before the question
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py build --level basic|full|none # after the answer
```

- **Order: input transform → correction → look → Rec.709**, all baked into one
  .cube per file that `render.py` applies to the **original** file.
- **Several files: match by looking** (`match.py`, before the question): `solve`, look
  at its `sheet.jpg`, **group the shots that share place and light** (different places
  stay different), `skip` what isn't camera footage, point at what repeats if skin isn't
  enough, `set` to nudge; one correction per group follows. Steps and rules in `color.md`.
- **Ambient** (`--ambient`, default balanced: keeps the light's warmth or coolness,
  removes green/magenta tints); the user can ask neutral or keep for this video.
- **Look at the before/after previews before rendering**: natural skin, no
  crushed blacks or burnt whites the original didn't have, a look at a sensible
  strength.
- **Graphics and subtitles go on top after the grade**, or brand colors shift.
- **Repairs only when needed, before the correction**: `--moire` if a 100% still
  of the raw shows false color on fabrics; `--denoise light|medium` only if asked
  (grain can be wanted — offer it in the delivery instead).
- Commands, options (looks, log LUTs, the user's LUT) and what each measurement means:
  **`${CLAUDE_SKILL_DIR}/references/color.md`**.

## 5. Voice, music and SFX

**Mix like a top sound engineer**: the voice, when there is one, clear, warm and
present on a phone speaker and on headphones; when there's music, it lifts the moment
and steps aside for every word; effects make cuts and graphics feel physical — and
never a click, a level jump or a lost word. Viewers forgive a bad picture before bad
sound.

**Clean on the raw, mix and level on the cut.**

```
uv run ${CLAUDE_SKILL_DIR}/scripts/audio.py clean                    # after ingest, once per file
uv run ${CLAUDE_SKILL_DIR}/scripts/audio.py music --file <track>     # optional bed (--in, --start, --gain, --clear)
uv run ${CLAUDE_SKILL_DIR}/scripts/render.py --loudness social       # mixes and masters
```

- **Cleanup on the whole raw file** (it depends on how it was recorded): the best
  source (a separate recording, the right track, the voice centered), high-pass, noise
  reduction calibrated on that file's own noise floor (`--nr light` for spontaneous
  material), de-esser, and every file brought to the same **speech** level (−20 LUFS,
  measured where someone talks), so phone and camera match. A file that's mostly
  ambience (the sea, a street) keeps its sound as recorded. Latency compensated: lips
  stay in sync; `render.py` uses it automatically; each file's room tone fills gaps.
- **On the cut** (it depends on the final edit): gentle compression across the
  whole voice (consistent level across cuts); music leveled to a bed and **ducked
  under the voice** (~14–18 dB down while someone talks, back up in the pauses —
  a fixed level sounds like a template); then an audio-only second pass that
  measures the result and sets the destination's loudness, with a 4× oversampled
  limiter only if true peak needs it:

  | `--loudness` | Integrated | True peak |
  |---|---|---|
  | `social` (TikTok, Reels, Shorts, YouTube) | −14 LUFS | −1 dBTP |
  | `podcast` | −16 LUFS | −1 dBTP |
  | `broadcast-eu` / `broadcast-us` (TV ads: confirm with the station) | −23 / −24 | −2 dBTP |
- **The voice rules.** Music never competes with it (a gentle dip around 3 kHz
  leaves room for speech); sound effects (`sfx.py`, section 7) sit ~10 dB below it.
- **Start the music where it has energy** (`--in` <seconds into the track>), not on a
  slow intro, and fade it out at the end. `--start` is where it enters the edit.
- **Music source**: a bed synthesized to the length (`musicgen.py`) for quick,
  informal videos; a real track for scripted, brand or music-driven ones. **Never
  anything the user can't publish**; third-party material (music, effects, stock,
  fonts) free-licensed, source and license in `project.md`. Where from, licenses:
  **`${CLAUDE_SKILL_DIR}/references/music.md`**. Cutting on the beat:
  `audio.py beats --file <track>` (tempo, beats, bars; same reference).
- **A voice effect on a segment**: `"voice": "phone|radio|megaphone|echo|hall|robot|deep|high|whisper"`
  (a call, a flashback, a gag, a disguised voice), or any ffmpeg audio filter chain.
- **Censor words**: `edl.json` `"bleep": {"words": ["puta*", "boludo"], "sound": "beep|mute"}`
  (accents and case ignored; `*` = any ending). render.py beeps or silences each
  time they're said (from the transcript, so it follows re-cuts), subtitles star them.
  Check by ear: a word the transcript missed isn't bleeped.

## 6. Subtitles

An editing decision, not asked: decide from the destination and the material and
state it in the delivery. Options: none · keywords as a graphic · burned-in ·
`.srt` only. Defaults: a feed (starts muted) → word-by-word burned-in + `.srt`;
chosen viewing with clean audio → `.srt` only. The type profile can override.

```
uv run ${CLAUDE_SKILL_DIR}/scripts/subtitles.py [--style highlight|sentence] [--upper] [--off hook]
uv run ${CLAUDE_SKILL_DIR}/scripts/subtitles.py --no-burn     # only the .srt
uv run ${CLAUDE_SKILL_DIR}/scripts/subtitles.py --engine ass  # burned by render.py, no graphics layer
```

- **Engine** (`auto` by default): `remotion` writes `02-work/captions.json` (same groups, timing, face and safe-zone
  placement) and the graphics layer draws them: animated (`pop`, `karaoke`,
  `bounce`, `none`), and position, size, colors, box and case editable in Remotion
  Studio (Graphics → `captionStyle`); then `graphics.py init|render` after it, even
  with no graphics plan (references/graphics.md). `ass` writes `02-work/subs.ass`, burned by
  `render.py` on top of everything: fast, no Node. `auto` picks `ass` only for a video over
  3 min with no graphics plan (or without Node), `remotion` otherwise.
- After the edit and the framing are final (it reads both).
- **`.srt` always** (`03-delivery/<name>.srt`): ≤2 lines of ≤42 characters, ~17
  characters per second, 1–7 s (Latin; Chinese, Japanese, Korean, Thai and right-to-left
  scripts use their own limits, from the language) — platforms use it even when burned in.
- **Burned-in (`highlight`)**: groups of 2–3 words, white with a black outline, the
  word being said in the design's accent (a dark accent gets a light outline, same
  thickness); `sentence` for sober types. Change them with the script's options,
  never by editing `subs.ass` by hand. In the
  project's caption font (design.json / fonts.py) — never a system font.
- **Never over the face, always inside the safe zone**: low by default, below or
  above the face when it's there, just under the mouth if the face fills the frame
  (the chin can be covered, mouth and eyes never). It warns when it can't.
- **Check by ear the names, brands and numbers it lists**: that's where every model
  fails; the glossary prevents most of it, not all.

## 7. Graphics over the video

**Treat everything you put on screen as your motion-design showreel**: purposeful
timing, beautiful easing, typography with intent, every frame polished. Captions read
at a glance, timed to the frame, broken where the meaning breaks, adding energy
without covering what matters. Go all out on craft; the amount follows this type's
level (dynamic / measured / minimal), because a great designer also knows when the
best graphic is none.

**How much graphics and sound design is set by the video**: usually dynamic for
feed videos, measured for long or informative ones, minimal for testimonials,
interviews and formal pieces. The profile suggests it; the request and the design
override it.

**Every video gets its own design, decided on your own** (never a house style, never
system fonts). Start with the grammar — graphics over a full-frame image, or the
footage placed inside a composed frame (a `stage`), type-led, quiet — and only then
typefaces, colors and icons. Palettes change the look; the grammar is what keeps
videos from all looking alike. One line on it in the delivery; changes come from there.

```
uv run ${CLAUDE_SKILL_DIR}/scripts/refs.py                      # the user's references, to look at first
uv run ${CLAUDE_SKILL_DIR}/scripts/design.py palette            # the footage's colors
uv run ${CLAUDE_SKILL_DIR}/scripts/fonts.py get "<Family>" --weights 500,800   # checks the video's characters
uv run ${CLAUDE_SKILL_DIR}/scripts/icons.py brand whatsapp      # or: icons.py ui check --set phosphor
uv run ${CLAUDE_SKILL_DIR}/scripts/graphics.py cues             # → design.json + graphics.json, then:
uv run ${CLAUDE_SKILL_DIR}/scripts/graphics.py init | still 1.2 4.5 | render
uv run ${CLAUDE_SKILL_DIR}/scripts/sfx.py place --kind pop --line L5 --word ventas
```

- **Design** (`02-work/design.json`, also used by subtitles and covers):
  **`${CLAUDE_SKILL_DIR}/references/design.md`**. The user's `references/` come first
  (`refs.py`, then look at each): criteria for the whole video, never copied, never shown.
- **Elements enter on the frame of the word that names them**, each with its sound
  on the same word; face, graphics and captions keep their own bands. Remotion renders
  only the graphics layer (alpha, or opaque where a `stage` composes the footage
  itself); `still` checks frames over the real image first.
  Scene plan, idea → graphic, sound: **`${CLAUDE_SKILL_DIR}/references/graphics.md`**.
  A map, 3D or another effect a Remotion skill covers: load that skill first (`remotion-maps`,
  `remotion-markup`), as `graphics.md` says.

## 8. Generating video from scratch (no filmed material)

Pure motion graphics (an animated reel, a card, a visual explainer): canvas +
ffmpeg rather than Remotion, unless it's a series or the user will edit it in
Studio. **`${CLAUDE_SKILL_DIR}/references/from-scratch.md`**.

## 9. GPU

**The scripts pick GPU or CPU** (never a smaller transcription model: the transcript
decides the cuts); heavy GPU jobs one at a time. `${CLAUDE_SKILL_DIR}/references/gpu.md`.

## 10. Project structure

```
project/
  00-raw/          original material (organize.py moves it here). Never edited, renamed or overwritten.
  brand/           logo, fonts, colors, LUTs, if the user has them.
  01-ingest/       transcript, stills, material data. Regenerable.
  02-work/         cuts, intermediates, graphics layer. Disposable.
  03-delivery/     video versions, .srt, covers, copy.
  project.md       what was decided and why.
```

`01` and `02` can be deleted to recover disk. `project.md` holds the decisions
(destination, subtitles, glossary, cuts, color, ambient) and the source and license
of every track, effect, clip and font — so work resumes without asking again.

Say in one line what `organize.py` moved (it never overwrites; `--undo` puts it
back). Footage given by path somewhere else is read where it is, not moved.

## 11. Delivery

**What the audience sees first is yours too.** Thumbnails like the best on the
platform: one idea readable in half a second at the size of a fingernail, a face with
real emotion when there's a person, a subject that pops when there isn't — three
concepts genuinely different. Titles and copy specific, curious, true to the video,
in the creator's or brand's voice. Never promise what the video doesn't deliver.
Before delivering, watch it as the toughest viewer and as the creator at once: would
you sign this? If anything is merely okay, fix it.

Always in `03-delivery`: **the video, the `.srt`** (even if subtitles are burned
in: YouTube and Meta use it for accessibility and index it), **the covers and the
copy to publish with**.

- **Video: versioned, never overwritten.** Each master is `<name>_v<N>.mp4`; every
  round of changes is a new version. `render.py --overwrite` only to fix the
  latest before it was delivered (a QA FAIL). The `.srt`, covers and copy are one
  per project and are overwritten. Other formats and alternative hooks are
  variants of the same version: **the same `--variant <v>` on every step** —
  `cutlist.py`, `reframe.py`, `subtitles.py`, `graphics.py cues|render`, `render.py`,
  `qa.py` — so each has its own cut, framing, captions (and `.srt`) and graphics
  layer, never the main video's; a line in the delivery saying what differs.
- **Copy** (`copy.txt`): a section per platform, from the transcript, in the
  brand's tone — the hook line, hashtags, a YouTube title ≤60 characters, chapters
  past ~5 minutes (`deliver.py chapter`: render.py writes them into the MP4 and
  `<video>.chapters.txt`). **`${CLAUDE_SKILL_DIR}/references/copy.md`**.
- **A GIF** when asked (a reaction, a preview for a README or a chat):
  `uv run ${CLAUDE_SKILL_DIR}/scripts/deliver.py gif --from 12.5 --to 16 [--width 480]`
  — a stretch of the latest version, looping; keep it under ~10 s.

**Covers: three different concepts, one file per platform**, each a free canvas
designed for this video (not one layout with three texts), the person cut out by
default. Sizes, commands, checks: **`${CLAUDE_SKILL_DIR}/references/covers.md`** —
read it before making them. The video's first frame must work alone too.

**Before delivering, always run the QA** (with the destination's platforms and
loudness):

```
uv run ${CLAUDE_SKILL_DIR}/scripts/qa.py --platform reels --platform tiktok --loudness social   # the latest version
```

It checks format, duration, picture = sound, black or frozen stretches, loudness,
`.srt` rules, captions in the safe zone, platform limits, covers and copy — and it
**listens**: the export is transcribed again and compared with the edit's words (a cut
mid-word, a lost or doubled word, a leftover filler). Report and upload reminders in
`03-delivery/qa/qa.md`. **A FAIL is fixed before delivering**; a WARN is judged (listen
where it points) and mentioned if it stays.

- **Look at the QA contact sheets** (`03-delivery/qa/`, the vertical safe zone in
  green): framing, readable captions, nothing cut off, the first frame works alone.
- Platform limits and export settings: `${CLAUDE_SKILL_DIR}/references/platforms.md`
  (they change: verify when a limit matters).
- In the delivery, say in one line what was left out, what was resolved on your
  own judgment, what the master was encoded with, and any WARN that stays.
- **Then `qa.py --cleanup`** deletes the heavy disposable intermediates (preview,
  render chunks, normalized copies) — only if nothing failed. Cleaned audio and
  grades stay: re-renders need them. No need to mention it.

## 12. Safe margins

**9:16: read `${CLAUDE_SKILL_DIR}/references/safe-margins.md` before placing any text.**
