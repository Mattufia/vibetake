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
- Loudness by destination: see `render.py --loudness` (social −14 LUFS / −1 dBTP).

Sources (October 2026): learn.headliner.app (Social Media Video Length Limits 2026),
pod2reels.com, tryordinal.com, zeely.ai, blitzcutai.com.
