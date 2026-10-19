# Vibetake

Vibe editing with real editorial judgment: a Claude Code plugin that takes raw
footage to a finished video — ingest, cut, mix, subtitles, graphics, thumbnails
and delivery.

> Private / work in progress.

## Install

```text
/plugin marketplace add Mattufia/vibetake
/plugin install vibetake@vibetake
```

Private repo: the machine needs GitHub credentials that `git` can use without
prompting (`gh auth login` then `gh auth setup-git`). Without an SSH key, set
`CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1`.

**Keep it updated automatically**: `/plugin` → **Marketplaces** → `vibetake` →
**Enable auto-update** (off by default for third-party marketplaces). New versions
arrive within the first minutes of a session and apply on the next one, or right
away with `/reload-plugins`. To update by hand: `/plugin` → **Marketplaces** →
`vibetake` → **Update marketplace**.

## Set up a machine

Run `/vibetake:setup` once per machine: it checks tools, GPU and models, installs
what's missing (winget / Homebrew / apt, after your OK) and downloads the heavy models
up front, with a real transcription test on the GPU. The machine profile goes to
`~/.vibetake/machine.json`, never into the plugin.

## Use: editing a video, step by step

### 1. Put your files in a folder — as they are

One folder per video, with everything loose inside: the clips, separate audio (a
lavalier, a recorder), photos, and if you have them, the logo, fonts or a LUT. No
need to sort anything: the first thing Vibetake does is tidy the folder itself.

```text
my-video/                         my-video/
  DJI_0001.MP4                      00-raw/     ← clips, audio, photos, card folders
  DJI_0002.MP4          →           brand/      ← logo, fonts, LUTs
  mic.wav                           01-ingest/  02-work/  03-delivery/
  logo.png                          script.docx ← anything else stays where it was
  script.docx
```

It only moves files inside the same folder (instant, nothing is copied or
re-encoded), never overwrites, and tells you what it moved. To put everything back
as it was: ask "undo the organizing". Several clips are fine: they're ordered by
recording time and treated as one sequence; separate audio is synced automatically.
Add more files later and only the new ones are moved.

### 2. Open Claude Code in that folder and ask

```text
cd my-video
claude
```

Ask in your own words. The more it knows, the fewer questions it asks:

```text
Edit this video for Instagram Reels and TikTok.
```
```text
Edit this video for YouTube. It's in Spanish; the brand is Acme, the logo is in the folder.
Names to spell right: Vibetake, DaVinci, Ana Pérez.
```
```text
Cut a 30-second ad from this, vertical, with a strong hook and a call to action.
```

What helps: **where it's going** (TikTok, Reels, Shorts, YouTube…), **the language**,
names and brands that appear, and the brand assets if there are any. What you don't
need to say: subtitles, pacing, margins, music — it decides them from the platform
and the kind of video, and tells you what it chose.

### 3. What happens

1. **It looks at the material** before touching anything: transcribes it, detects
   the camera, HDR or log footage, faces, retakes and filler words.
2. **It asks only what it can't deduce**, all at once — usually where the video goes
   and how much color correction (with before/after previews to compare).
3. **It edits**: cuts on the transcript (retakes and fillers out, natural pauses),
   reframes for vertical when needed, corrects color, cleans and mixes the audio,
   adds subtitles, graphics and sound effects according to the type of video.
4. **It checks the result** before handing it over: format, loudness, sync,
   subtitles inside the safe zone — and it transcribes the final file again to catch
   a cut in the middle of a word or a lost phrase.

### 4. What you get, in `03-delivery/`

