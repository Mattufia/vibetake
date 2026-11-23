# Music: where it comes from

Rule zero: the music is the plugin's generator (`musicgen.py`) or a file the user
brings. Never search or download music from a library or bank, and never use anything
that needs a credit: there is nothing to mention when publishing. Keep where every
track came from in `project.md`.

## 1. Synthesized (the default, for every kind of video)

```
uv run ${CLAUDE_SKILL_DIR}/scripts/musicgen.py --mood <mood> --duration <edit length> --energy "<s>:low|mid|high,..." [--bpm] [--key] [--seed]
uv run ${CLAUDE_SKILL_DIR}/scripts/audio.py music --file 02-work/music/<mood>.wav
```

**Scored for this video, never a default.** The music comes from what the video is
and what happens in it, decided after the cut:

- **Mood** from the profile, the topic and the tone of the transcript: `calm`, `ambient`,
  `piano` (explaining, relaxed), `warm`, `romantic`, `sad` (emotional, testimonial,
  family), `lofi`, `jazz`, `acoustic` (casual, study, desk, cooking), `upbeat`, `happy`,
  `playful`, `corporate`, `inspiring` (positive, product, lifestyle), `energetic`,
  `house`, `funk`, `latin`, `rock`, `synthwave`, `hiphop`, `trap` (sport, action, a fast
  ad), `cinematic`, `epic` (travel, a reveal), `tense`, `dark`, `mystery` (a problem, a
  before). Two moods in one video (a problem then the solution): two beds, each with
  `--duration` of its part.
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

Computed note by note: free to use anywhere, needs nothing but numpy, exactly the
length of the edit (get it with `broll.py check`), with a `.beats.json` for cutting on
the beat. It's still synthesized: under the voice, ducked, it works best. When the
music carries the video (a montage, a brand piece), listen first and tell the user
it's synthesized, so they can bring their own track if they want one.

### Directing the piece

`musicgen.py --list` shows everything available (27 moods, 26 instruments, patterns,
grooves, kits, modes). A mood is only the starting point; say what the video needs:

```
musicgen.py --mood cinematic --duration 42 --form "intro:2,build:4,drop:6,outro:2" \
  --progression "i VI III VII" --instrument "lead=choir,bass=bass_808" --pattern "bass=octave" \
  --hit "12.4,31.0" --ending ring --no-melody --out 02-work/music/main.wav
```

- **Land on the edit**: `--hit` puts an impact on those seconds; without `--bpm` the
  tempo moves up to 5% so they fall on bars. `--energy` (`low|mid|high` or 0–1) or
  `--form` shapes the arc. A melody only enters from energy 0.6: keep speech over
  lower sections, or use `--no-melody` for a pure bed.
- **Compare**: `--variations 3` gives three takes (`_v1`, `_v2`, `_v3`).
- **Change one thing, keep the rest** (the user liked it except…): every render writes
  `<out>.score.json`. Reopen it with `--score` plus only the change:

| The user says | Do |
|---|---|
| "sin batería", "solo piano y bajo" | `--mute drums`, `--solo keys,bass` |
| "más lento", "más agudo", "en menor" | `--bpm 84`, `--key E`, `--mode minor` |
| "otra melodía en el estribillo" | `--regen chorus:lead` (`--seed` for another idea) |
| "cambiá los acordes del verso" | `--regen verse:chords` |
| "otro ritmo acá", "otro bajo" | `--regen chorus:drums`, `--regen verse:bass` |
| "otro instrumento" | `--instrument lead=flute` |
| "más oscuro, más sucio, más grande" | `--bright 0.7`, `--warmth 0.5`, `--lofi 0.4`, `--reverb 0.45`, `--width 1.3` |
| "la melodía más baja" | `--gain lead=-4` |

- **Note by note**: anything flags don't reach, edit the score: each section has
  `bars`, `energy`, `chords` (`"Am:2"` holds two bars), optional `parts` (which play),
  `patterns`, `groove`, and `notes` per part as `[beat, length, note, velocity]`, with
  notes as MIDI numbers or names (`"E5"`). Notes written for a part replace its pattern
  in that section. Then render it with `--score`.
- **Stems**: `--stems` writes one WAV per part in `<out>.stems/`.

### Cutting on the beat (any track)

```
uv run ${CLAUDE_SKILL_DIR}/scripts/audio.py beats --file <track>    # → <track>.beats.json {bpm, beats, bars}
```

Times are seconds of the track. With the music in the edit at `music.at` and
starting from `music.in` of the track, a beat lands on the timeline at
`at + (beat − in)` the first time through (music loops to fill the edit: past the track's end, add its length): put cuts there (on bars for big changes). It assumes a steady
tempo: a track that speeds up or drifts (live, rubato) gets beats that slide, so
check by ear and cut on what you hear.

## 2. The user's own file

Only when the user brings it (bought, licensed, or theirs), in the project's `music/`
folder: it takes priority over the generator. Never ask them to go and find one. If it
looks like a commercial song they may not have the rights to, say so before using it
(Content ID mutes or blocks it).

## Level

How it's mixed under the voice: SKILL.md section 5. The bed sits at ~−28 LUFS;
`--gain` on `audio.py music` nudges it (+3 louder, −3 quieter).

## Everything else (effects, b-roll, images, fonts)

- **Effects**: the synthesized set (`sfx.py`) or the user's. **B-roll and images**: the
  user's. No libraries, stock banks or manual downloads.
- Fonts and icons come from the open-license sets the scripts already fetch.
- **Nothing that needs a credit.** If something the user brings does, say so before
  using it.
- **Keep the source of every piece the user brings in `project.md`**: if a copyright
  claim (Content ID) arrives, that's the proof.
