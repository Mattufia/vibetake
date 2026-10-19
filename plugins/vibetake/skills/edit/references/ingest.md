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
- **Several audio tracks** (a wireless mic on track 2) → the clearest one is picked
  before transcribing, and the transcript, the cut and the cleanup use it. A warning
  says when the whole-file analysis disagrees (`transcribe.py <file> --track-for
  <file>=<n>` to change it).
- **Channels**: a mic recorded on one channel only, or one mic per person on left and
  right → flagged; the cleanup (and a render without it) puts the voice in the center.
- **Audio recorded separately** (a recorder, a wireless mic that records) → each video
  is found inside the separate files (full length: one long recording can cover
  several clips), with its offset and the drift of the two clocks. The cleanup uses
  that sound for the video, aligned (`audio.py clean --no-external` keeps the
  camera's); the transcript stays on the camera audio, which has the same timing.
- **Transcripts are kept**: running ingest again only transcribes new or changed files
  (same language, glossary and track → skipped; `transcribe.py --force` redoes it).
- **Possible log footage** → flagged from metadata or from a flat image. Confirm
  it on the contact sheets, then read `log-formats.md` for the conversion.
- **Faces** every 0.5 s in `01-ingest/faces.json` (x, y, w, h, score; 0–1): used
  to place subtitles and overlays, to measure skin, and by `reframe.py`.
- **Files in recording order** (metadata creation time, else name); the transcript
  treats them as one sequence, so a retake across files is still found.
