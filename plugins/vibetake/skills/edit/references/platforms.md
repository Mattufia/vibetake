# Platforms: specs and limits

**These change often.** Values below as reported in October 2026 by third-party
guides (not the platforms' own help pages); `qa.py` checks against them. Verify on
the platform's help page when a limit matters, and update this table and
`PLATFORMS` in `scripts/qa.py` together.

| Platform (`--platform`) | Aspect / size | Max length | Notes |
|---|---|---|---|
| `tiktok` | 9:16, 1080×1920 | 60 min uploaded (10 min recorded in the app) | |
| `reels` (Instagram) | 9:16, 1080×1920 | 3 min (longer uploads rolling out, per account) | grid preview crops the center (see `safe-margins.md`) |
| `shorts` (YouTube) | 9:16, 1080×1920 | 3 min | longer vertical videos become regular videos |
| `youtube` | 16:9, 1920×1080 (or more) | 15 min until the account is verified, then 12 h / 256 GB | thumbnail 1280×720 |
| `x` | 16:9 or 1:1 or 9:16 | 2 min 20 s (free accounts) | unverified |
| `linkedin` | 16:9, 1:1, 4:5 or 9:16 | 10 min | unverified |

**A platform that isn't here** (Twitch, Vimeo, a client with its own specs…): look
up its current specs before exporting — resolution, aspect, fps, codec, max length
and size, loudness, the interface's safe zone — and say in the delivery where they
came from. No clear data: apply the general rules below and say so.

## For every platform

- **Native frame rate**: shot at 24, 25 or 30 → delivered at that. Converting 24 → 30
  stutters. 60 only if the material is 60 and the motion earns it.
- **SDR BT.709, never HDR**: Instagram and TikTok handle HDR badly (washed out or
  blown). `render.py` tone-maps HDR sources and tags BT.709.
- **Upload with quality to spare**: every platform re-encodes. YouTube gives uploads
  of 1440p and up its better codecs (VP9/AV1) — they look better even when watched at
  1080p — so a 1080p edit for YouTube goes out with `render.py --upload-height 1440`
  (lanczos; AI upscaling only to recover detail). Instagram compresses hard and keeps
  "Upload at highest quality" off by default; TikTok has its own HD-upload switch.
  `qa.py` lists these reminders at the end of its report.

## Export settings (what `render.py` writes)

- H.264 High, yuv420p 8-bit, constant frame rate, BT.709 tags (primaries,
  transfer, matrix, tv range), AAC 320 kb/s 48 kHz stereo, `+faststart` (playback
  starts before the whole file downloads).
- **Master**: the GPU's hardware encoder at constant quality with its best preset —
  NVENC `-preset p7 -tune hq -rc vbr -cq 18`, VideoToolbox `-q:v 70`, QSV
  `-global_quality 18`, AMF `-rc cqp -qp 18` — tested on a short clip first; if none
  works, x264 `-preset slow -crf 17`. Platforms re-encode everything: what decides
  final quality is uploading with quality to spare. When file size matters, x264
  gives more quality per megabyte.
- **Preview**: 540p, fastest preset, CQ/CRF 28.
- Loudness by destination (`render.py --loudness`):

  | `--loudness` | Integrated | True peak |
  |---|---|---|
  | `social` (TikTok, Reels, Shorts, YouTube) | −14 LUFS | −1 dBTP |
  | `podcast` | −16 LUFS | −1 dBTP |
  | `broadcast-eu` / `broadcast-us` (TV ads: confirm with the station) | −23 / −24 | −2 dBTP |

Sources (October 2026): learn.headliner.app (Social Media Video Length Limits 2026),
pod2reels.com, tryordinal.com, zeely.ai, blitzcutai.com.