| File | What |
|---|---|
| `my-video_v1.mp4` | the video (each round of changes is a new version: `_v2`, `_v3`…) |
| `my-video.srt` | subtitles as a file, for the platforms' accessibility and search |
| `cover_<platform>_<n>.jpg` | three cover concepts per platform (YouTube thumbnail, Reels/TikTok cover) |
| `copy.txt` | the text to publish with, per platform: caption, hashtags, title, chapters |
| `qa/qa.md` | the final check, plus reminders for the upload (e.g. Instagram's "highest quality") |

`project.md` keeps what was decided and why, so work can resume later without
starting over.

### 5. Ask for changes

Just say it; the edit is kept as a list of cuts, so changes don't redo everything:

```text
Take out the part where I talk about the price.
Start with the line about the deadline.
Subtitles bigger and without capitals.
Less music. Warmer color. No zoom on the cuts.
Another cover, with the frame where I'm pointing.
```

Each round produces a new video version; covers, subtitles and copy are updated in
place.

### Other commands

- `/vibetake:setup` — check or set up the machine (once per machine, or after a
  driver/ffmpeg update).
- `/vibetake:setup check` — only see what's installed and ready.

### If something looks wrong

Say what and at what second ("at 0:42 a word is cut", "the subtitles cover my face at
1:10"). `03-delivery/qa/qa.md` and `project.md` show what was checked and decided.

## Requirements

- [uv](https://docs.astral.sh/uv/) (runs the scripts and installs their Python dependencies on first use)
- ffmpeg / ffprobe (a full build: libplacebo or zscale for HDR, NVENC/VideoToolbox/QSV/AMF optional)
- exiftool (optional, better camera detection)
- Node.js 18+ for the animated graphics layer, built with [Remotion](https://www.remotion.dev/)
  in each project. **Remotion's license**: free for individuals and companies of up to
  3 people; larger companies need a [Remotion company license](https://www.remotion.dev/license).
- Typefaces (Google Fonts, open licenses) and icons (Simple Icons CC0, Lucide ISC,
  Phosphor MIT) are downloaded per project, while editing — nothing preinstalled
- Optional, installed on first use or by hand: rembg (`birefnet-portrait`, cut-out
  people for covers, ~1 GB model), realesrgan-ncnn-vulkan (AI upscaling of stretched
  crops; on the PATH or in `~/.vibetake/tools/realesrgan/`)

## Tests

```text
uv run tests/test_ingest.py       # ingest traps, synthetic clips
uv run tests/test_transcribe.py   # retakes, fillers, word timing (needs the Whisper model)
uv run tests/test_grade.py        # color: ambient white balance, log path, levels, the user's LUT
uv run tests/test_match.py        # shot matching: groups, references, darks kept, skip, nudges, rules
uv run tests/test_edit.py         # cuts and render: A/V sync, no clicks, retakes out, cuts in silence
uv run tests/test_reframe.py      # framing: full/fixed/operator per segment; eases, cuts; punch-ins, impact zoom
uv run tests/test_broll.py        # b-roll anchored to words, sound underneath, follows re-cuts
uv run tests/test_audio.py        # cleanup on the raw (noise, level, no delay), loudness per destination, ducking, musicgen
uv run tests/test_subs.py         # .srt rules, word groups, face avoidance, burn-in
uv run tests/test_qa.py           # delivery QA: a good file passes, planted faults are caught
uv run tests/test_graphics.py     # cue sheet, synthesized SFX on their word, graphics layer, captions off
uv run tests/test_delivery.py     # versions, copy checks, cut edges on the audio, listening check
uv run tests/test_setup.py        # machine profile, install plan, tools found outside PATH
uv run tests/test_organize.py     # loose files → 00-raw/ and brand/, nothing overwritten, undo
uv run tests/test_cut_rules.py    # reactions vs fillers, silent stretches, natural pace, protected ranges
uv run tests/test_design.py       # project fonts and icons, palette, Remotion layer with design + safe-zone check (network, Node)
uv run tests/test_upscale.py      # AI upscaling of stretched segments (skipped without Real-ESRGAN)
uv run tests/test_fixes.py        # regressions: re-cut keeps placements, stale layers, HDR without grade, mixed log
```

Generates synthetic clips for each ingest trap (VFR, HDR, rotation, two audio
tracks, external audio, log) and checks what `ingest.py` detects.
