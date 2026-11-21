# Music: where it comes from

Rule zero: never music the user can't publish (Content ID mutes or blocks it; a
brand can't use unlicensed music). Keep where every track came from in `project.md`.

## 1. Synthesized (default for backgrounds)

```
uv run ${CLAUDE_SKILL_DIR}/scripts/musicgen.py --mood <mood> --duration <edit length> --energy "<s>:low|mid|high,..." [--bpm] [--key] [--seed]
uv run ${CLAUDE_SKILL_DIR}/scripts/audio.py music --file 02-work/music/<mood>.wav
```

**Scored for this video, never a default.** The music comes from what the video is
and what happens in it, decided after the cut:

- **Mood** from the profile, the topic and the tone of the transcript: `calm`
  (explaining, relaxed), `warm` (emotional, testimonial, family), `lofi` (casual,
  study, desk, cooking), `upbeat` (positive, product, lifestyle), `energetic` (sport,
  action, a fast ad), `cinematic` (epic, travel, a reveal), `tense` (a problem, a
  mystery, a before). Two moods in one video (a problem then the solution): two beds,
  each with `--duration` of its part.
- **Tempo** from the pace of the cut: few cuts and calm speech, the low end of the
  mood's range; fast cuts or a lot of action, the high end (`--bpm`).
- **Energy** (`--energy`) from the edit's own times: `low` under a quiet start or a
  serious line, `high` from the hook, the reveal, the action or the payoff, `mid`
  for the rest; back to `low`/`mid` toward the close. A rise gets a drum fill into it.
- The seed comes from the project, so each video sounds different and the same edit
  reproduces it. The user doesn't like it: another `--seed` (new key, tempo,
  chords), or another mood if it's the feel that's wrong.
- Write in `project.md` what was chosen and why (mood, bpm, key, seed, energy: the
  `.beats.json` has them), and tell the user in one line.

Computed note by note (pads, bass, arpeggio, drums, reverb): free to use anywhere,
exactly the length of the edit (get it with `broll.py check`), with an ending, and
a `.beats.json` for cutting on the beat. It sounds synthetic up close; under the
voice, ducked, it works as a bed for quick, informal videos. Another `--seed` gives
another variation. Not for a scripted piece, a brand video or a montage that lives
on its music: use a real track there.

### Cutting on the beat (any track)

```
uv run ${CLAUDE_SKILL_DIR}/scripts/audio.py beats --file <track>    # → <track>.beats.json {bpm, beats, bars}
```

Times are seconds of the track. With the music in the edit at `music.at` and
starting from `music.in` of the track, a beat lands on the timeline at
`at + (beat − in)` the first time through (music loops to fill the edit: past the track's end, add its length): put cuts there (on bars for big changes). It assumes a steady
tempo: a track that speeds up or drifts (live, rubato) gets beats that slide, so
check by ear and cut on what you hear.

## 2. A real track (better quality)

- **The user's own file** (bought, licensed, or theirs).
- **Downloaded by hand** into the project's `music/` folder, from libraries that
  allow commercial use without attribution — check the track's page each time,
  terms change:
  - **Pixabay Music** (Pixabay Content License). Its API has no music endpoint, so
    no automatic search.
  - **Mixkit** (Mixkit license): **not for TV/radio broadcast, video games or
    DVDs**.
- Not **YouTube Audio Library**: the license changes per track, standard tracks
  are only for YouTube and many need attribution.

## Level

How it's mixed under the voice: SKILL.md section 5. The bed sits at ~−28 LUFS;
`--gain` on `audio.py music` nudges it (+3 louder, −3 quieter).

## Any third-party material (music, effects, stock, images, fonts)

- Free licenses, ideally without attribution: CC0, or Pixabay/Pexels-style licenses;
  fonts under OFL (Google Fonts). If only an option with attribution exists, say so
  and put the credit in the copy.
- **Keep the source link of every piece in `project.md`** with its license: if a
  copyright claim (Content ID) arrives, that's the proof.
- No recognizable brands or people in stock used for ads (they need releases).
