---
name: edit
description: Edit video from raw footage to a finished deliverable — ingest and analyze the material, cut on the transcript, mix voice/music/SFX, subtitles, motion graphics, thumbnails and final export. Use when the user asks to edit, cut, trim, subtitle, reframe, mix, export or create a video, reel, short or ad, or hands over footage to work on.
---

# Video editing instructions

**Tools:** ffmpeg and yt-dlp (prepare and download material), Whisper
(transcription), auto-editor (silences), Remotion with the `remotion-*` skills
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

**Check what's available before starting**: which tools are installed, which GPU
and from which vendor, how much VRAM, cores, RAM, disk space, and which encoders
ffmpeg was compiled with (some builds lack x264). That determines model size,
concurrency and the render path. Don't assume any GPU brand: there is
acceleration on NVIDIA, AMD, Intel and Apple, through different routes. Video
eats hundreds of GB: if disk is tight, flag it before starting.

**Install what's missing, saying what gets installed.** Command-line tools are
installed **globally**: they're used across all projects and there's no point
reinstalling them each time (the transcription dependencies alone weigh several
GB). For Python tools, use an installer that keeps them isolated from each other
but available system-wide, so versions don't clash. Downloaded models live in the
user cache and are shared between projects: don't duplicate them. For system
binaries, the OS's own package manager. Don't reinstall what's already there:
check first and note in `project.md` what was installed.

The only per-project install is Remotion, because its version is pinned in each
project.

**Known trap**: installing PyTorch plainly usually brings the CPU-only variant,
and then the GPU "isn't detected" without any visible error. If there's a GPU,
install the variant for that platform and verify it's detected **before**
transcribing.

**The first run on a new machine downloads models and a headless browser**
(several GB). Say so instead of leaving the terminal hanging without explanation.

**If installing isn't possible** (no permissions, no network, locked-down work
machine), make do with what's there and say so in one line: without auto-editor,
silences come from the transcript; without Remotion, graphics are done with
canvas; without a GPU, an optimized CPU runtime. The only non-negotiable is
ingest.

## 1. Ingest: never edit blind

Before touching filmed material, however trivial the request seems. Run, from the
project folder:

```
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
     isn't).
   - `↻ retake → L12` this line is an abandoned or repeated take of line 12,
     possibly across files. Keep the later take unless it's clearly worse.
   - `⚠ low confidence` check that text by ear before it goes into subtitles.

   Cuts use the word timestamps in `transcript.json` (already trimmed to where the
   sound is). **If a name, brand or term comes out wrong, add it to the glossary
   and redo the transcript** — it's seconds on a GPU:
   `uv run ${CLAUDE_SKILL_DIR}/scripts/transcribe.py <files in order> --lang <code> --glossary "…"`.
   To recompute only the marks: `transcribe.py --rebuild-md --lang <code>`.
   Filler words per language live in `${CLAUDE_SKILL_DIR}/references/fillers.json`.

What the script already handles, so it isn't redone by hand:

- **VFR** → CFR copy in `01-ingest/normalized/` (otherwise the cuts drift).
- **HDR** (HLG/PQ, typical of phones) → SDR Rec.709 copy, tone-mapped with
  reference white mapped to SDR white and gamut mapping. Edit decisions use the
  copy; **any color grade renders from the original**.
- **Rotation** metadata → real orientation reported.
- **Several audio tracks** → level and noise per track, suggested best track. If
  it isn't track 1, redo the transcript:
  `uv run ${CLAUDE_SKILL_DIR}/scripts/transcribe.py <file> --lang <code> --track <n>`.
- **Audio recorded separately** (audio-only files in the folder) → offset to
  place it on the video timeline.
- **Possible log footage** → flagged from metadata or from a flat image. Confirm
  it on the contact sheets, then read
  `${CLAUDE_SKILL_DIR}/references/log-formats.md` for the conversion.
- Faces every 0.5 s in `01-ingest/faces.json` (boxes 0–1): use them to place
  subtitles and overlays and to reframe.

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

| Type | Covers | Typical signals |
|---|---|---|
| `ad` | brand, UGC, product promo | CTA, price/offer, brand mentioned, short, product on screen |
| `talking-head` | opinion, educational to camera | one face, fixed framing, dense speech |
| `tutorial` | screencast, step by step | screen/UI, instructional vocabulary, face absent or in a corner |
| `vlog` | day in the life, travel | many locations, handheld, spontaneous speech, ambient sound |
| `podcast-interview` | 2+ voices in conversation | several speakers, long, fixed cameras |
| `testimonial` | customer or real case | interviewee talking about an experience, emotion, short |
| `music-montage` | event, aftermovie, real estate, product with no voice | little or no speech, music, many shots |
| `corporate` | company or institutional presentation | formal speech, brand, offices/team |

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

- **Cut on the transcript, not on the waveform.** auto-editor as a first pass for
  silences; then refine with the word timestamps.
- Cut on the breath, never mid-word.
- **Cleanup follows the type profile**: fillers and retakes come out fully in a
  talking head or an ad, lightly in a vlog, and almost not at all in a
  testimonial, where hesitation carries emotion. Retakes: keep the last good take.
- **J/L cuts**: the audio comes in before or carries on after the picture. It's
  what makes the cut go unnoticed.
- Talking-head jump cut: cover it with a punch-in (10–15% reframe) or b-roll.
  Never leave it raw.
- ffmpeg does the cutting. **Don't send the footage through Remotion**:
  re-encoding hours of camera footage through a browser is a waste of time.

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
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py propose            # before the question
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py build --level basic|full|none
       [--look cinematic --intensity 60] [--input-lut <format>.cube]
       [--creative-lut <user>.cube] [--reference <image>]       # after the answer
uv run ${CLAUDE_SKILL_DIR}/scripts/grade.py preview
```

