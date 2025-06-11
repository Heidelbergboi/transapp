#!/usr/bin/env python3
"""
title_clips.py – Albanian titles with 'no speech' logging & placeholder.
"""
from __future__ import annotations
import csv, os, subprocess, tempfile, sys, datetime, logging
from pathlib import Path
from shutil import which
from dotenv import load_dotenv

load_dotenv(".env")

from openai import OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"),
                project=os.getenv("OPENAI_PROJECT_ID"))

# --- paths --------------------------------------------------------------
BASE      = Path(__file__).resolve().parent           # ← SAME root everywhere
CLIP_DIR  = BASE / "videos" / "clips"
LOG_DIR   = BASE / "logs"; LOG_DIR.mkdir(exist_ok=True)

# --- ffmpeg -------------------------------------------------------------
FFMPEG_ENV = os.getenv("FFMPEG_BINARY")
FFMPEG  = FFMPEG_ENV if FFMPEG_ENV and Path(FFMPEG_ENV).exists() else "ffmpeg"
FFPROBE = Path(FFMPEG).with_name(
    "ffprobe.exe" if Path(FFMPEG).suffix == ".exe" else "ffprobe"
)
if not which(FFMPEG):
    sys.exit("⛔  ffmpeg not found – install it in your Render build.")

# --- logging ------------------------------------------------------------
logging.basicConfig(level=logging.INFO,
    format="%(levelname)s title → %(message)s")

SYSTEM = ("Je një asistent që sugjeron tituj shumë të shkurtër "
          "(2–7 fjalë) në shqip, sensacionalë por korrektë, "
          "bazuar në transkriptin e klipit.")

def _run(cmd: list[str]): subprocess.check_call(cmd,
                                               stdout=subprocess.DEVNULL,
                                               stderr=subprocess.DEVNULL)

def _has_audio(p: Path) -> bool:
    return bool(subprocess.check_output(
        [FFPROBE, "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(p)],
        text=True).strip())

def _duration(p: Path) -> float:
    return float(subprocess.check_output(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(p)], text=True))

def _to_ogg(mp4: Path) -> Path:
    ogg = Path(tempfile.mktemp(suffix=".ogg"))
    if _has_audio(mp4):
        _run([FFMPEG, "-loglevel", "error", "-i", mp4,
              "-vn", "-ac", "1", "-ar", "16000", "-b:a", "32k", "-y", ogg])
    else:
        _run([FFMPEG, "-loglevel", "error", "-f", "lavfi",
              "-t", str(_duration(mp4)), "-i", "anullsrc=r=16000:cl=mono",
              "-b:a", "32k", "-y", ogg])
    return ogg

def _title(mp4: Path) -> str:
    try:
        ogg = _to_ogg(mp4)
        with ogg.open("rb") as fh:
            txt = client.audio.transcriptions.create(
                    model="whisper-1", file=fh,
                    response_format="text").strip()
        if not txt:
            logging.warning("no speech → %s", mp4.name)
            return "[Nuk u gjet fjalë]"
        prompt = ("Bazuar në transkriptin më poshtë, propozo një titull "
                  "(2–7 fjalë), pa thonjëza.\n\n" + txt)
        rsp = client.chat.completions.create(
                model="gpt-3.5-turbo", temperature=0.6,
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user",   "content": prompt}])
        return rsp.choices[0].message.content.strip()
    except Exception as e:
        logging.error("%s failed – %s", mp4.name, e)
        return "[Titull i munguar]"
    finally:
        try:
            ogg.unlink(missing_ok=True)
        except Exception:
            pass

def main():
    rows = []
    for mp4 in sorted(CLIP_DIR.glob("*.mp4")):
        logging.info("processing %s", mp4.name)
        rows.append((mp4.name, _title(mp4)))
        # mp4.unlink(missing_ok=True)      # ← keep files so /clips/<file> works
    ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out = LOG_DIR / f"clip_titles_{ts}.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([("file", "title"), *rows])
    print("[OK] Titles saved ->", out)

if __name__ == "__main__":
    main()
