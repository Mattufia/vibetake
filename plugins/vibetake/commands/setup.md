---
description: Set up this machine for Vibetake — check tools, GPU and models, install what's missing, download the heavy models
argument-hint: "[check]"
---

Set up this machine for video editing with Vibetake. Talk to the user in their
language, in plain words; they may not be technical. Say what you'll install and how
big it is before doing it.

The scripts live in `${CLAUDE_PLUGIN_ROOT}/skills/edit/scripts/`.

1. **uv first** — it runs everything else. If `uv --version` fails, install it, saying so:
   Windows `winget install --id astral-sh.uv -e --accept-source-agreements --accept-package-agreements`,
   macOS `brew install uv`, Linux `curl -LsSf https://astral.sh/uv/install.sh | sh`.
   A terminal opened before the install may not see it yet: call it by its full path
   (winget puts it under `%LOCALAPPDATA%\Microsoft\WinGet\Links` or `Packages`,
   the Linux installer under `~/.local/bin`).

2. **Check**: `uv run ${CLAUDE_PLUGIN_ROOT}/skills/edit/scripts/setup.py check`.
   Summarize in a few lines: machine, GPU, what's ready, what's missing and why it
   matters (required / recommended / optional), and any WARNING. If the argument was
   `check`, stop here.

3. **Install what's missing** (if anything): run `setup.py install` (a dry run that
   prints the plan), show the user the list, and after their OK run
   `setup.py install --yes`. On Windows, winget may open permission prompts the
   user has to accept. Optional tools (Node for Remotion, Real-ESRGAN) only if the
   user wants them — say what each is for. If there's no package manager or no
   permission to install (a locked-down work machine), say what's missing and what
   still works without it (SKILL.md, section 0).

4. **Heavy downloads now, not mid-edit**: offer `setup.py prefetch` — the Whisper
   model (~3 GB) and, with an NVIDIA GPU, the CUDA libraries (~1 GB), tested on a
   short clip; the face detector; rembg's portrait model for covers (~1 GB).
   Recommended on the first setup. It shows progress; if asked how it's going,
   answer from that output.

5. **Close with one short summary**: ready or not, what transcription runs on
   (GPU or CPU), the encoder the master will use, and anything left pending. The
   machine profile is in `~/.vibetake/machine.json` (one per machine, outside the
   plugin); nothing personal goes into the plugin.

Run it again any time: after a driver or ffmpeg update, on a new machine, or when a
script says a tool is missing.