- `analyze` (also run by `propose` and `build`) → `02-work/color/color.md`:
  exposure, the cast of what should be neutral, skin hue against the skin line,
  verdict.
- `build` bakes everything into **one .cube per file** and writes `grade.json`
  (which pre-filter and LUT each file gets at render). It re-measures through the
  grade and prints the cast and exposure before → after.
- `preview` → before/after sheets with waveform and vectorscope. **Look at them
  before rendering**: natural skin, no crushed blacks or burnt whites the
  original didn't have, the look at a sensible strength.
- **Order: input transform → correction → look → Rec.709.** Log: correction in
  log, before the format's official LUT (`references/log-formats.md`). HDR: tone
  mapping as a pre-filter, then the LUT. **Render from the original file**, never
  from the normalized copy.
- **Graphics and subtitles go on top after the grade**, or brand colors shift.
- A look the user liked can also be delivered as a .cube for their editor:
  `grade.py look --look cinematic --intensity 60 --file 03-delivery/<name>.cube`.

## 5. Voice, music and SFX

- **Voice chain before leveling** (it's the most noticeable difference between
  amateur and paid work): high-pass ~80 Hz → gentle de-noise → de-esser →
  moderate compression. Only then, level.
- **The voice rules.** SFX leveled by loudness (not by peak), ~8–12 dB below the
  voice. Never an SFX louder than the voice.
- **Music with dynamic ducking** against the voice, aiming for ~18 dB below
  (10–15% of the voice) while someone is speaking and rising in the gaps. A fixed
  level sounds like a template.
- Final master at **−14 LUFS integrated**, true peak ≤ −1 dBTP. Leave headroom in
  the mix (careful: mono voice → stereo adds +3 LU) and bring it to −14 with
  linear gain/loudnorm at the end.
- Careful with `loudnorm` in `linear=true`: when the true-peak ceiling limits the
  gain, **`offset` is ignored**. For fine adjustment, use an explicit
  `volume=-XdB`.

## 6. Subtitles

It's an editing decision, not a consequence of the format, and it isn't asked:
decide from the destination and the material, and state it in the delivery. Options:
no subtitles · keywords only as a graphic punchline · full burned-in · separate
`.srt`. Defaults: a feed (it starts muted) → word-by-word burned-in + `.srt`;
chosen viewing with clean audio and clear diction → `.srt` only. The type profile
can override.

- Vertical: 2–3 words per frame. Horizontal: lines of ≤42 characters.
- **Never over the face.** Check the stills section by section: the face moves
  with zooms and reframes.
- Always inside the safe zone.
- **Check proper names, brands and numbers by hand.** That's where it always
  fails, with any model; the glossary prevents most of it, not all.

## 7. Graphics over the video

**Remotion renders only the graphics layer, with an alpha channel** (ProRes 4444
or WebM VP9); ffmpeg overlays it on the edit. Cards, lower thirds, titles, logos,
punchlines. It renders in minutes instead of an hour and remains a reusable
template.

New Remotion project:
`npx remotion add @remotion/transitions @remotion/google-fonts @remotion/lottie @remotion/three`

If there are brand assets (vector logo, typefaces, colors, LUT), use them. If
not, propose a graphic identity, apply it, and show it in the delivery on a
sample card so changing it is a concrete request. Don't hold up the work waiting
for approval.

## 8. Generating video from scratch (no filmed material)

Pure motion graphics — an animated reel, a card, a visual explainer:

