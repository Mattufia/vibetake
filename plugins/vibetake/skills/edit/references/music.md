# Music: where it comes from

Rule zero: the music is a file the user brings, a CC0 track found with `musicfind.py`,
or the plugin's generator (`musicgen.py`). Never use anything that needs a credit or
limits commercial use: there is nothing to mention when publishing. Keep where every
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
3. **Find it (section 1) or, if the user prefers, compose it (section 2), add it, and
   remix only the sound**: `audio.py music --file <track>`, then
   `render.py --audio-only`. The picture is copied, so a try takes seconds. If the picture
   changed since the last version it refuses: render normally.
4. **Show it and adjust from plain phrases** ("más rápido", "sin batería"), remixing
   again each time. `audio.py music --clear` + `--audio-only` takes it back out.

## 1. Real music, CC0 (the default when the user brings no track)

```
uv run ${CLAUDE_SKILL_DIR}/scripts/musicfind.py find --query "upbeat,happy background" --duration 42.6 --tone bright --bpm 90-135
uv run ${CLAUDE_SKILL_DIR}/scripts/musicfind.py use --file 02-work/music/candidates/<track>.mp3
uv run ${CLAUDE_SKILL_DIR}/scripts/audio.py music --file 02-work/music/candidates/<track>.mp3 --in <start>
```

- **`--query`**: a few comma-separated words about the music, from the brief and the chosen
  style. Only that leaves the machine: never the project's name, transcript or brief.
- **`--tone`** bright or dark from the brief (`any` if neither), **`--bpm`** from the energy.
- It confirms CC0 on each track's own page, measures key, tempo, activity and room for the
  voice, and keeps up to 3. It takes a few minutes: say so. Measuring rules tracks out; it
  can't say a track is good, so **the user chooses by ear**.
- **Show them mixed into the video** (`--audio-only`, one version per track), and say which
  are shorter than the video: they repeat. Run `use` only on the one chosen.
- **None convinces:** ask what was wrong, change the words or range and run `find` again. A
  track already shown never comes back.
- **Still nothing, or no results:** ask for *one* track from the Pixabay search `find` prints
  (Pixabay's license allows it inside a video, not as a loose file). It goes in `music/`.
  Measure it before mixing, then record it:
  `musicfind.py check --file music/<track> --duration <s> --tone bright --bpm 90-135`,
  `musicfind.py use --file music/<track> --url <its page> --license "Pixabay Content License"`.
- Never bundle a track with the plugin or hand it over outside the video.

## 2. Synthesized (when the user asks for it, or there's no internet)

**You are the composer.** Write the whole piece as a score for this video: no presets,
no numbers that pick for you. What's written is exactly what plays.

```
uv run ${CLAUDE_SKILL_DIR}/scripts/musicgen.py --list                                    # instruments, kits, patterns, notation
uv run ${CLAUDE_SKILL_DIR}/scripts/musicgen.py --score 02-work/music/piece.json --check --speech "0.5-12,14-30"
uv run ${CLAUDE_SKILL_DIR}/scripts/musicgen.py --score 02-work/music/piece.json --out 02-work/music/music.wav
uv run ${CLAUDE_SKILL_DIR}/scripts/audio.py music --file 02-work/music/music.wav
```

```json
{"version": 3, "bpm": 112, "key": "D", "mode": "major", "duration": 42.6, "kit": "acoustic", "ending": "ring",
 "motifs": {"hook": "F#5:1 A5:.5 B5:.5 A5:2"},
 "parts": {
   "guitarra": {"instrument": "nylon", "role": "keys", "pattern": "broken", "gain": -11},
   "bajo": {"instrument": "bass_electric", "role": "bass", "pattern": "root"},
   "silbido": {"instrument": "flute", "role": "lead", "gain": -14}},
 "sections": [
   {"name": "entra", "bars": 4, "chords": ["D", "A", "Bm", "G"], "parts": ["guitarra"]},
   {"name": "cuerpo", "bars": 8, "energy": 0.7, "chords": ["D", "A", "G", "A"],
    "groove": {"kick": "X.....X...X.....", "snare": "....X.......X...", "shaker": "x.x.x.x.x.x.x.x."},
    "notes": {"silbido": "r:8 @hook r:4 @hook-2 r:4 | r:8 @hook D6:4"}}]}
```

- **Parts are yours**: any name and as many as it needs. `role` (pad, keys, bass, arp,
  lead, drums, fx) only sets its mix and its patterns; a name with no role mixes as lead.
- **Notes**: `note:beats[:velocity]`, `r` rests, `+` stacks, `@motif` or `@motif+2`
  transposed, `|` is only for reading. Beats count from the section's start. A part with
  `notes` in a section plays them instead of its pattern. `[beat, length, note, velocity]`
  lists also work.
- **Section**: `bars`, `chords` (roman or names, `"Am:2"` holds two bars), optional
  `parts` (who plays; default all), `patterns`, `groove`, `notes`, `energy` (only its level).
- **Nothing is added.** No automatic fills, crashes, risers or endings: write them.
- **By default it accompanies**: low gains, space for the voice, the melody in the gaps
  between phrases. It leads only if the user asks.

**Run `--check` before rendering** and read every warning: notes clashing with the
chord, dissonance that doesn't resolve, phrases with no close, unsingable leaps, no
motif that returns, melody over the voice (`--speech`), and a score that contradicts the
brief. They're a musician's notes, not rules: fix them or keep them on purpose.

**Then listen to it against the video** and adjust from what the user says. Mix flags
work on a score without touching the notes: `--mute`, `--solo`, `--gain "bajo=-3"`,
`--instrument`, `--bpm`, `--key`, `--reverb`, `--width`, `--bright`, `--warmth`, `--lofi`,
`--sidechain`, `--hit`, `--ending`, `--stems`. Anything else: edit the score.

Write under the brief in `project.md` the key, tempo and why. It's still synthesized:
under the voice it works best. When the music carries the video (a montage, a brand
piece), tell the user it's synthesized, so they can bring their own track.

### Cutting on the beat (any track)

```
uv run ${CLAUDE_SKILL_DIR}/scripts/audio.py beats --file <track>    # → <track>.beats.json {bpm, beats, bars}
```

Times are seconds of the track. With the music in the edit at `music.at` and
starting from `music.in` of the track, a beat lands on the timeline at
`at + (beat − in)` the first time through (music loops to fill the edit: past the track's end, add its length): put cuts there (on bars for big changes). It assumes a steady
tempo: a track that speeds up or drifts (live, rubato) gets beats that slide, so
check by ear and cut on what you hear.

## 3. The user's own file

Only when the user brings it (bought, licensed, or theirs), in the project's `music/`
folder: it takes priority over everything else. If it
looks like a commercial song they may not have the rights to, say so before using it
(Content ID mutes or blocks it).

## Level

How it's mixed under the voice: SKILL.md section 5. The bed sits at ~−28 LUFS;
`--gain` on `audio.py music` nudges it (+3 louder, −3 quieter).

## Everything else (effects, b-roll, images, fonts)

- **Effects**: the synthesized set (`sfx.py`) or the user's. **B-roll and images**: the
  user's. No libraries or stock banks: the music search is only for music.
- Fonts and icons come from the open-license sets the scripts already fetch.
- **Nothing that needs a credit.** If something the user brings does, say so before
  using it.
- **Keep the source of every piece from outside in `project.md`**: if a copyright
  claim (Content ID) arrives, that's the proof.
