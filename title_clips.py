#!/usr/bin/env python3
"""
title_clips.py
──────────────
Iterate over each .mp4 in videos/clips, be sure audio exists (add silence
if needed), send to GPT-4o mini Transcribe, ask GPT-3.5 for a short Albanian
title, save results to logs/clip_titles_<timestamp>.csv.
"""

from __future__ import annotations
import csv
import os
import subprocess
import tempfile
import sys
import datetime
from pathlib import Path

import dotenv
import openai
from tqdm import tqdm

# ── console (ASCII fallback) ────────────────────────────────────────────
try:
    sys.stdout.reconfigure(encoding="utf-8")
    ARROW, WARN = "->", "WARNING"
except Exception:
    ARROW, WARN = "->", "WARNING"

# ── paths & env ─────────────────────────────────────────────────────────
BASE    = Path(__file__).resolve().parent
CLIPS   = BASE / "videos" / "clips"
LOG_DIR = BASE / "logs"
CLIPS.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

dotenv.load_dotenv(BASE / ".env", override=True)

# ── ffmpeg / ffprobe ────────────────────────────────────────────────────
FFMPEG = os.getenv("FFMPEG_BINARY", "ffmpeg")
def ffprobe_bin() -> str:
    p = Path(FFMPEG)
    probe = p.with_name("ffprobe.exe" if p.suffix.lower()==".exe" else "ffprobe")
    return str(probe) if probe.exists() else "ffprobe"
FFPROBE = ffprobe_bin()

# ── OpenAI setup ────────────────────────────────────────────────────────
openai.api_key = os.getenv("OPENAI_API_KEY", "")
if not openai.api_key:
    sys.exit(f"{WARN}: OPENAI_API_KEY not set in environment")

SYSTEM_MSG = (
    "Je një asistent që sugjeron tituj shumë të shkurtër (2–7 fjalë), "
    "në shqip, sensacionalë por korrektë, bazuar në transkriptin e klipit."
)

def albanian_title(text: str) -> str:
    prompt = (
        "Bazuar në transkriptin më poshtë, propozo një titull të shkurtër "
        "(2–7 fjalë), pa thonjëza.\n\n" + text
    )
    r = openai.chat.completions.create(
        model="gpt-3.5-turbo",
        temperature=0.6,
        messages=[
            {"role": "system", "content": SYSTEM_MSG},
            {"role": "user",   "content": prompt},
        ],
    )
    return r.choices[0].message.content.strip()

# ── audio helpers ───────────────────────────────────────────────────────
def has_audio(path: Path) -> bool:
    cmd = [
        FFPROBE, "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index", "-of", "csv=p=0", str(path)
    ]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
    return bool(out)

def duration_sec(path: Path) -> float:
    cmd = [
        FFPROBE, "-v", "error",
        "-show_entries", "format=duration", "-of", "csv=p=0", str(path)
    ]
    return float(subprocess.check_output(cmd, text=True).strip())

def extract_ogg(mp4: Path) -> Path:
    tmp = Path(tempfile.mktemp(suffix=".ogg"))
    if has_audio(mp4):
        cmd = [
            FFMPEG, "-loglevel", "error", "-i", str(mp4),
            "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
            "-y", str(tmp)
        ]
    else:
        dur = duration_sec(mp4)
        cmd = [
            FFMPEG, "-loglevel", "error", "-f", "lavfi", "-t", f"{dur}",
            "-i", "anullsrc=r=16000:cl=mono", "-b:a", "32k",
            "-y", str(tmp)
        ]
    subprocess.check_call(cmd)
    return tmp

# ── processing ─────────────────────────────────────────────────────────
def process_clip(mp4: Path) -> tuple[str, str] | None:
    try:
        ogg = extract_ogg(mp4)
        with ogg.open("rb") as fh:
            txt = openai.audio.transcriptions.create(
                model="gpt-4o-mini-transcribe",
                file=fh,
                response_format="text",
                temperature=0.0,
            ).strip()
        title = albanian_title(txt)
        return mp4.name, title
    except Exception as exc:
        print(f"{WARN}: Skipped {mp4.name} – {exc}")
        return None
    finally:
        try:
            ogg.unlink(missing_ok=True)
        except Exception:
            pass

def main() -> None:
    clips = sorted(CLIPS.glob("*.mp4"))
    if not clips:
        print(f"{WARN}: No clips found in {CLIPS!r}")
        return

    rows: list[tuple[str, str]] = []
    print(f"Processing {len(clips)} clip(s)…\n")
    for mp4 in tqdm(clips, unit="clip"):
        res = process_clip(mp4)
        if res:
            rows.append(res)

    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out = LOG_DIR / f"clip_titles_{ts}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(("file", "title"))
        writer.writerows(rows)

    print(f"\n[✓] Titles saved {ARROW} {out}")

if __name__ == "__main__":
    main()
