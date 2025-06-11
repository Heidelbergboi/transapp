#!/usr/bin/env python3
"""
cut.py – split an MP4, write slices under /tmp/videos/clips,
         upload each slice to S3 (clips/...), and return local paths.

The caller (Flask app) uses the paths then deletes them.
"""
from __future__ import annotations
import argparse, math, os, subprocess, sys, json, tempfile, shutil
from pathlib import Path

try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

# project root = transapp/
PROJECT   = Path(__file__).resolve().parent.parent
TMP_ROOT  = Path(tempfile.gettempdir()) / "videos" / "clips"
TMP_ROOT.mkdir(parents=True, exist_ok=True)

FFMPEG  = os.getenv("FFMPEG_BINARY", "ffmpeg")
FFPROBE = Path(FFMPEG).with_name(
    "ffprobe.exe" if Path(FFMPEG).suffix.lower()==".exe" else "ffprobe"
)

from s3_utils import upload_file
S3_PREFIX = "clips/"

def duration(path: Path) -> float:
    cmd=[FFPROBE,"-v","error","-show_entries","format=duration",
         "-of","csv=p=0",str(path)]
    return float(subprocess.check_output(cmd,text=True))

def split(src: Path, parts:int|None=None, interval:float|None=None)->list[str]:
    if not src.exists(): raise FileNotFoundError(src)
    total=duration(src)
    if parts: seg=total/parts
    elif interval: parts=math.ceil(total/interval); seg=interval
    else: raise ValueError("need --parts or --interval")

    print(f"[i] {src.name}: {total/60:.2f} min → {parts} slices × {seg:.1f}s")
    paths=[]
    base,ext=src.stem,src.suffix
    for i in range(parts):
        start=i*seg; length=min(seg,total-start)
        out=TMP_ROOT/f"{base}_part{i+1}{ext}"
        cmd=[FFMPEG,"-hide_banner","-loglevel","error",
             "-ss",str(start),"-i",str(src),"-t",str(length),
             "-c:v","copy","-c:a","aac","-b:a","64k",str(out)]
        subprocess.check_call(cmd)
        upload_file(out,S3_PREFIX+out.name)
        print(f"  slice {i+1}/{parts} → {out.name} ↑ S3")
        paths.append(str(out))
    return paths

def _cli():
    p=argparse.ArgumentParser()
    p.add_argument("video",type=Path)
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument("--parts","-p",type=int)
    g.add_argument("--interval","-i",type=float)
    return p.parse_args()

if __name__=="__main__":
    a=_cli()
    out_list=split(a.video.resolve(),parts=a.parts,interval=a.interval)
    print(json.dumps(out_list))
