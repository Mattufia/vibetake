---
name: edit
description: Edit video from raw footage to a finished deliverable — ingest and analyze the material, cut on the transcript, mix voice/music/SFX, subtitles, motion graphics, thumbnails and final export. Use when the user asks to edit, cut, trim, subtitle, reframe, mix, export or create a video, reel, short or ad, or hands over footage to work on.
---

# Video editing instructions

**Tools:** ffmpeg (prepare, cut, render), yt-dlp (download material from a link),
Whisper (transcription; cuts come from it), Remotion with the `remotion-*` skills
(graphics layer only, see below), rembg (remove the background of people or
objects in stills; for people, use the `birefnet-portrait` model, and check hair
and hands).

**Don't wait for intermediate approvals.** Deliver the first complete version and
adjust afterwards on concrete requests.

**Long processes with visible progress.** Renders, transcriptions, downloads and
long encodes run showing their progress: without filtering the output (no
`| tail`), or saving it to a log that can be checked. If asked how it's going,
answer with a percentage or frames processed, plus an estimate of the remaining
time. If the tool doesn't show progress, say so before launching it.

**This assumes a terminal and the ability to see images.** Much of the method
relies on looking at stills (framing, where the face falls, verifying the
export). If images can't be viewed, say so and don't carry on as if they had
been looked at.

## 0. Setup

**Read `~/.vibetake/machine.json` before starting** (tools and their paths, GPU and
VRAM, RAM, free disk, the encoder that works, ffmpeg's filters, where transcription
runs). Missing, older than ~30 days, or a script says a tool is missing → run
`uv run ${CLAUDE_SKILL_DIR}/scripts/setup.py check`; to install or download models,
the `/vibetake:setup` command walks the user through it (`setup.py install`,
`setup.py prefetch`; without it, the first run downloads several GB: say so). Don't
assume any GPU brand. Video eats hundreds of GB: if disk
is tight, flag it before starting.

**Ask before installing anything**: what, what it's for and how big; install only
after the user's OK (a declined tool: work without it). Command-line tools are
installed **globally**: they're used across all projects and there's no point
reinstalling them each time (the transcription dependencies alone weigh several
GB). For Python tools, use an installer that keeps them isolated from each other
but available system-wide, so versions don't clash. Downloaded models live in the
user cache and are shared between projects: don't duplicate them. For system
binaries, the OS's own package manager. Don't reinstall what's already there:
check first and note in `project.md` what was installed.

The only per-project install is Remotion, because its version is pinned in each
project.

**If installing isn't possible** (no permissions, no network, locked-down work
machine), make do with what's there and say so in one line: without Remotion, graphics
are done with canvas; without yt-dlp, the user downloads the material; without a
GPU, an optimized CPU runtime. The only non-negotiable is
ingest.

## 1. Ingest: never edit blind

Before touching filmed material, however trivial the request seems. The user opens
Claude Code in the folder where their files are: first tidy it, then ingest.

```
uv run ${CLAUDE_SKILL_DIR}/scripts/organize.py      # loose footage → 00-raw/, logos/fonts/LUTs → brand/
uv run ${CLAUDE_SKILL_DIR}/scripts/ingest.py 00-raw --lang <code> --glossary "<names, brands, terms>" --out 01-ingest
```

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
   - `⚠ low confidence` check that text by ear before it goes into subtitles.
   - `S2 … (no speech)` a stretch without words: look at its sheets — action, a
     walk, a look, a dramatic pause are part of the video, not dead air.

   Cuts use the word timestamps in `transcript.json` (already trimmed to where the
   sound is). **If a name, brand or term comes out wrong, add it to the glossary
   and redo the transcript** — it's seconds on a GPU:
   `uv run ${CLAUDE_SKILL_DIR}/scripts/transcribe.py <files in order> --lang <code> --glossary "…"`.
   To recompute only the marks: `transcribe.py --rebuild-md --lang <code>`.
   Filler words per language live in `${CLAUDE_SKILL_DIR}/references/fillers.json`.

The script already handles VFR, HDR, rotation, several audio tracks, audio
recorded separately and possible log footage, and records faces every 0.5 s.
**When `material.md` shows a warning, read `${CLAUDE_SKILL_DIR}/references/ingest.md`**
for what each one means and what to do.

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

There's no fixed questionnaire. Before asking anything, two checks: **can it be
deduced with confidence** (from the request, the material, `project.md`)? And
**how expensive is it to fix if wrong?**

| | Cheap to fix | Expensive to fix |
|---|---|---|
| **Deducible** | decide, state it in the delivery | decide, state it in the delivery |
| **In doubt** | propose, apply, state it in the delivery | **ask** |

Applied:

