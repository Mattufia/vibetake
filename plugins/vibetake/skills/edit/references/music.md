# Music: where it comes from

Rule zero: never music the user can't publish (Content ID mutes or blocks it; a
brand can't use unlicensed music). Keep where every track came from in `project.md`.

## 1. Synthesized (default for backgrounds)

```
uv run ${CLAUDE_SKILL_DIR}/scripts/musicgen.py --mood calm|upbeat|lofi|cinematic --duration <edit length> [--bpm] [--key] [--seed]
uv run ${CLAUDE_SKILL_DIR}/scripts/audio.py music --file 02-work/music/<mood>.wav
```

Computed note by note (pads, bass, arpeggio, drums, reverb): free to use anywhere,
exactly the length of the edit (get it with `broll.py check`), with an ending, and
a `.beats.json` for cutting on the beat. It sounds synthetic up close; under the
voice, ducked, it works as a bed for quick, informal videos. Another `--seed` gives
another variation. Not for a scripted piece, a brand video or a montage that lives
on its music: use a real track there.

## 2. A real track (better quality)

- **The user's own file** (bought, licensed, or theirs).
- **Downloaded by hand** into the project (`00-raw/music/`), from libraries that
  allow commercial use without attribution — check the track's page each time,
  terms change:
  - **Pixabay Music** (Pixabay Content License). Its API has no music endpoint, so
    no automatic search.
  - **Mixkit** (Mixkit license): **not for TV/radio broadcast, video games or
    DVDs**.
- Not **YouTube Audio Library**: the license changes per track, standard tracks
  are only for YouTube and many need attribution.

## 3. Planned (researched, not built)

- **Openverse** (keyless API, filter `license=cc0`, `category=music`): automatic
  search of public-domain music; quality varies a lot, always listen first.
- **Freesound** (free API key, filter `license:"Creative Commons 0"`): loops and
  sound effects.
- **Local generation with ACE-Step 1.5** on the GPU (reported as MIT-licensed and
  usable commercially, 4–8 GB VRAM): verify the license on the official model card
  before using it; the only option for real-sounding music made to measure.

## Mixing

`render.py` levels the bed (~−28 LUFS), ducks it ~14–18 dB under the voice and
brings it back in the pauses, fades in and out, and masters the whole mix to the
destination's loudness. `--gain` on `audio.py music` nudges the bed (+3 louder,
−3 quieter).
