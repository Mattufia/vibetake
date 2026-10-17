# Ingest warnings: what they mean, what to do

What `ingest.py` already handles, so it isn't redone by hand:

- **VFR** → CFR copy in `01-ingest/normalized/` (otherwise the cuts drift). The
  dominant frame interval sets the rate, not the average.
- **HDR** (HLG/PQ, typical of phones) → SDR Rec.709 copy, tone-mapped (libplacebo
  BT.2390, or zscale + hable) with HLG reference white (203 nits) mapped to SDR
  white and perceptual gamut mapping. Edit decisions use the copy; **any color
  grade renders from the original** (`render.py` applies the same tone mapping as
  a pre-filter). Exception: the delivery is meant to be HDR — then don't convert.
- **Rotation** metadata → real orientation reported.
- **Several audio tracks** → level and noise per track, suggested best track. If
  it isn't track 1, redo the transcript:
  `uv run ${CLAUDE_SKILL_DIR}/scripts/transcribe.py <file> --lang <code> --track <n>`
  and put `"track": n` on that file's segments in `edl.json`.
- **Audio recorded separately** (audio-only files in the folder) → offset to place
  it on the video timeline (`material.md` → External audio).
- **Possible log footage** → flagged from metadata or from a flat image. Confirm
  it on the contact sheets, then read `log-formats.md` for the conversion.
- **Faces** every 0.5 s in `01-ingest/faces.json` (x, y, w, h, score; 0–1): used
  to place subtitles and overlays, to measure skin, and by `reframe.py`.
- **Files in recording order** (metadata creation time, else name); the transcript
  treats them as one sequence, so a retake across files is still found.