- **Destination and format** (where it's published, orientation): the raw
  footage doesn't determine it — people shoot horizontal and publish vertical all
  the time — and getting it wrong redoes reframing, margins, pacing and duration.
  **Ask, unless the request says it.** Ask it as one question: the platform
  implies the orientation (TikTok/Reels/Shorts 9:16, YouTube 16:9).
- **Type**: ask only with low confidence; otherwise state it.
- **Subtitles**: don't ask. They follow from the destination and the material
  (section 6); state the choice in the delivery.
- **Color**: ask, as levels, with the recommended one marked from what was
  measured (section 4). It's subjective and it's the user's image. Log footage
  without its conversion LUT: also ask for the LUT.

When something has to be asked, it's **one moment**: say in one line what the
raw footage is, then ask every open question together. With a multiple-choice
question tool, use it (up to 4 questions in one go, recommended option first);
without one, a single message with the proposals.

```
It's a UGC ad (high confidence), 48 s, one person to camera. Color: mild warm cast,
skin leaning red · before/after: 02-work/color/basic/ and 02-work/color/full/
→ Where will it be published?  TikTok + Reels 9:16 (recommended) · YouTube 16:9 · Both
→ Color?  Full (recommended) · Basic · Untouched · (write: a look, your LUT, a reference)
```

- **Don't ask what the request already says.** Often there's no question at all.
- If the user says "no questions" or "your call", take the recommended options
  and flag them in the delivery.

Margins, pacing and duration follow from the answers; don't ask about them
separately.

Pacing comes from the destination axis: **is it watched in a scrolling feed, or
did the person choose to watch it?** Feed: hook in the first 2 seconds, no
intro, hard cuts, safe margins. Chosen: let it breathe, lower thirds to
introduce people, chapters if it runs past ~5 minutes. The type profile adjusts
within that frame.

## 3. Editing

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
  The face sits at ~40% from the top when there's room; the head is never cut. It
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
- **Hook (feed)**: `hook.py` shortlists lines that could open the video into
  `02-work/hooks.md`; read them in context and choose. First with `--keep
  "L9,L1-L12"` (also kept in place) or `"L9,L1-L8,L10-L12"` (moved).
- **B-roll**: own material first (ingest flags candidates). `broll.py list`
  catalogs them; describe each from its contact sheets; `broll.py place` anchors
  one to a word (`--line L5 --word material`) or centers it on a cut (`--cut 3`);
  `render.py` lays it over the edit with the speech underneath. Rules (what to
  illustrate, length, coverage, never over the hook):
  **`${CLAUDE_SKILL_DIR}/references/broll.md`**.
- Edit `edl.json` by hand for anything finer (trim a segment, move one, change a
  segment's `frame.zoom`) and re-render.
- `render.py` works from the **original files**: color from
  `02-work/color/grade.json`, framing to the output size, constant frame rate,
  12 ms audio fades so cuts never click, cuts on the source's frame grid so sync
  never drifts. It reports expected vs actual picture and sound durations.
- **Check the preview** (stills, the cuts around retakes) before the master.

- **Cleanup follows the type profile**: full in a talking head, light in a vlog,
  almost none in a testimonial, none inside an acted scene.
- **J/L cuts**: the audio comes in before or carries on after the picture. It's
  what makes the cut go unnoticed.

## 4. Color

The goal of the correction is the most natural, best-looking image; anything
stylized is the user's choice. **Always on the whole frame**: no masks, no
tracking (faces are only used to *measure* skin). It's asked, in the same moment as
the other questions, as levels:

| Option | What |
|---|---|
| **Basic** | exposure (when clearly off), white balance (strong casts fully, mild ones partly: natural isn't sterile, a warm room keeps some warmth), black and white points, **matching shots and cameras** |
| **Full** | Basic + skin toward the skin line (global and gentle, capped at a few degrees: similar colors like wood barely move), contrast and vibrance tidy-up |
| **Untouched** | the material as is (e.g. the user grades it elsewhere) |
| **Custom** (free text) | a look (`natural`, `cinematic`, `warm`, `cold`, `bw`, `vintage`, with intensity), the user's LUT, a reference image (experimental) — on top of Basic unless told otherwise |

**Recommended**: Full when skin is clearly off the line (>10°); Basic when only
exposure, balance or matching need work; Untouched when nothing needs fixing.
Before asking, run `propose`, which builds Basic and Full with before/after
previews so the user can compare, and give the folders in the question's context
line (the options themselves can only show text). "Your call" → the recommended.

```
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py propose                      # before the question
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py build --level basic|full|none # after the answer
```

- **Order: input transform → correction → look → Rec.709**, all baked into one
  .cube per file that `render.py` applies to the **original** file.
- **Look at the before/after previews before rendering**: natural skin, no
  crushed blacks or burnt whites the original didn't have, a look at a sensible
  strength.
- **Graphics and subtitles go on top after the grade**, or brand colors shift.
- **Repairs only when needed, before the correction**: `--moire` if a 100% still
  of the raw shows false color on fabrics; `--denoise light|medium` only if asked
  (grain can be wanted — offer it in the delivery instead).
- Commands, options (looks, log LUTs, the user's LUT, reference image, look-only
  .cube export) and what each measurement means:
  **`${CLAUDE_SKILL_DIR}/references/color.md`**.

## 5. Voice, music and SFX

**Clean on the raw, mix and level on the cut.**

```
uv run ${CLAUDE_SKILL_DIR}/scripts/audio.py clean                    # after ingest, once per file
uv run ${CLAUDE_SKILL_DIR}/scripts/audio.py music --file <track>     # optional bed (--gain, --start, --clear)
uv run ${CLAUDE_SKILL_DIR}/scripts/render.py --loudness social       # mixes and masters
```

- **Cleanup on the whole raw file** (it depends on how it was recorded): high-pass
  ~80 Hz, noise reduction calibrated on that file's own noise floor (the raw's
  long pauses teach it the noise), de-esser, and every file brought to the same
  speech level (−20 LUFS), so phone and camera match. The chain's latency is
  measured and compensated: lips stay in sync. `render.py` uses the cleaned audio
  automatically. Transcription stays on the original. It also keeps each file's
  room tone: a gap is filled with the room's air, never digital silence.
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
- **Start the music where it has energy** (`--start`), not on a slow intro, and
  fade it out at the end.
- **Music source**: for quick, informal videos, a bed synthesized to the exact
  length (`musicgen.py`, no licenses; it sounds synthetic up close, fine ducked
  under the voice). For scripted, brand or music-driven videos, a real track (the
  user's, or downloaded by hand from libraries without attribution). Never music
  the user can't publish. **Any third-party material** (music, effects, stock,
  fonts) under a free license, ideally without attribution (CC0, Pixabay/Pexels
  style; OFL fonts); if only an attributed one exists, say so and credit it in the
  copy. Options and licenses: **`${CLAUDE_SKILL_DIR}/references/music.md`**.

## 6. Subtitles

An editing decision, not asked: decide from the destination and the material and
state it in the delivery. Options: none · keywords as a graphic · burned-in ·
`.srt` only. Defaults: a feed (starts muted) → word-by-word burned-in + `.srt`;
chosen viewing with clean audio → `.srt` only. The type profile can override.

```
uv run ${CLAUDE_SKILL_DIR}/scripts/subtitles.py [--style highlight|sentence] [--upper] [--font "Arial Black"] [--off hook]
uv run ${CLAUDE_SKILL_DIR}/scripts/subtitles.py --no-burn     # only the .srt
```

- After the edit and the framing are final (it reads both); `render.py` burns
  `02-work/subs.ass` on top of everything.
- **`.srt` always** (`03-delivery/<name>.srt`): ≤2 lines of ≤42 characters, ~17
  characters per second, 1–7 s — platforms use it even when captions are burned in.
- **Burned-in (`highlight`)**: groups of 2–3 words, white with a black outline, the
  word being said in yellow; `sentence` for sober types. The brand's font if there
  is one (`--fontsdir` in render.py if it isn't installed).
- **Never over the face, always inside the safe zone**: low by default, below or
  above the face when it's there, just under the mouth if the face fills the frame
  (the chin can be covered, mouth and eyes never). It warns when it can't.
- **Check by ear the names, brands and numbers it lists**: that's where every model
  fails; the glossary prevents most of it, not all.

## 7. Graphics over the video

**How much graphics and sound design is set by the video**: dynamic for feed
videos (every idea gets its visual and its sound), measured for long or
informative ones, minimal for testimonials, interviews and formal pieces. The
profile names the level; the request overrides it.

```
uv run ${CLAUDE_SKILL_DIR}/scripts/graphics.py cues        # words, cuts, face, safe zone → 02-work/cues.json
uv run ${CLAUDE_SKILL_DIR}/scripts/sfx.py place --kind pop --line L5 --word ventas
uv run ${CLAUDE_SKILL_DIR}/scripts/subtitles.py --off hook  # the hook is already a graphic
```

- **Elements enter on the frame of the word that names them**, one at a time;
  keywords, not sentences; a graphic per kind of idea (numbers → counter,
  steps → flow, yes/no → ✓/✕, close → call to action).
- **Face, graphics and captions each keep their own band**, inside the safe zone.
- **Each visual event can have its sound** (whoosh on a scene change, pop on an
  entry, hit on a punchline, riser into a reveal), anchored to the same word.
- **Remotion renders only the graphics layer, with alpha**, from `cues.json`, to
  `02-work/graphics.mov`; `render.py` lays it over b-roll and under subtitles. Never
  the footage through Remotion.
- The full method (levels, idea → graphic table, hook, animation, layout, render
  command, sound levels): **`${CLAUDE_SKILL_DIR}/references/graphics.md`**.

New Remotion project:
`npx remotion add @remotion/transitions @remotion/google-fonts @remotion/lottie @remotion/three`

If there are brand assets (vector logo, typefaces, colors, LUT), use them. If
not, propose a graphic identity, apply it, and show it in the delivery on a
sample card so changing it is a concrete request. Don't hold up the work waiting
for approval.

## 8. Generating video from scratch (no filmed material)

Pure motion graphics (an animated reel, a card, a visual explainer): canvas +
ffmpeg rather than Remotion, unless it's a series or the user will edit it in
Studio. **`${CLAUDE_SKILL_DIR}/references/from-scratch.md`**.

## 9. GPU

**GPU where it helps, CPU without drama** — the scripts pick: Whisper on CUDA or
the same model on CPU int8 (never a smaller model: the transcript decides the cuts);
the master on the GPU's encoder after a short test, x264 otherwise. **Always set the
language explicitly.** Heavy GPU jobs one at a time. Details:
`${CLAUDE_SKILL_DIR}/references/gpu.md`.

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

Conversions go to `01-ingest`; `01` and `02` can be deleted to recover disk.
`project.md` holds destination, subtitle decision, glossary, what was cut and why,
the color and LUT used, the source and license of every piece of music, effect,
stock clip and font (the proof if a copyright claim comes), what was installed and
what's pending — so work resumes without asking everything again.

Say in one line what `organize.py` moved (it never overwrites; `--undo` puts it
back). Footage given by path somewhere else is read where it is, not moved.

## 11. Delivery

Always in `03-delivery`: **the video, the `.srt`** (even if subtitles are burned
in: YouTube and Meta use it for accessibility and index it), **the covers and the
copy to publish with**.

- **Video: versioned, never overwritten.** Each master is `<name>_v<N>.mp4`; every
  round of changes is a new version. `render.py --overwrite` only to fix the
  latest before it was delivered (a QA FAIL). The `.srt`, covers and copy are one
  per project and are overwritten. Several formats (9:16, 1:1, 16:9) come from the
  same edit, each with its own framing and margins checked.
- **Copy** (`copy.txt`): a section per platform, from the transcript, in the
  brand's tone — the hook line, hashtags, a YouTube title ≤60 characters, chapters
  past ~5 minutes. **`${CLAUDE_SKILL_DIR}/references/copy.md`**.
- **Upload resolution**: YouTube gives 1440p+ uploads better codecs, so a 1080p
  edit for YouTube goes out with `render.py --upload-height 1440`.

**Covers: one per platform it's published on**, composed, not extracted (a raw
frame is worse than nothing): a YouTube thumbnail 1280×720; a 1080×1920 cover for
Reels, TikTok or Shorts whose headline and face survive the profile grid's central
3:4 crop. **2–3 variants**, each with a different frame and gesture; they ship with
the video and don't hold up the delivery.

```
uv run ${CLAUDE_SKILL_DIR}/scripts/cover.py candidates                 # frames worth a cover
uv run ${CLAUDE_SKILL_DIR}/scripts/cover.py make --video <file> --time 12.4 --title "…" --keyword … --big "3×" --platform youtube --platform reels --variant 1
```

Default composition (colors, fonts, logo from the brand): headline in capitals with
the keyword boxed, the person cut out and leaving through the bottom edge, a brand
background with depth, a giant half-transparent element; the face never under text.
How to choose the frame and the rest: **`${CLAUDE_SKILL_DIR}/references/covers.md`**. The video's first frame must work alone too (autoplay starts there).

**Before delivering, always run the QA** (with the destination's platforms and
loudness):

```
uv run ${CLAUDE_SKILL_DIR}/scripts/qa.py --platform reels --platform tiktok --loudness social   # the latest version
```

It checks format (H.264 yuv420p, constant frame rate, BT.709, AAC 48 kHz,
faststart), size and duration against the edit, picture = sound, unintended black
or frozen stretches, loudness and true peak, long silences, `.srt` rules, burned-in
captions inside the safe zone, platform aspect/length/resolution, covers and copy
per platform — and it **listens**: the export is transcribed again and compared
with the edit's words, which catches a cut mid-word, a lost or doubled word, or a
leftover filler that stills can't show. Report in `03-delivery/qa/qa.md`, with the
upload reminders per platform (Instagram's "highest quality" switch, TikTok's HD
upload); exit code 1 on any FAIL. **A FAIL is fixed before delivering**; a WARN is
judged (listen where the listening check points) and mentioned if it stays.

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

**On every 9:16 video, read `${CLAUDE_SKILL_DIR}/references/safe-margins.md`
before placing any text, subtitle, graphic or logo.**
