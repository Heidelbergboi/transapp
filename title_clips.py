#!/usr/bin/env python3
"""
title_clips.py – generate Albanian titles for clips in $CLIPS_DIR.

USAGE
  # old behavior (all clips)
  python title_clips.py

  # new: only these files (basenames that live in $CLIPS_DIR)
  python title_clips.py --files a_part1.mp4 a_part2.mp4 ...
"""
from __future__ import annotations

import argparse
import csv
import datetime
import logging
import os
import subprocess
import sys
from pathlib import Path
from shutil import which
from typing import Iterable, List, Tuple

import requests
from dotenv import load_dotenv

# ── env & paths ───────────────────────────────────────────────────────
BASE        = Path(__file__).resolve().parent
DEFAULT_DIR = BASE / "videos" / "clips"
CLIPS_DIR   = Path(os.getenv("CLIPS_DIR", DEFAULT_DIR))
CLIPS_DIR.mkdir(parents=True, exist_ok=True)

LOG_DIR     = BASE / "logs"
LOG_DIR.mkdir(exist_ok=True)

FFMPEG      = os.getenv("FFMPEG_BINARY") or which("ffmpeg") or "ffmpeg"
KEEP_OGG    = str(os.getenv("KEEP_OGG", "1")).lower() not in {"0", "false", "no"}

OPENAI_KEY              = os.getenv("OPENAI_API_KEY", "")
OPENAI_TRANSCRIBE_MODEL = os.getenv("OPENAI_TRANSCRIBE_MODEL", "whisper-1")
OPENAI_CHAT_MODEL       = os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini")

logging.basicConfig(
    level=logging.INFO,
    format="INFO title → %(message)s",
)

# ── helpers ───────────────────────────────────────────────────────────
def _shell(cmd: List[str]) -> None:
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"Command failed ({p.returncode}): {' '.join(cmd)}\n{p.stdout}")

def _to_ogg(mp4: Path) -> Path:
    ogg = mp4.with_suffix(".ogg")
    if ogg.exists():
        return ogg
    # mono 16 kHz, opus, small bitrate – good for transcription
    _shell([
        FFMPEG, "-hide_banner", "-loglevel", "error",
        "-y", "-i", str(mp4),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "libopus", "-b:a", "48k",
        str(ogg),
    ])
    return ogg

def _openai_transcribe(ogg: Path) -> str:
    if not OPENAI_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")
    with ogg.open("rb") as f:
        files = {"file": (ogg.name, f, "audio/ogg")}
        data = {"model": OPENAI_TRANSCRIBE_MODEL}
        r = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {OPENAI_KEY}"},
            files=files,
            data=data,
            timeout=300,
        )
    r.raise_for_status()
    js = r.json()
    # whisper-style returns {"text": "..."}
    return js.get("text") or js

def _openai_title(transcript: str) -> str:
    if not OPENAI_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")
    payload = {
        "model": OPENAI_CHAT_MODEL,
        "messages": [
            {"role": "system", "content": "Ti je një redaktor i zgjuar videosh. Kthe një titull të shkurtër, tërheqës dhe shqip bazuar në fragmentin e dhënë."},
            {"role": "user", "content": transcript[:6000]},
        ],
        "temperature": 0.3,
        "max_tokens": 64,
    }
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=300,
    )
    r.raise_for_status()
    js = r.json()
    return js["choices"][0]["message"]["content"].strip()

def _title_for_file(mp4: Path) -> Tuple[str, str]:
    logging.info("processing %s", mp4.name)
    ogg = _to_ogg(mp4)
    transcript = _openai_transcribe(ogg)
    title = _openai_title(transcript)
    if not KEEP_OGG:
        try:
            ogg.unlink(missing_ok=True)
        except Exception:
            pass
    return (mp4.name, title)

# ── main ──────────────────────────────────────────────────────────────
def _parse() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--files", nargs="*", default=None,
        help="Optional list of clip *basenames* (e.g. file_part1.mp4) to process from $CLIPS_DIR. "
             "If omitted, all *.mp4 are processed."
    )
    return p.parse_args()

def main() -> None:
    args = _parse()
    # decide set of clips
    if args.files:
        candidates = [CLIPS_DIR / f for f in args.files]
    else:
        candidates = sorted(CLIPS_DIR.glob("*.mp4"))

    rows: List[Tuple[str, str]] = []
    for mp4 in candidates:
        if not mp4.exists():
            # skip silently if the clip was cleaned or never existed locally
            continue
        rows.append(_title_for_file(mp4))

    ts  = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out = LOG_DIR / f"clip_titles_{ts}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([("file", "title"), *rows])
    print("[OK] Titles saved ->", out)

if __name__ == "__main__":
    main()
