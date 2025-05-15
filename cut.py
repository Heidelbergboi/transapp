#!/usr/bin/env python3
"""
cut.py  –  loss-less split of an MP4 into equal parts

Example CLI
-----------
python cut.py video.mp4 --parts 4       # 4 proportional slices
python cut.py video.mp4 --interval 120  # slices of 120 s each
"""
import argparse
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

# ── ENSURE UTF-8 (FALLBACK FOR WINDOWS) ─────────────────────────────────
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# ── CONFIGURE FFMPEG / FFPROBE ─────────────────────────────────────────
FFMPEG_BIN = os.getenv("FFMPEG_BINARY", "ffmpeg")
_ffmpeg_path = Path(FFMPEG_BIN)
if _ffmpeg_path.name.lower().startswith("ffmpeg") and _ffmpeg_path.exists():
    ffprobe_name = "ffprobe.exe" if _ffmpeg_path.suffix.lower() == ".exe" else "ffprobe"
    FFPROBE_BIN = str(_ffmpeg_path.with_name(ffprobe_name))
else:
    FFPROBE_BIN = os.getenv("FFPROBE_BINARY", "ffprobe")


def video_duration(path: Path) -> float:
    """
    Returns duration in seconds, or raises if ffprobe
    can’t parse it (handles the ‘N/A’ case).
    """
    cmd = [FFPROBE_BIN, "-v", "error",
           "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1",
           str(path)]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out = proc.stdout.strip()
    if not out or out == "N/A":
        raise RuntimeError(f"Could not determine duration of {path}: '{out}'")
    try:
        return float(out)
    except ValueError:
        raise RuntimeError(f"Invalid duration '{out}' for {path}")


def fast_cut(
    src: Path,
    parts: Optional[int] = None,
    interval: Optional[float] = None
) -> None:
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(f"Source not found: {src}")
    total = video_duration(src)
    if parts:
        seg = total / parts
    elif interval:
        parts = math.ceil(total / interval)
        seg = interval
    else:
        raise ValueError("Must supply either --parts or --interval")

    clip_dir = src.parent.parent / "clips"
    clip_dir.mkdir(parents=True, exist_ok=True)

    print(f"[i] {src.name}: {total/60:.2f} min -> {parts} pjesë x {seg:.1f}s")
    base, ext = src.stem, src.suffix
    for i in range(parts):
        start = i * seg
        length = min(seg, total - start)
        out = clip_dir / f"{base}_part{i+1}{ext}"
        print(f" • pjesa {i+1} -> {out.name}")
        cmd = [
            FFMPEG_BIN,
            "-hide_banner", "-loglevel", "error",
            "-ss", str(start),
            "-i", str(src),
            "-t", str(length),
            "-c", "copy",
            str(out)
        ]
        subprocess.run(cmd, check=True)

    print(f"[OK] Klipet ruhen në {clip_dir}")


def _cli():
    p = argparse.ArgumentParser(description="Split MP4 into equal parts.")
    p.add_argument("video", type=Path)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--parts", "-p", type=int, help="numri i pjesëve")
    g.add_argument("--interval", "-i", type=float, help="sekonda çdo pjesë")
    return p.parse_args()

if __name__ == "__main__":
    args = _cli()
    fast_cut(args.video.resolve(), parts=args.parts, interval=args.interval)
