# /// script
# requires-python = ">=3.10,<3.13"
# dependencies = ["rembg>=2.0.60", "onnxruntime-gpu==1.22.0; sys_platform != 'darwin'",
#                 "onnxruntime>=1.18; sys_platform == 'darwin'", "numpy>=1.26", "pillow>=10"]
# ///
"""Mask of the person in a video, frame by frame (rembg u2net_human_seg): white = person.

    uv run matte.py --in segment.mkv --out mask.mkv

render.py calls it for segments marked "behind": true (text behind the person) and keeps the
mask in 02-work/matte/. ~0.1 s a frame on an NVIDIA GPU, ~0.8 s on the CPU (a 5 s segment at
30 fps: half a minute vs two minutes). The first run downloads the model (~170 MB).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _common import tool  # noqa: E402  (finds ffmpeg/uv through ~/.vibetake/machine.json when PATH doesn't)

CUDA_WHEELS = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12>=9,<10", "nvidia-cuda-runtime-cu12", "nvidia-cufft-cu12"]
SIDE = 640           # masks are made at this size (u2net looks at 320 px anyway); render.py scales them up


def ensure_cuda_libs() -> None:
    """Same as transcribe.py: with an NVIDIA GPU, run again with the CUDA wheels if they're missing."""
    if os.environ.get("VIBETAKE_CUDA_REEXEC") or not shutil.which("nvidia-smi") or not tool("uv", required=False):
        return
    try:
        import nvidia.cublas  # noqa: F401
        import nvidia.cudnn  # noqa: F401
        import nvidia.cufft  # noqa: F401
        return
    except ImportError:
        pass
    cmd = [tool("uv"), "run"] + [x for w in CUDA_WHEELS for x in ("--with", w)]
    env = {**os.environ, "VIBETAKE_CUDA_REEXEC": "1"}
    sys.exit(subprocess.run([*cmd, str(Path(__file__).resolve()), *sys.argv[1:]], env=env).returncode)


def add_cuda_dlls() -> None:
    if os.name != "nt":
        return
    for base in map(Path, sys.path):
        for b in (base / "nvidia").glob("*/bin") if (base / "nvidia").is_dir() else []:
            os.add_dll_directory(str(b))
            os.environ["PATH"] = f"{b}{os.pathsep}{os.environ['PATH']}"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ensure_cuda_libs()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    add_cuda_dlls()
    import numpy as np
    from PIL import Image
    from rembg import new_session, remove

    ff = tool("ffmpeg")
    probe = subprocess.run([tool("ffprobe"), "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height,avg_frame_rate", "-of", "csv=p=0", args.src],
                           capture_output=True, text=True, check=True).stdout.strip().split(",")
    w, h, rate = int(probe[0]), int(probe[1]), probe[2]
    k = min(1.0, SIDE / max(w, h))
    mw, mh = round(w * k / 2) * 2, round(h * k / 2) * 2
    sess = new_session("u2net_human_seg", providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
    gpu = "CUDAExecutionProvider" in sess.inner_session.get_providers()
    print(f"matte: {Path(args.src).name} on the {'GPU' if gpu else 'CPU (slow: ~0.8 s a frame)'}", flush=True)
    dec = subprocess.Popen([ff, "-v", "error", "-i", args.src, "-vf", f"scale={mw}:{mh}", "-f", "rawvideo",
                            "-pix_fmt", "rgb24", "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen([ff, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{mw}x{mh}",
                            "-r", rate, "-i", "-", "-c:v", "ffv1", args.out], stdin=subprocess.PIPE)
    n, t0 = 0, time.time()
    while True:
        buf = dec.stdout.read(mw * mh * 3)
        if len(buf) < mw * mh * 3:
            break
        img = Image.fromarray(np.frombuffer(buf, np.uint8).reshape(mh, mw, 3))
        enc.stdin.write(np.asarray(remove(img, session=sess, only_mask=True).convert("L")).tobytes())
        n += 1
    enc.stdin.close()
    if enc.wait() or dec.wait():
        sys.exit("matte: ffmpeg failed")
    print(f"matte: {n} frames in {time.time() - t0:.0f} s → {args.out}")


if __name__ == "__main__":
    main()
