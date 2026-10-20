# Generating video from scratch (no filmed material)

Pure motion graphics — an animated reel, a card, a visual explainer:

- **Canvas + ffmpeg, not Remotion.** An HTML page with a deterministic `frame(t)`
  function, a headless browser over CDP, and ffmpeg encoding according to the
  master rule (SKILL.md, `references/platforms.md`), with `-pix_fmt yuv420p -movflags +faststart`. Average
  2–3 temporal subsamples per frame for real motion blur. It's faster to render
  and simpler to reason about than the same thing in Remotion.
- **Remotion from scratch only if** there's a series (the same mold with different
  data), the user will touch it in Studio, or a Remotion skill does the heavy part
  (a map animation → `remotion-maps`, 3D → `remotion-markup`). Then load
  `remotion-best-practices` and the skill it routes to.
- Synthesized audio (hits, dings, backing music): float32 WAV at 48 kHz, mixed
  separately.
