# Vibetake

Vibe editing with real editorial judgment: a Claude Code plugin that takes raw
footage to a finished video — ingest, cut, mix, subtitles, graphics, thumbnails
and delivery.

> Private / work in progress.

## Install

```text
/plugin marketplace add <owner>/vibetake
/plugin install vibetake@vibetake
```

Private repo: the machine needs GitHub credentials that `git` can use without
prompting (`gh auth login` then `gh auth setup-git`). Without an SSH key, set
`CLAUDE_CODE_PLUGIN_PREFER_HTTPS=1`.

## Set up a machine

Run `/vibetake:setup` once per machine: it checks tools, GPU and models, installs
what's missing (winget / Homebrew / apt, after your OK) and downloads the heavy models
up front, with a real transcription test on the GPU. The machine profile goes to
`~/.vibetake/machine.json`, never into the plugin.

## Use

Ask Claude to edit a video, or run `/vibetake:edit`.

## Requirements

- [uv](https://docs.astral.sh/uv/) (runs the scripts and installs their Python dependencies on first use)
- ffmpeg / ffprobe (a full build: libplacebo or zscale for HDR, NVENC/VideoToolbox/QSV/AMF optional)
- exiftool (optional, better camera detection)
- Optional, installed on first use or by hand: rembg (`birefnet-portrait`, cut-out
  people for covers, ~1 GB model), realesrgan-ncnn-vulkan (AI upscaling of stretched
  crops; on the PATH or in `~/.vibetake/tools/realesrgan/`)

## Tests

```text
uv run tests/test_ingest.py       # ingest traps, synthetic clips
uv run tests/test_transcribe.py   # retakes, fillers, word timing (needs the Whisper model)
uv run tests/test_grade.py        # color: cast correction, log path, looks, creative
uv run tests/test_edit.py         # cuts and render: A/V sync, no clicks, retakes out, cuts in silence
uv run tests/test_reframe.py      # framing: full/fixed/operator per segment; eases, cuts; punch-ins, impact zoom
uv run tests/test_broll.py        # b-roll anchored to words, sound underneath, follows re-cuts; hook ranking
uv run tests/test_audio.py        # cleanup on the raw (noise, level, no delay), loudness per destination, ducking, musicgen
uv run tests/test_subs.py         # .srt rules, word groups, face avoidance, burn-in
uv run tests/test_qa.py           # delivery QA: a good file passes, planted faults are caught
uv run tests/test_graphics.py     # cue sheet, synthesized SFX on their word, graphics layer, captions off
uv run tests/test_cover.py        # covers per platform: head size, crop-safe layouts, no text on the face
uv run tests/test_delivery.py     # versions, copy checks, cut edges on the audio, listening check
uv run tests/test_setup.py        # machine profile, install plan, tools found outside PATH
uv run tests/test_upscale.py      # AI upscaling of stretched segments (skipped without Real-ESRGAN)
```

Generates synthetic clips for each ingest trap (VFR, HDR, rotation, two audio
tracks, external audio, log) and checks what `ingest.py` detects.
