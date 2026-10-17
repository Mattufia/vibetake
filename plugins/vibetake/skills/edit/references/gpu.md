# GPU, transcription models and encoding


**Prioritize the GPU where it helps, falling back to CPU without drama.** If
there's a GPU, use it to raise quality too, not just to go faster: larger
transcription models, accelerated rendering, higher-resolution previews.

**Transcription**: with a GPU, a large model. Without a GPU, the same model size
on an optimized runtime (`faster-whisper` in int8) rather than dropping model
size: here the transcript defines where the cuts go, so degrading it degrades the
edit, not just the text. Drop model size only as a last resort, and say so.
Choose the size according to available VRAM. **Always set the language
explicitly**: auto-detection looks at the first 30 seconds and an intro with
music or silence ruins everything.

**Parallelize by real contention, not by a fixed rule.** Two heavy GPU jobs, in
series. One GPU job and one CPU job that don't step on each other, in parallel.
Light tasks, all together. Ingest is the typical case where it pays to launch
everything at once.

**Final master: GPU by default, at high quality; CPU as fallback.** Platforms
re-encode everything: what defines final quality is uploading a file with quality
to spare, not which encoder made it.

- Use the hardware encoder of whatever GPU is present, in **constant quality**
  mode (not fixed bitrate) and with its highest-quality preset. Check the options
  on that machine (`ffmpeg -h encoder=<name>`) instead of assuming them.
- **Test it first with a few seconds**: having a GPU doesn't guarantee the encoder
  works (drivers, disabled card, ffmpeg build without support).
- **CPU (x264, slow preset, low CRF)** if there's no GPU or the test fails, or
  when file size matters: at equal size, x264 gives more quality.
- Intermediates and previews, always on the GPU if there is one.
- State in the delivery what the master was encoded with.


## CUDA libraries for transcription (NVIDIA)

faster-whisper needs cuBLAS and cuDNN, which come as pip wheels (`nvidia-cublas-cu12`,
`nvidia-cudnn-cu12`). Installed inside an isolated tool environment, Windows doesn't
find their DLLs and transcription fails with `cublas64_12.dll is not found`.
`transcribe.py` handles it: with an NVIDIA GPU and the wheels missing, it runs itself
again through uv with them added, and puts their `bin` folders on the DLL path of its
own process (never the global PATH). Called by hand, by ingest or by QA, it's the same.
If it still fails, check `nvidia-smi` and the driver before blaming the script.
