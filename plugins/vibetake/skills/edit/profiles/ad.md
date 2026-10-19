# Profile: ad

Brand ads, UGC ads, product promos.

- **Structure**: hook (0–2 s) → problem → solution → proof → CTA. End card of
  2–3 s with logo and CTA.
- **Pacing**: a cut every 1–2 s; no dead air.
- **Cleanup**: full — fillers, retakes, long breaths.
- **Music**: energetic; cut on the hits.
- **Subtitles**: burned-in by default (in a feed it starts muted) + `.srt`.
- **Graphics**: dynamic level (`references/graphics.md`): the hook as a headline graphic, price, offer and CTA as cards; brand logo. Sound effects on every visual event.
- **Alternative hooks: offered, not made by default.** For paid campaigns, add it to
  the destination question ("two openings to test which works better?"). A variant
  must open *differently* — another line or moment of the material, its own shot and
  its own headline — never the same video with only the headline text changed (that
  tests nothing and looks identical). Every step with `--variant hookB`, starting with
  `cutlist.py --variant hookB --keep "L7,L1-…"` (SKILL.md section 11). In the delivery,
  one line per variant: what it opens with, and
  "upload both in the same ad set; the platform shows more of the one that works".
- **Other deliverables**: offer in one line 6/15/30 s cuts and other aspect ratios
  (4:5, 1:1, 16:9). If the request says it's for paid campaigns, make the ratios that
  platform needs without asking (`--variant 4x5` with its own framing).
- **Traps**: offer, price, dates and legal text are checked letter by letter
  against the request or the brief.
- **Color**: recommend Full with people on camera, Basic otherwise; brand-consistent (the brand's LUT as Custom if there is one); every version and aspect ratio shares the same grade.
- **Pace**: `cutlist.py --pace tight` when someone talks to camera; **acted ads (a
  situation, a sketch) follow `narrative.md`**: `--pace natural --fillers keep`.