- **Canvas + ffmpeg, not Remotion.** An HTML page with a deterministic `frame(t)`
  function, a headless browser over CDP, and ffmpeg encoding according to the
  master rule (section 9), with `-pix_fmt yuv420p -movflags +faststart`. Average
  2–3 temporal subsamples per frame for real motion blur. It's faster to render
  and simpler to reason about than the same thing in Remotion.
- **Remotion from scratch only if** there's a series (the same mold with different
  data) or the user will touch it in Studio.
- Synthesized audio (hits, dings, backing music): float32 WAV at 48 kHz, mixed
  separately.

## 9. GPU

**Prioritize the GPU where it helps, falling back to CPU without drama.** If
there's a GPU, use it to raise quality too, not just to go faster: larger
transcription models, accelerated rendering, higher-resolution previews.

**Transcription**: with a GPU, a large model. Without a GPU, the same model size
on an optimized runtime (`faster-whisper` in int8) rather than dropping model
size: here the transcript defines where the cuts go, so degrading it degrades the
edit, not just the text. Drop model size only as a last resort, and say so.
Choose the size according to available VRAM. **Always set the language
explicitly**: auto-detection looks at the first 30 seconds and an intro with
music or silence ruins everything.

**Parallelize by real contention, not by a fixed rule.** Two heavy GPU jobs, in
series. One GPU job and one CPU job that don't step on each other, in parallel.
Light tasks, all together. Ingest is the typical case where it pays to launch
everything at once.

**Final master: GPU by default, at high quality; CPU as fallback.** Platforms
re-encode everything: what defines final quality is uploading a file with quality
to spare, not which encoder made it.

- Use the hardware encoder of whatever GPU is present, in **constant quality**
  mode (not fixed bitrate) and with its highest-quality preset. Check the options
  on that machine (`ffmpeg -h encoder=<name>`) instead of assuming them.
- **Test it first with a few seconds**: having a GPU doesn't guarantee the encoder
  works (drivers, disabled card, ffmpeg build without support).
- **CPU (x264, slow preset, low CRF)** if there's no GPU or the test fails, or
  when file size matters: at equal size, x264 gives more quality.
- Intermediates and previews, always on the GPU if there is one.
- State in the delivery what the master was encoded with.

## 10. Project structure

```
project/
  00-raw/          original material. Never touched, renamed or overwritten.
  01-ingest/       transcript, stills, material data. Regenerable.
  02-work/         cuts, intermediates, graphics layer. Disposable.
  03-delivery/     final video, .srt, thumbnail.
  project.md       what was decided and why.
```

`00-raw` is untouchable: if something has to be normalized or converted, the
result goes to `01-ingest`. `01` and `02` can be deleted entirely to recover
disk without losing anything. `project.md` holds orientation, destination,
subtitle decision, the transcription glossary, what was cut and why, what was installed and what's pending,
so work can resume without asking everything again.

**If the project is already organized differently, adapt and don't reorganize
it.** This structure is only created when starting from scratch.

## 11. Delivery

Always in `03-delivery`: **the video, the `.srt`** (even if subtitles are burned
in: YouTube and Meta use it for accessibility and index it) **and a designed
thumbnail**.

The thumbnail is composed, not extracted: a well-chosen frame from the material
plus text and branding on top. A raw frame is worse than nothing.

**Choose the frame with judgment**: a gesture that matches the headline
(pointing, surprise, thumbs up), eyes open, no mouth mid-syllable. **Deliver 2–3
complete thumbnails** to choose from, each with a different frame and gesture;
they ship with the video and don't hold up the delivery.
Minimalist background or with subtle patterns: it shouldn't compete with the text
or the person.

- Horizontal: 1280×720.
- Vertical: at full resolution (Instagram and TikTok accept a custom cover and it
  shows in the feed and in the profile grid). **The text has to survive the
  grid's center crop**, and the first frame of the video has to work on its own,
  because autoplay starts there.

**Before delivering, always:**

- Measure the export: `ffmpeg -af ebur128=peak=true`. Confirm −14 LUFS /
  ≤ −1 dBTP and that SFX and music don't bury the voice.
- Pull stills from the final export and look at them: safe zone, readable
  subtitles, nothing cut off.
- Say in one line what was left out or what was resolved on your own judgment.

**After verifying the export, delete the heavy intermediates** (intermediate
renders, ProRes, temporary WAVs): they're regenerated from the project in minutes
and there's no point in them taking up disk. They're temporary: no need to
mention it.

## 12. Safe margins

**On every 9:16 video, read `${CLAUDE_SKILL_DIR}/references/safe-margins.md`
before placing any text, subtitle, graphic or logo.**
