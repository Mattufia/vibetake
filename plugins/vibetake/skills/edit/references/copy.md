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

- **Chapters**: from `graphics.py cues` (each line's timeline start), grouped by topic;
  the first at 0:00.
- **Hashtags**: specific to the topic and the audience; no generic filler (#fyp,
  #viral).
- No claims the video doesn't make; nothing that needs a disclaimer the video doesn't
  have (prices, health, finance) without the user's say.
