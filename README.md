# Vibetake

🇪🇸 [Leer en español](README.es.md)

Vibe editing with real editorial judgment: a Claude Code plugin that takes raw
footage to a finished video — ingest, cut, mix, subtitles, graphics, thumbnails
and delivery.

## Install

```text
/plugin marketplace add Mattufia/vibetake
/plugin install vibetake@vibetake
```

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
lavalier, a recorder), references you like (covers, other videos, screenshots), and if you have them, the logo, fonts or a LUT. No
need to sort anything: the first thing Vibetake does is tidy the folder itself.

```text
my-video/                         my-video/
  DJI_0001.MP4                      00-raw/     ← clips, audio, card folders
  DJI_0002.MP4          →           brand/      ← logo, fonts, LUTs
                                    references/ ← styles you like (never shown)
  mic.wav                           01-ingest/  02-work/  03-delivery/
  logo.png                          script.docx ← anything else stays where it was
  script.docx
```

It only moves files inside the same folder (instant, nothing is copied or
re-encoded), never overwrites, and tells you what it moved. To put everything back
as it was: ask "undo the organizing". Several clips are fine: they're ordered by
recording time and treated as one sequence; separate audio is synced automatically.
Add more files later and only the new ones are moved. A photo it can't tell apart (a shot for the video, or a reference?) stays where it is, and it asks you.

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
uv run tests/test_ingest.py        # ingest traps, synthetic clips
uv run tests/test_transcribe.py    # retakes, fillers, word timing (needs the Whisper model)
uv run tests/test_grade.py         # color: ambient white balance, log path, levels, the user's LUT
uv run tests/test_match.py         # shot matching: groups, references, darks kept, skip, nudges, rules
uv run tests/test_edit.py          # cuts and render: A/V sync, no clicks, retakes out, cuts in silence
uv run tests/test_joins.py         # every join of the preview: sheets, a short reel, small/light/level jumps, flashes
uv run tests/test_take_quality.py  # two takes of a line compared: softer, shakier, blown out; a shared look says nothing
uv run tests/test_reframe.py       # framing: full/fixed/operator per segment; eases, cuts; punch-ins, impact zoom
uv run tests/test_speaker.py       # several people: who says each sentence (mics, mouths), framing follows them
uv run tests/test_broll.py         # b-roll anchored to words, sound underneath, follows re-cuts
uv run tests/test_audio.py         # cleanup on the raw (noise, level, no delay), loudness per destination, ducking, musicgen
uv run tests/test_audio_inputs.py  # the sound's source: channels, best track, separate recordings, ambience
uv run tests/test_subs.py          # .srt rules, word groups, face avoidance, burn-in
uv run tests/test_qa.py            # delivery QA: a good file passes, planted faults are caught
uv run tests/test_graphics.py      # cue sheet, synthesized SFX on their word, graphics layer, captions off
uv run tests/test_delivery.py      # versions, copy checks, cut edges on the audio, listening check
uv run tests/test_setup.py         # machine profile, install plan, tools found outside PATH
uv run tests/test_organize.py      # loose files → 00-raw/ and brand/, nothing overwritten, undo
uv run tests/test_refs.py          # references made viewable, a reference video's rhythm measured
uv run tests/test_cut_rules.py     # reactions vs fillers, silent stretches, natural pace, protected ranges
uv run tests/test_design.py        # project fonts and icons, palette, Remotion layer with design + safe-zone check (network, Node)
uv run tests/test_stage.py         # composition: the edit placed in a stage, frame-synced, page and frame components (Node)
uv run tests/test_timing.py        # speed, ramps, freezes, reverse, boomerang, J/L cuts, transitions, fades: frame by frame
uv run tests/test_effects_edges.py # transitions beside straight cuts, L+J on one segment, freeze at 0, smooth, J/L as played
uv run tests/test_montage.py       # montage to music: moments, cuts on the beat, shorter shots when loud, blocks around speech
uv run tests/test_shorts.py        # shorts from a long video: ranges without retakes, hook, own cut/captions/render, QA per short
uv run tests/test_translate.py     # translated .srt: same timing, too-fast and too-long cues flagged, CJK without spaces
uv run tests/test_screen.py        # screen zooms: the menu and the typing, never the cursor or a page change; eases, glides, vertical
uv run tests/test_ingest_edges.py  # odd material: duplicate names, silent/no audio, broken files, proxies, no duration, lock, false retakes
uv run tests/test_cut_edges.py     # cut order: hooks kept in place, protections per variant, silences where listed, carry ties
uv run tests/test_render_edges.py  # upscale keeps blur and effects, static blur over handles, 100+ spans, audio offset, apostrophes
uv run tests/test_look.py          # fit over a blurred copy, flip/rotate/tilt, chroma key, tracked blur boxes, stabilization, look
uv run tests/test_audio_fx.py      # bleeped words (beep/mute, starred subs), beat tracking, voice effects, chapters in the MP4, GIF
uv run tests/test_compose.py       # whip/push transitions, picture-in-picture, split screen, graphics behind the person
uv run tests/test_upscale.py       # AI upscaling of stretched segments (skipped without Real-ESRGAN)
uv run tests/test_fixes.py         # regressions: re-cut keeps placements, stale layers, HDR without grade, mixed log
uv run tests/test_readme.py        # README.md and README.es.md have the same structure
```

Each test prints PASS/FAIL per check. Most build their own synthetic clips; a few
need the Whisper model, Node or the network, as noted.

## Contributing

Ideas, bugs and pull requests are welcome.

- **A bug or an idea**: open an issue. For a video that came out wrong, say what and
  at what second, and attach `03-delivery/qa/qa.md` if you can.
- **A camera that isn't detected**: run `uv run tools/camera_report.py <clip>` on an
  original file from the camera and paste the output into an issue.
- **A pull request**: keep it focused on one thing, add or update a test in `tests/`
  that fails without your change, and run the tests above before sending it.
- **Docs**: this README has a Spanish copy, [`README.es.md`](README.es.md). A change
  here goes there too, in the same pull request (`uv run tests/test_readme.py` checks
  that both have the same structure).

## License

[MIT](LICENSE). Remotion, used for the graphics layer, has its own license (see
Requirements).
