#!/usr/bin/env python3
from __future__ import annotations
import os, re, subprocess, uuid, sys, csv, shutil, tempfile, time, logging, datetime, json
from pathlib import Path
from flask import (
    Flask, render_template, request, redirect, url_for,
    stream_with_context, Response, send_from_directory, jsonify
)
from dotenv import load_dotenv
from s3_utils import upload_file, download_to_temp
from boto3.session import Session

# ── env / aws session ─────────────────────────────────────────────────
BASE = Path(__file__).parent.resolve()
load_dotenv(BASE / ".env")
AWS   = Session(
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=os.getenv("AWS_REGION","eu-north-1"),
)
S3_BUCKET = os.getenv("S3_BUCKET")

# ── logging -----------------------------------------------------------
LOG_DIR = Path("logs"); LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / f"run_{datetime.datetime.now():%Y%m%d_%H%M%S}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s → %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"),
              logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("transapp")

# ── local work dirs ---------------------------------------------------
FULL  = BASE / "videos" / "full";  FULL.mkdir(parents=True, exist_ok=True)
CLIPS = BASE / "videos" / "clips"; CLIPS.mkdir(parents=True, exist_ok=True)

CUT_SCRIPT   = str(BASE / "cut.py")
TITLE_SCRIPT = str(BASE / "title_clips.py")
PYTHON       = sys.executable
FFMPEG       = os.getenv("FFMPEG_BINARY","ffmpeg")

# ── flask app ---------------------------------------------------------
app = Flask(__name__, template_folder="templates")
app.secret_key = os.getenv("SECRET_KEY","dev")

JOBS: dict[str,dict] = {}

def yt_id(url:str)->str:
    m=re.search(r"(?:v=|be/)([\w-]{11})",url)
    if not m: raise ValueError("Link YouTube i pavlefshëm.")
    return m.group(1)

def run_cmd(cmd:list[str]):
    with subprocess.Popen(cmd,stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT,text=True) as p:
        for ln in p.stdout: yield ln.rstrip()
        p.wait();  p.check_returncode()

# ──────────────────────────────────────────────────────────────────────
# 1)  DIRECT-TO-S3 PRESIGNED UPLOAD
# ---------------------------------------------------------------------
@app.route("/sign", methods=["POST"])
def sign():
    """Return a one-off S3 POST policy so the browser can upload directly."""
    filename = request.json["filename"]
    ext      = Path(filename).suffix or ".mp4"
    key      = f"full/{uuid.uuid4().hex}{ext}"
    s3       = AWS.client("s3")
    policy   = s3.generate_presigned_post(
        Bucket=S3_BUCKET,
        Key=key,
        Fields={"acl":"private","Content-Type":"video/mp4"},
        Conditions=[
            ["starts-with","$Content-Type","video/"],
            ["content-length-range",0,5_368_709_120]   # 5 GB per POST
        ],
        ExpiresIn=3600,
    )
    return {"url":policy["url"],"fields":policy["fields"],"s3_key":key}

# ──────────────────────────────────────────────────────────────────────
# 2)  KICK OFF THE JOB AFTER UPLOAD
# ---------------------------------------------------------------------
@app.route("/start-job", methods=["POST"])
def start_job():
    data  = request.get_json(force=True)
    parts = max(2,int(data.get("parts",5)))
    job_id= uuid.uuid4().hex
    JOBS[job_id] = {"link":"", "s3_key":data["s3_key"], "parts":parts}
    return {"stream":url_for("stream_page",job_id=job_id)}

# ──────────────────────────────────────────────────────────────────────
# 3)  UI PAGES
# ---------------------------------------------------------------------
@app.route("/")
def index():      return render_template("index.html")

@app.route("/stream/<job_id>")
def stream_page(job_id): return render_template("stream.html", job_id=job_id)

# ──────────────────────────────────────────────────────────────────────
# 4)  LONG-RUNNING STREAM (UNCHANGED EXCEPT NO FILE-UPLOAD BRANCH)
# ---------------------------------------------------------------------
@app.route("/stream_raw/<job_id>")
def stream_raw(job_id):
    job   = JOBS.pop(job_id,{})
    link  = job.get("link","")
    s3_key= job.get("s3_key")
    parts = job.get("parts",5)

    @stream_with_context
    def generate():
        try:
            log.info("JOB %s start parts=%s link=%s s3=%s",job_id,parts,bool(link),s3_key)
            shutil.rmtree(CLIPS,ignore_errors=True); CLIPS.mkdir(exist_ok=True)
            # 1) fetch --------------------------------------------------
            if link:
                vid_id=yt_id(link); src=FULL/f"{vid_id}.mp4"
                if not src.exists():
                    yield "🔻 Shkarkimi i videos…\n"
                    cmd=["yt-dlp","-f","bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]",
                         "--remux-video","mp4","--ffmpeg-location",FFMPEG,
                         "-o",str(src),link,"--newline"]
                    for ln in run_cmd(cmd): yield ln+"\n"
                    upload_file(src,f"full/{src.name}")
                else: yield "✅ Video në cache\n"
            else:
                yield f"🔻 Shkarkimi nga S3: {s3_key}\n"
                src=download_to_temp(s3_key);  yield "✅ Shkarkuar\n"
            # 2) cut ----------------------------------------------------
            yield f"\n✂️  Prerja në {parts} pjesë…\n"
            t0=time.perf_counter()
            for ln in run_cmd([PYTHON,CUT_SCRIPT,str(src),"--parts",str(parts)]):
                yield ln+"\n"
            log.info("cut %.1fs",time.perf_counter()-t0)
            # 3) title --------------------------------------------------
            yield "\n💬 Gjenerimi i titujve…\n"
            t1=time.perf_counter()
            for ln in run_cmd([PYTHON,TITLE_SCRIPT]): yield ln+"\n"
            log.info("title %.1fs",time.perf_counter()-t1)
            yield "\n🎉 FINISHED\n"; log.info("JOB %s OK",job_id)
        except Exception as e:
            log.exception("JOB %s failed",job_id);  yield f"\n⛔ {e}\n"

    return Response(generate(),mimetype="text/plain")

# ──────────────────────────────────────────────────────────────────────
# 5)  DONE PAGE & CLIP SERVE (UNCHANGED)
# ---------------------------------------------------------------------
@app.route("/done")
def done():
    csvs=sorted(LOG_DIR.glob("clip_titles_*.csv"),
                key=lambda p:p.stat().st_mtime,reverse=True)
    clips,msg=[],None
    if csvs:
        with csvs[0].open(encoding="utf-8") as fh:
            clips=list(csv.DictReader(fh))
            if not clips:
                fh.seek(0); last=list(csv.reader(fh))[-1]
                if last and last[0].startswith("#"): msg=last[0][1:].strip()
    return render_template("done.html",clips=clips,msg=msg)

@app.route("/clips/<path:filename>")
def serve_clip(filename): return send_from_directory(CLIPS,filename)

# ──────────────────────────────────────────────────────────────────────
if __name__=="__main__":
    app.run(debug=True)
