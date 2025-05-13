#!/usr/bin/env python3
"""
download.py  – grab highest-quality YouTube video + audio
---------------------------------------------------------

Usage:
    python download.py <youtube-url>

The merged MP4 lands in   videos/full/<videoID>.mp4
Requires:
    • yt-dlp  (pip install yt-dlp)
    • ffmpeg  (already unzipped in ffmpeg 7.1.1\bin)
"""
from __future__ import annotations
import subprocess, sys, re, os
from pathlib import Path
from dotenv import load_dotenv

# ── 0.  env vars ────────────────────────────────────────────
BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")                       # load if present

FFMPEG_BIN = os.getenv("FFMPEG_BINARY") or str(BASE / "ffmpeg 7.1.1" / "bin" / "ffmpeg.exe")
DEST_DIR   = BASE / "videos" / "full"
DEST_DIR.mkdir(parents=True, exist_ok=True)

# ── 1.  Helpers ─────────────────────────────────────────────
_YT_ID = re.compile(r"(?:v=|\/|be/)([0-9A-Za-z_-]{11})")
def video_id(url: str) -> str:
    m = _YT_ID.search(url)
    if not m:
        raise ValueError("Could not parse a YouTube video ID from that URL.")
    return m.group(1)

def download_hd(url: str) -> Path:
    output_tpl = str(DEST_DIR / "%(id)s.%(ext)s")
    cmd = [
        "yt-dlp",
        "-f", "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]",  # best video-only + best audio-only
        "--remux-video", "mp4",                      # merge → mp4 (fast, no re-encode)
        "--ffmpeg-location", FFMPEG_BIN,             # point at our local ffmpeg
        "-o", output_tpl,
        url,
    ]
    print(" ".join(cmd))        # show exact command for debugging
    subprocess.check_call(cmd)
    return DEST_DIR / f"{video_id(url)}.mp4"

# ── 2.  CLI ─────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python download.py <youtube-url>")
    try:
        mp4 = download_hd(sys.argv[1])
        print(f"\n[✓] Saved → {mp4}")
    except subprocess.CalledProcessError as e:
        sys.exit(f"yt-dlp / ffmpeg failed – {e}")
