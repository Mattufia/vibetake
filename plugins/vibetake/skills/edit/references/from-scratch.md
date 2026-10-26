# Generating video from scratch (no filmed material)

Pure motion graphics — an animated reel, a card, a visual explainer:

- **Canvas + ffmpeg, not Remotion.** An HTML page with a deterministic `frame(t)`
  function, a headless browser over CDP, and ffmpeg encoding according to the
  master rule (SKILL.md, `references/platforms.md`), with `-pix_fmt yuv420p -movflags +faststart`. Average
  2–3 temporal subsamples per frame for real motion blur. It's faster to render
  and simpler to reason about than the same thing in Remotion.
- **Remotion from scratch only if** there's a series (the same mold with different
  data), the user will touch it in Studio, or a Remotion skill does the heavy part
  (a map animation → `remotion-maps`, a 3D scene → `remotion-markup`). Then load
  `remotion-best-practices` and the skill it routes to. A single 3D object (logo,
  word, icon, `.glb`) is already in the kit: `"type": "3d"` (`graphics.md`).
- **Blender only for what Three.js can't fake**: a real-looking material (glass,
  liquid, brushed metal, cloth) or an animated model with its own rig. Not for an
  extruded logo or a turning word — that's the kit. If `blender` is on the PATH:
  `blender -b -P scene.py` with the times read from `cues.json`, rendering PNGs with
  alpha (`film_transparent`) to `02-work/blender/`, then used as a layer or b-roll.
  EEVEE by default (seconds a frame); Cycles only with a GPU and a short shot. If it
  isn't installed, say so and offer the kit's 3D instead — don't install it unasked.
- Synthesized audio (hits, dings, backing music): float32 WAV at 48 kHz, mixed
  separately.
