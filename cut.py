#!/usr/bin/env python3
"""
cut.py  –  loss-less split of an MP4 into equal parts

Example CLI
-----------
python cut.py video.mp4 --parts 4       # 4 proportional slices
python cut.py video.mp4 --interval 120  # slices of 120 s each
"""
from __future__ import annotations
import argparse, math, os, subprocess, sys
from pathlib import Path
from typing import Tuple

# ── find ffmpeg / ffprobe ──────────────────────────────────────────────
FFMPEG_BIN = os.getenv("FFMPEG_BINARY", "ffmpeg")

def probe_bin() -> str:
    """Return a working ffprobe executable."""
    if Path(FFMPEG_BIN).is_file():
        p = Path(FFMPEG_BIN)
        candidate = p.with_name("ffprobe.exe" if p.name.endswith(".exe") else "ffprobe")
        if candidate.exists():
            return str(candidate)
    # fall back to whichever ffprobe is on PATH
    return "ffprobe"

FFPROBE_BIN = probe_bin()

# ── console encoding safety (→ arrow) ──────────────────────────────────
try:
    sys.stdout.reconfigure(encoding="utf-8")
    ARROW = "→"
except Exception:
    ARROW = "->"

# ── paths ──────────────────────────────────────────────────────────────
BASE = Path(__file__).resolve().parent
CLIP_DIR = BASE / "videos" / "clips"
CLIP_DIR.mkdir(parents=True, exist_ok=True)

# ── helpers ────────────────────────────────────────────────────────────
def video_duration(src: Path) -> float:
    """Return duration in seconds using ffprobe."""
    cmd = [FFPROBE_BIN, "-v", "error",
           "-show_entries", "format=duration",
           "-of", "default=nk=1:nw=1", str(src)]
    out = subprocess.check_output(cmd, text=True).strip()
    return float(out)

def make_slice(src: Path, start: float, end: float, idx: int) -> str:
    """Copy-codec slice via ffmpeg; return status message."""
    out_file = CLIP_DIR / f"{src.stem}_part{idx+1}.mp4"
    cmd = [FFMPEG_BIN,
           "-ss", f"{start}", "-to", f"{end}",
           "-i", str(src),
           "-c", "copy", "-avoid_negative_ts", "make_zero",
           "-y", str(out_file)]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    return f"  • pjesa {idx+1} {ARROW} {out_file.name}"

def fast_cut(src: Path, parts: int | None = None, interval: float | None = None):
    if bool(parts) == bool(interval):
        raise ValueError("Specify exactly one of --parts OR --interval.")

    total = video_duration(src)     # seconds
    if parts:
        interval = total / parts
    else:
        parts = math.ceil(total / interval)

    print(f"[i] {src.name}: {total/60:.2f} min {ARROW} {parts} pjesë × {interval:.1f}s")
    for idx in range(parts):
        start, end = idx*interval, min((idx+1)*interval, total)
        print(make_slice(src, start, end, idx))
    print(f"[✓] Klipet ruhen në  {CLIP_DIR}")

# ── CLI ----------------------------------------------------------------
def _cli() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Split MP4 into equal parts.")
    p.add_argument("video", type=Path)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--parts", "-p", type=int, help="numri i pjesëve")
    g.add_argument("--interval", "-i", type=float, help="sekonda çdo pjesë")
    return p.parse_args()

if __name__ == "__main__":
    a = _cli()
    fast_cut(a.video.resolve(), parts=a.parts, interval=a.interval)
