# Montage to music (no speech needed)

For what isn't built from lines: an aftermovie, a place, a product, a trip — or a stretch of
a spoken video (an intro, a closing). The picture gives the moments, the music gives the time.

```
uv run ${CLAUDE_SKILL_DIR}/scripts/montage.py moments [--files a.mp4,b.mp4] [--max 6]
uv run ${CLAUDE_SKILL_DIR}/scripts/montage.py build --order "M3,M12,M7-M9" --music music/<track> [--in 12.0]
                    [--pace auto|fast|slow] [--where only|start|end] [--sound mute|keep] [--format vertical]
uv run ${CLAUDE_SKILL_DIR}/scripts/montage.py clear
```

## 1. Moments

`moments` splits every clip at its shot changes (ingest found them) and a long shot at its
calmest points (no moment past `--max`, 6 s), and finds each moment's action peak (where the
picture changes most). It writes `02-work/montage/sheet_NN.jpg` — a row per moment: start ·
peak · end, with its id — and `moments.md`.

**Look at every sheet before choosing.** You decide what's good, not a score. The notes in
`moments.md` ("softer", "shakier", "darker", "brighter", "face") only compare a moment with the
rest of its own clip: a hint of where to look, never a reason by itself (a dark or moving shot
can be the intent). Between two moments that show the same thing, keep the better one.

## 2. The order is the edit

Like choosing lines: an arc that follows the music — an opening that sets the place, a build,
the strongest shots on the peak, a close that lands. Vary distance and movement from one shot to
the next (wide → close, still → moving); never two near-identical shots in a row. A moment can
come back (`M3` twice) when it means something. Ranges work: `M7-M9`, `M9-M7`.

## 3. The music sets the time

Choose the track first (`references/music.md`: real tracks for a montage). `build` reads its
beats and bars (`audio.py beats`, made on the fly) and, where each shot falls, its loudness:

| Track there | Shot length (`--pace auto`) |
|---|---|
| loud (≥1.2× its median) | 2 beats |
| medium | a bar (4 beats) |
| quiet (<0.8×) | two bars (8 beats) |

`--pace fast` halves it, `slow` doubles it. A shot is never longer than its moment (it takes
fewer beats instead); a moment shorter than one beat is left out with a warning. Every cut lands
on a beat (shots of a bar or more on a bar), frame-exact; each shot shows the stretch around its
action peak. `--in` = where in the track the montage starts (snapped to a beat: start it where
the track has energy, or on its intro on purpose). If the track runs out, the rest of the list
is left out and it says so.

- `--where only`: the whole edit is the montage (a spoken edit.json is kept as
  `edl.before-montage.json`). `start` / `end`: a block before or after the spoken edit; its
  music comes in with it and fades out over the first 1.5 s of speech; cutlist.py re-cuts keep
  the block. Build again to change it; `clear` takes it out.
- `--sound mute` (default): room tone under the music. `keep`: the place's own sound 12 dB down,
  as an accent (a crowd, the sea). The montage's music leads: it isn't ducked.
- After `build`: `reframe.py` (framing, a vertical from horizontal shots), then
  `render.py --preview` — **watch it with sound**: a cut that misses the beat is very visible.
  Effects (speed ramps, a whip on a drop, `stabilize` on a shot you keep): fields of the
  segments, `references/effects.md`.
