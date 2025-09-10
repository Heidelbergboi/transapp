#!/usr/bin/env python3
"""
title_clips.py – generate Albanian titles for every clip in $CLIPS_DIR.
NEW:
  • Prefers .ogg files created by cut.py (tiny); falls back to .mp4.
  • If using .mp4, converts to a small temp .ogg first.
  • Writes the same CSV shape you already expect in ./logs/.
"""
from __future__ import annotations
import os, csv, subprocess, tempfile, datetime, logging, sys
from pathlib import Path
from shutil import which
from dotenv import load_dotenv
from openai import OpenAI

# ── env & paths ───────────────────────────────────────────────────────
BASE        = Path(__file__).resolve().parent
DEFAULT_DIR = BASE / "videos" / "clips"
CLIPS_DIR   = Path(os.getenv("CLIPS_DIR", DEFAULT_DIR))
CLIPS_DIR.mkdir(parents=True, exist_ok=True)

LOG_DIR = BASE / "logs"; LOG_DIR.mkdir(exist_ok=True)
load_dotenv(BASE / ".env")

client = OpenAI(
    api_key = os.getenv("OPENAI_API_KEY"),
    project = os.getenv("OPENAI_PROJECT_ID"),
)

FFMPEG  = os.getenv("FFMPEG_BINARY", "ffmpeg")
FFPROBE = Path(FFMPEG).with_name(
    "ffprobe.exe" if Path(FFMPEG).suffix.lower()==".exe" else "ffprobe"
)
if not which(FFMPEG):
    sys.exit("⛔  ffmpeg not found – install it in the Render image.")

logging.basicConfig(level=logging.INFO, format="INFO title → %(message)s")
SYSTEM_MSG = ("Je një asistent që sugjeron tituj 2–7 fjalësh, "
              "sensacionalë por korrektë, në shqip, bazuar në transkriptin e klipit.")

def _run(cmd): subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def _to_ogg(mp4: Path) -> Path:
    fd, tmp_path = tempfile.mkstemp(suffix=".ogg")
    os.close(fd)
    out = Path(tmp_path)
    _run([FFMPEG, "-loglevel", "error", "-i", str(mp4),
          "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k",
          "-y", str(out)])
    return out

def _transcribe(audio_path: Path) -> str:
    with audio_path.open("rb") as fh:
        txt = client.audio.transcriptions.create(
            model="whisper-1", file=fh, response_format="text")
    return (txt or "").strip()

def _title_from_text(text: str) -> str:
    if not text:
        return "[Nuk u gjet fjalë]"
    rsp = client.chat.completions.create(
        model="gpt-3.5-turbo",
        temperature=0.6,
        messages=[
            {"role":"system","content":SYSTEM_MSG},
            {"role":"user","content":f"Bazuar në transkriptin më poshtë, propozo një titull (2–7 fjalë), pa thonjëza.\n\n{text}"}
        ]
    )
    return rsp.choices[0].message.content.strip()

def main():
    rows = [("file", "title")]

    # Prefer tiny .ogg created by cut.py; otherwise fall back to .mp4
    candidates = sorted(CLIPS_DIR.glob("*.ogg"))
    if not candidates:
        candidates = sorted(CLIPS_DIR.glob("*.mp4"))

    for p in candidates:
        logging.info("processing %s", p.name)
        try:
            if p.suffix.lower()==".ogg":
                text = _transcribe(p)
                mp4_name = p.with_suffix(".mp4").name
            else:
                tmp_ogg = _to_ogg(p)
                try:
                    text = _transcribe(tmp_ogg)
                finally:
                    try: tmp_ogg.unlink(missing_ok=True)
                    except Exception: pass
                mp4_name = p.name

            title = _title_from_text(text)
        except Exception as e:
            logging.info("error %s", e)
            title = "[Titull i munguar]"

        rows.append((mp4_name, title))

    ts  = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out = LOG_DIR / f"clip_titles_{ts}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    print("[OK] Titles saved ->", out)

if __name__ == "__main__":
    main()
