# Copy to publish with

`03-delivery/copy.txt`, ready to paste: one `## <platform>` section per platform it's
published on (the same names as `qa.py --platform`). Written from the transcript, in
the video's language and the brand's tone. Names, numbers and dates checked like the
subtitles. `qa.py` checks the sections and the limits below.

```
## youtube
Title: How I edit a whole video with AI in 10 minutes
The two lines that show before "more": what the viewer gets.
…
0:00 Intro
1:12 Ingest
4:30 Color

## reels
First line that hooks (it's all that shows before "more")
2–4 short lines with the substance.
A call to action: comment, save, follow.
#tag1 #tag2 #tag3

## tiktok
…
```

| Platform | What | Limits (October 2026 — verify, they change) |
|---|---|---|
| Reels | caption: hook line, 2–4 lines, call to action, 3–5 hashtags | 2,200 characters |
| TikTok | same shape, shorter | 4,000 characters |
| YouTube | `Title:` ≤60 characters (search cuts the rest; 100 max), description with what matters in the first 2 lines, **chapters from 0:00** if it runs past ~5 minutes (at least 3, in order) | description 5,000 |
| Shorts | `Title:` ≤100 characters, a line or two | |

- **Chapters**: one per topic, where its first line is said (`graphics.py cues` lists
  the lines): `deliver.py chapter --line L5 --title "Pricing"` (or `--cut N`: the cut
  before segment N, counted from 0), anchored to the source so re-cuts move them.
  `deliver.py chapters` prints the list and warns about what YouTube refuses (fewer than
  3, or one under 10 s, the last one included); the first shows at 0:00.
  render.py writes them into the MP4 and into `<video>.chapters.txt`: paste that into
  the description.
- **Hashtags**: specific to the topic and the audience; no generic filler (#fyp,
  #viral).
- No claims the video doesn't make; nothing that needs a disclaimer the video doesn't
  have (prices, health, finance) without the user's say.

## GIF

When asked (a reaction, a preview for a README or a chat):
`uv run ${CLAUDE_SKILL_DIR}/scripts/deliver.py gif --from 12.5 --to 16 [--width 480]` — a
stretch of the latest version, looping, next to it; keep it under ~10 s.
