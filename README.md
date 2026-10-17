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
uv run tests/test_grade.py        # color: cast correction, log path, looks, creative
uv run tests/test_edit.py         # cuts and render: A/V sync, no clicks, retakes out, cuts in silence
uv run tests/test_reframe.py      # camera operator: holds, eases, cuts; rendered crop follows; punch-ins
uv run tests/test_broll.py        # b-roll anchored to words, sound underneath, follows re-cuts; hook ranking
uv run tests/test_audio.py        # cleanup on the raw (noise, level, no delay), loudness per destination, ducking, musicgen
uv run tests/test_subs.py         # .srt rules, word groups, face avoidance, burn-in
```

Generates synthetic clips for each ingest trap (VFR, HDR, rotation, two audio
tracks, external audio, log) and checks what `ingest.py` detects.
