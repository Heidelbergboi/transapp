#!/usr/bin/env python3
"""
title_clips.py – Albanian titles for each .mp4 in videos/clips.
                 Logs ‘no speech’ and still writes a placeholder title
                 so the /done page never comes back empty.
"""
from __future__ import annotations
import csv, os, subprocess, tempfile, sys, datetime, logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(".env")
from openai import OpenAI
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    project=os.getenv("OPENAI_PROJECT_ID"),
)

BASE     = Path(__file__).resolve().parent
CLIP_DIR = BASE / "videos" / "clips"
LOG_DIR  = BASE / "logs"; LOG_DIR.mkdir(exist_ok=True)

FFMPEG  = os.getenv("FFMPEG_BINARY", "ffmpeg")
FFPROBE = Path(FFMPEG).with_name(
    "ffprobe.exe" if Path(FFMPEG).suffix.lower()==".exe" else "ffprobe"
)

logging.basicConfig(level=logging.INFO,
    format="%(levelname)s title → %(message)s")

SYSTEM = ("Je një asistent që sugjeron tituj shumë të shkurtër (2–7 fjalë), "
          "në shqip, sensacionalë por korrektë, bazuar në transkriptin e klipit.")

# — ffprobe helpers ---------------------------------------------------
def has_audio(p:Path)->bool:
    cmd=[FFPROBE,"-v","error","-select_streams","a",
         "-show_entries","stream=index","-of","csv=p=0",str(p)]
    return bool(subprocess.check_output(cmd,text=True).strip())

def dur(p:Path)->float:
    cmd=[FFPROBE,"-v","error","-show_entries","format=duration",
         "-of","csv=p=0",str(p)]
    return float(subprocess.check_output(cmd,text=True))

def to_ogg(mp4:Path)->Path:
    ogg=Path(tempfile.mktemp(suffix=".ogg"))
    if has_audio(mp4):
        cmd=[FFMPEG,"-loglevel","error","-i",str(mp4),
             "-vn","-ac","1","-ar","16000","-b:a","32k","-y",str(ogg)]
    else:
        cmd=[FFMPEG,"-loglevel","error","-f","lavfi","-t",str(dur(mp4)),
             "-i","anullsrc=r=16000:cl=mono","-b:a","32k","-y",str(ogg)]
    subprocess.check_call(cmd); return ogg

# — title per clip ----------------------------------------------------
def title(mp4:Path)->str:
    try:
        ogg=to_ogg(mp4)
        with ogg.open("rb") as fh:
            txt=client.audio.transcriptions.create(
                    model="whisper-1", file=fh,
                    response_format="text").strip()

        if not txt:
            logging.warning("no speech → %s", mp4.name)
            return "[Nuk u gjet fjalë]"

        prompt=("Bazuar në transkriptin më poshtë, propozo një titull "
                "shumë të shkurtër (2–7 fjalë), pa thonjëza.\n\n"+txt)
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

# — driver ------------------------------------------------------------
def main()->None:
    clips=sorted(CLIP_DIR.glob("*.mp4"))
    if not clips:
        print("No clips in",CLIP_DIR); return
    rows=[]
    for m in clips:
        logging.info("processing %s", m.name)
        rows.append((m.name, title(m)))
        m.unlink(missing_ok=True)          # keep disk clean

    ts=datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out=LOG_DIR/f"clip_titles_{ts}.csv"
    with out.open("w",newline="",encoding="utf-8") as f:
        csv.writer(f).writerows([("file","title"), *rows])
    print("[OK] Titles saved ->", out)

if __name__=="__main__":
    main()
