#!/usr/bin/env python3
"""
cut.py – split an MP4, save slices into $CLIPS_DIR (default videos/clips),
         upload each slice to S3, and print JSON list of *local* paths (for compatibility).
NEW:
  • Accepts a local path OR an HTTP(S) presigned URL as input.
  • Creates a tiny 16 kHz mono .ogg next to every slice for title generation.
  • Deletes the local .mp4 slice right after upload (unless KEEP_LOCAL_CLIPS=1).
"""
from __future__ import annotations
import argparse, math, os, subprocess, sys, json
from pathlib import Path
from shutil import which
from urllib.parse import urlparse

from s3_utils import upload_file

# ── env & paths ───────────────────────────────────────────────────────
PROJECT      = Path(__file__).resolve().parent
DEFAULT_DIR  = PROJECT / "videos" / "clips"
CLIP_DIR     = Path(os.getenv("CLIPS_DIR", DEFAULT_DIR))
CLIP_DIR.mkdir(parents=True, exist_ok=True)

S3_PREFIX = "clips/"
KEEP_LOCAL_CLIPS = os.getenv("KEEP_LOCAL_CLIPS", "0") == "1"
KEEP_OGG         = os.getenv("KEEP_OGG", "1") == "1"

# ── ffmpeg/ffprobe ───────────────────────────────────────────────────
FFMPEG_ENV = os.getenv("FFMPEG_BINARY")
FFMPEG  = FFMPEG_ENV if FFMPEG_ENV and Path(FFMPEG_ENV).exists() else "ffmpeg"
FFPROBE = Path(FFMPEG).with_name(
    "ffprobe.exe" if Path(FFMPEG).suffix.lower() == ".exe" else "ffprobe"
)
if not which(FFMPEG):
    sys.exit("⛔  ffmpeg not found – make sure it’s in the image/apt install.")

def _is_url(s: str|Path) -> bool:
    try:
        u = urlparse(str(s))
        return u.scheme in ("http", "https")
    except Exception:
        return False

def _duration(src: str|Path) -> float:
    return float(subprocess.check_output(
        [str(FFPROBE), "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(src)], text=True).strip())

def _make_ogg(src_mp4: Path) -> Path:
    ogg = src_mp4.with_suffix(".ogg")
    subprocess.check_call([
        str(FFMPEG), "-hide_banner", "-loglevel", "error",
        "-i", str(src_mp4), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
        "-y", str(ogg)
    ])
    return ogg

def split(src: str|Path, *, parts: int|None = None, interval: float|None = None) -> list[str]:
    # allow URL input (don’t Path.exists() check)
    src_pathish = Path(str(src))
    if not _is_url(src):
        if not src_pathish.exists():
            raise FileNotFoundError(src_pathish)

    total = _duration(src)
    if parts:
        seg = total / parts
    elif interval:
        parts = math.ceil(total / interval)
        seg   = interval
    else:
        raise ValueError("give --parts or --interval")

    print(f"[i] {src_pathish.name}: {total/60:.2f} min → {parts} slices × {seg:.1f}s")
    out_paths: list[str] = []

    for i in range(parts):
        start  = i * seg
        length = min(seg, total - start)
        out    = CLIP_DIR / f"{src_pathish.stem}_part{i+1}.mp4"

        cmd = [
            str(FFMPEG), "-hide_banner", "-loglevel", "error",
            "-ss", f"{start}", "-i", str(src), "-t", f"{length}",
            "-c:v", "copy",  # very fast stream copy
            "-c:a", "aac", "-b:a", "64k",
            "-y", str(out)
        ]
        subprocess.check_call(cmd)

        # (NEW) tiny audio for titles
        if KEEP_OGG:
            try:
                _make_ogg(out)
            except Exception:
                pass

        # upload to S3
        upload_file(out, S3_PREFIX + out.name)
        print(f"slice {i+1}/{parts} → {out.name} ↑ S3")

        # (NEW) free local disk
        if not KEEP_LOCAL_CLIPS:
            try:
                out.unlink(missing_ok=True)
            except Exception:
                pass

        out_paths.append(str(out))   # keep returning the same name pattern

    return out_paths

# ── CLI ───────────────────────────────────────────────────────────────
def _cli():
    p = argparse.ArgumentParser()
    p.add_argument("video")  # local path OR URL
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--parts", "-p", type=int)
    g.add_argument("--interval", "-i", type=float, help="seconds per slice")
    return p.parse_args()

if __name__ == "__main__":
    a = _cli()
    print(json.dumps(split(a.video, parts=a.parts, interval=a.interval)))
