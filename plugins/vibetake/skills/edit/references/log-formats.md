# Log formats

What matters is the **log format**, not the camera: many cameras share one.
Camera models are only hints for identifying it.

## Detecting it

`ingest.py` raises "possible log" from three layers; none is enough alone:

1. **Metadata**: a log name anywhere in the tags (`material.json` → `log_hint`).
2. **Color tags**: usually useless for log — most cameras tag log as plain
   bt709. HLG/PQ are HDR, not log, and go through tone mapping instead.
3. **Image signature**: lifted blacks, no real whites, low saturation
   (`faces.image.flat`). Fog or a low-contrast scene can look the same, especially
   in 8-bit; 10-bit plus flat is a strong sign.

Confirm on the contact sheets. If the format is still unclear, ask the user which
mode they recorded in — one question, not a guess.

## Converting it

Order: **correction in log (balance, exposure as an offset) → the format's
official conversion LUT to Rec.709 → optional look → graphics on top.**
Keep 10-bit until the final master.

LUTs live in the user's folder, never in the plugin (vendor licenses usually don't
allow redistribution): `~/.vibetake/luts/<format>.cube`. If it's missing, tell the
user where the official one is published and ask for the file. Without a LUT,
offer an approximate conversion (contrast/saturation expansion) and label it as
approximate in the delivery.

## Formats

All entries are **unverified** until someone confirms them with an original file
(`scripts/camera_report.py <clip> --mode "<mode>"`, then add what it shows).

| Format | Used by (hints) | Where the official LUT is | Status |
|---|---|---|---|
| S-Log3 (S-Gamut3 / S-Gamut3.Cine) | Sony cameras, some Sony phones | Sony's LUT downloads | unverified |
| V-Log (V-Gamut) | Panasonic Lumix / Varicam | Panasonic's V-Log LUT library | unverified |
| C-Log2 / C-Log3 | Canon Cinema EOS and EOS R | Canon's LUT downloads | unverified |
| D-Log / D-Log M | DJI drones, Osmo Pocket, Osmo Action | DJI's download center for each product | unverified |
| F-Log / F-Log2 | Fujifilm X / GFX | Fujifilm's LUT downloads | unverified |
| N-Log | Nikon Z | Nikon's N-Log 3D LUT download | unverified |
| Apple Log | iPhone 15 Pro and later | Apple's Apple Log profile/LUT downloads | unverified |
| Samsung Log | recent Samsung Galaxy flagships | Samsung's LUT download | unverified |
| LogC3 / LogC4 | ARRI | ARRI's LUT generator / look library | unverified |
| Log3G10 (REDWideGamutRGB) | RED | RED's IPP2 output LUTs | unverified |
| Blackmagic Film (Gen 4/5) | Blackmagic cameras | bundled with DaVinci Resolve | unverified |

When a format is verified, replace "unverified" with what identifies it reliably
(metadata key and value, bit depth) and the camera used to verify it.
