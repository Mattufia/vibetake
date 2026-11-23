# Music: where it comes from

Rule zero: the music is the plugin's generator (`musicgen.py`) or a file the user
brings. Never search or download music from a library or bank, and never use anything
that needs a credit: there is nothing to mention when publishing. Keep where every
track came from in `project.md`.

## The tone brief: the whole video first

**Before proposing or composing anything, write it in `project.md`.** Music chosen line
by line goes wrong: one word like "complicadísimo" in a pitch full of enthusiasm turned
into a sad piece. Read the whole video once, then decide one tone for all of it.

```
## Music brief
- About: <what the video is about, in one line>
- Speaker: <how the person is: excited, calm, proud, worried…>
- Viewer should feel: <the one feeling they leave with>
- Tone of the whole piece: <e.g. bright, confident, forward>
- Allowed variation: <e.g. quieter under the problem, fuller on the payoff; same tone>
```

Read it from these sources, in this order; a later one never outvotes an earlier one
except the last:

1. **The full message and how it ends.** A problem that gets solved is a positive video.
   The ending says what the video was for.
2. **How the person says it**: speed, voice energy, expression. Someone smiling while
   describing something hard is not sad.
3. **The image**: light, color, place, movement. It supports the reading, and a dark car
   interior doesn't make a happy message dark.
4. **The user's request or reference overrides all of the above.**

**Moments change intensity, never emotion.** A serious line, a doubt or a problem gets
less: fewer instruments, lower energy, more space. It does not get a sad chord or a
minor turn in a bright piece. The music is sad, dark or tense only if the *whole* video
is. If the reading is truly unclear, the style question is where the user settles it.

When delivering the music, say in one line what you understood ("Lo leí como un pitch
entusiasta que termina en una buena noticia, así que fui por algo luminoso y con empuje").

## When there's music

Only when the user wants it (SKILL.md section 5). The edit is delivered without it first.

1. **Write the tone brief** (above) before proposing anything.
2. **Ask with `AskUserQuestion`, these two questions and no others:**
   - **Style**: 3 options that fit the brief, the first marked recommended with
     the reason ("Pop enérgico (recomendado: …)", "Tech / electrónico", "Funk con
     groove"); the free-text option covers anything else. Styles are a direction to
     compose toward, never a stored template. If the user already said one ("algo
     tech"), skip this question.
   - **Energy**: tranquila · media · a tope.
   Volume isn't asked: it stays in the background unless the user says otherwise.
3. **Compose, add it, and remix only the sound**: `audio.py music --file <track>`, then
   `render.py --audio-only`. The picture is copied, so a try takes seconds. If the picture
   changed since the last version it refuses: render normally.
4. **Show it and adjust from plain phrases** ("más rápido", "sin batería"), remixing
   again each time. `audio.py music --clear` + `--audio-only` takes it back out.

## 1. Synthesized (when the user wants music and brings no track)

```
uv run ${CLAUDE_SKILL_DIR}/scripts/musicgen.py --feel "light=.7,drive=.4,tension=.2,organic=.8,size=.3,color=.5,sway=.2" --duration <edit length> --energy "<s>:low|mid|high,..." [--bpm] [--key] [--seed]
uv run ${CLAUDE_SKILL_DIR}/scripts/audio.py music --file 02-work/music/music.wav
```

**Scored for this video, never a default.** There are no styles to pick from. From
the brief, set seven numbers (0–1) for the whole piece; mode, tempo, chords, melody,
rhythm, instruments and room are composed from them:

| Feel | Read it from | 0 → 1 |
|---|---|---|
| `light` | the brief's tone: what the whole video leaves you with | dark, heavy → luminous, hopeful |
| `drive` | cuts per minute, speech rate, action on screen | still → pushing forward |
| `tension` | is the *whole* video unresolved? (a problem that gets solved isn't) | settled → unresolved |
| `organic` | people, hands, nature, craft vs screens, tech, city at night | electronic → played by people |
| `size` | close and personal vs landscapes, events, big claims | intimate → huge |
| `color` | how refined or adult the subject and the look are | plain triads → rich sevenths and ninths |
| `sway` | relaxed, humor, dance, walking vs precise, formal | straight → loose, swung |

- Decide each number from this video's evidence, not from its genre: two vlogs can
  land far apart. One feel for the whole video. Two beds with different emotions only
  if the brief says the video really turns (or the user asks).
- **Energy** (`--energy`) from the edit's own times: `low` under a quiet start or a
  serious line (less music, same emotion), `high` from the hook, the reveal, the action or the payoff, `mid`
  for the rest; back to `low`/`mid` toward the close. A rise gets a drum fill into it.
- The seed comes from the project, so each video sounds different and the same edit
  reproduces it. The user doesn't like it: another `--seed` (new key and ideas on the
  same feel), or move the number that's wrong ("más alegre" is more `light`).
- Write under the brief in `project.md` the feel and why (with bpm, key, mode and seed: the
  `.beats.json` has them), and tell the user in one line.

**The writing follows the rules of music** (`_compose.py`): chords move by function
and phrases close on cadences; a question ends open and its answer lands on the tonic;
voices move as little as possible; the melody is a motif that returns, with chord tones
on strong beats and long notes, dissonance that resolves by step and singable leaps.

Computed note by note: free to use anywhere, needs nothing but numpy, exactly the
length of the edit (get it with `broll.py check`), with a `.beats.json` for cutting on
the beat. It's still synthesized: under the voice, ducked, it works best. When the
music carries the video (a montage, a brand piece), listen first and tell the user
it's synthesized, so they can bring their own track if they want one.

### Directing the piece

`musicgen.py --list` shows everything available (the feel, 26 instruments, patterns,
kits, modes). The feel is only the starting point; say what the video needs:

```
musicgen.py --feel "light=.3,drive=.5,tension=.6,size=.9" --duration 42 --form "intro:2,build:4,drop:6,outro:2" \
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
  `patterns`, `groove` (drum lines of 16 steps), and `notes` per part as `[beat, length, note, velocity]`, with
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
