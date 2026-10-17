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

## Use

Ask Claude to edit a video, or run `/vibetake:edit`.

## Requirements

- [uv](https://docs.astral.sh/uv/) (runs the scripts and installs their Python dependencies on first use)
- ffmpeg / ffprobe (a full build: libplacebo or zscale for HDR, NVENC/VideoToolbox/QSV/AMF optional)
- exiftool (optional, better camera detection)

## Tests

```text
uv run tests/test_ingest.py       # ingest traps, synthetic clips
uv run tests/test_transcribe.py   # retakes, fillers, word timing (needs the Whisper model)
```

Generates synthetic clips for each ingest trap (VFR, HDR, rotation, two audio
tracks, external audio, log) and checks what `ingest.py` detects.
