#!/usr/bin/env python3
"""
title_clips.py – Albanian titles with 'no speech' logging & placeholder.
"""
from __future__ import annotations
import csv, os, subprocess, tempfile, sys, datetime, logging
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(".env")

from openai import OpenAI
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"),
                project=os.getenv("OPENAI_PROJECT_ID"))

BASE      = Path(__file__).resolve().parent
CLIP_DIR  = BASE / "videos" / "clips"
LOG_DIR   = BASE / "logs"; LOG_DIR.mkdir(exist_ok=True)

FFMPEG  = os.getenv("FFMPEG_BINARY","ffmpeg")
FFPROBE = Path(FFMPEG).with_name("ffprobe.exe" if Path(FFMPEG).suffix==".exe" else "ffprobe")

logging.basicConfig(level=logging.INFO,
    format="%(levelname)s title → %(message)s")

SYSTEM = ("Je një asistent që sugjeron tituj shumë të shkurtër (2–7 fjalë), "
          "në shqip, sensacionalë por korrektë, bazuar në transkriptin e klipit.")

def run(cmd:list[str]): subprocess.check_call(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def has_audio(p): return bool(subprocess.check_output(
        [FFPROBE,"-v","error","-select_streams","a","-show_entries",
         "stream=index","-of","csv=p=0",str(p)],text=True).strip())

def duration(p): return float(subprocess.check_output(
        [FFPROBE,"-v","error","-show_entries","format=duration",
         "-of","csv=p=0",str(p)],text=True))

def to_ogg(mp4:Path)->Path:
    ogg = Path(tempfile.mktemp(suffix=".ogg"))
    if has_audio(mp4):
        run([FFMPEG,"-loglevel","error","-i",mp4,"-vn","-ac","1","-ar","16000",
             "-b:a","32k","-y",ogg])
    else:
        run([FFMPEG,"-loglevel","error","-f","lavfi","-t",str(duration(mp4)),
             "-i","anullsrc=r=16000:cl=mono","-b:a","32k","-y",ogg])
    return ogg

def title(mp4:Path)->str:
    try:
        ogg=to_ogg(mp4)
        with ogg.open("rb") as fh:
            txt=client.audio.transcriptions.create(model="whisper-1",file=fh,
                                                   response_format="text").strip()
        if not txt:
            logging.warning("no speech → %s", mp4.name)
            return "[Nuk u gjet fjalë]"
        prompt=("Bazuar në transkriptin më poshtë, propozo një titull "
                "(2–7 fjalë), pa thonjëza.\n\n"+txt)
        rsp=client.chat.completions.create(
                model="gpt-3.5-turbo",temperature=0.6,
                messages=[{"role":"system","content":SYSTEM},
                          {"role":"user","content":prompt}])
        return rsp.choices[0].message.content.strip()
    except Exception as e:
        logging.error("%s failed – %s", mp4.name, e)
        return "[Titull i munguar]"
    finally:
        try: ogg.unlink(missing_ok=True)
        except Exception: pass

def main():
    rows=[]
    for mp4 in sorted(CLIP_DIR.glob("*.mp4")):
        logging.info("processing %s", mp4.name)
        rows.append((mp4.name, title(mp4)))
        mp4.unlink(missing_ok=True)
    ts=datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out=LOG_DIR/f"clip_titles_{ts}.csv"
    with out.open("w",newline="",encoding="utf-8") as f:
        csv.writer(f).writerows([("file","title"),*rows])
    print("[OK] Titles saved ->", out)

if __name__=="__main__":
    main()
