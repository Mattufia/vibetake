# GPU, transcription and parallel work

The scripts pick GPU or CPU on their own (the machine profile in
`~/.vibetake/machine.json` says what's there); don't assume any GPU brand.

**Transcription**: the large model on CUDA, or **the same model on CPU** (int8)
without a GPU — never a smaller one: the transcript decides where the cuts go, so a
worse transcript is a worse edit. Always pass the language explicitly (auto-detection
looks at the first 30 seconds; an intro with music or silence ruins it).

**Parallelize by real contention, not by a fixed rule.** Two heavy GPU jobs, in
series. One GPU job and one CPU job that don't step on each other, in parallel. Light
tasks, all together. Ingest is the typical case where it pays to launch everything at
once.

**Encoding**: the master on the GPU's encoder after a short test, x264 otherwise —
settings and why in `platforms.md` (Export settings). Say in the delivery what the
master was encoded with.

## CUDA libraries for transcription (NVIDIA)

faster-whisper needs cuBLAS and cuDNN, which come as pip wheels (`nvidia-cublas-cu12`,
`nvidia-cudnn-cu12`). Installed inside an isolated tool environment, Windows doesn't
find their DLLs and transcription fails with `cublas64_12.dll is not found`.
`transcribe.py` handles it: with an NVIDIA GPU and the wheels missing, it runs itself
again through uv with them added, and puts their `bin` folders on the DLL path of its
own process (never the global PATH). Called by hand, by ingest or by QA, it's the same.
If it still fails, check `nvidia-smi` and the driver before blaming the script.
