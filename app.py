#!/usr/bin/env python3
from __future__ import annotations
import os, re, subprocess, uuid, sys, csv, shutil
from pathlib import Path
from flask import (
    Flask, render_template, request, redirect, url_for,
    stream_with_context, Response, send_from_directory, flash
)
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# ── SETUP ───────────────────────────────────────────────────────────────
BASE   = Path(__file__).parent
FULL   = BASE / "videos" / "full"
CLIPS  = BASE / "videos" / "clips"
LOGS   = BASE / "logs"
for d in (FULL, CLIPS, LOGS):
    d.mkdir(parents=True, exist_ok=True)

CUT_SCRIPT   = str(BASE / "cut.py")
TITLE_SCRIPT = str(BASE / "title_clips.py")
PYTHON       = sys.executable
FFMPEG       = os.getenv("FFMPEG_BINARY", "ffmpeg")
load_dotenv(BASE / ".env")

app = Flask(__name__, template_folder="templates")
app.secret_key = os.getenv("SECRET_KEY", "dev")  # don’t forget to change this!

ALLOWED_EXTS = {"mp4"}
JOBS: dict[str,dict] = {}

def allowed_file(fn: str) -> bool:
    return "." in fn and fn.rsplit(".",1)[1].lower() in ALLOWED_EXTS

def yt_id(url: str) -> str:
    m = re.search(r"(?:v=|be/)([\w-]{11})", url)
    if not m:
        raise ValueError("Link YouTube i pavlefshëm.")
    return m.group(1)

def run_cmd(cmd: list[str]):
    p = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, universal_newlines=True
    )
    for line in p.stdout:
        yield line.rstrip()
    p.wait()
    if p.returncode:
        raise subprocess.CalledProcessError(p.returncode, cmd)

# ── ROUTES ─────────────────────────────────────────────────────────────
@app.route("/", methods=["GET","POST"])
def index():
    if request.method=="POST":
        link   = request.form.get("youtubelink","").strip()
        parts  = request.form.get("parts","5")
        try:
            parts_n = max(2,int(parts))
        except:
            flash("Numri i pjesëve duhet të jetë ≥2","danger")
            return redirect(url_for("index"))

        upload = request.files.get("videofile")
        path = None
        if upload and allowed_file(upload.filename):
            fn   = secure_filename(upload.filename)
            path = str(FULL / f"{uuid.uuid4().hex}_{fn}")
            upload.save(path)
        elif not link:
            flash("Ngarkoni një MP4 ose vendosni link YouTube","danger")
            return redirect(url_for("index"))

        job_id = uuid.uuid4().hex
        JOBS[job_id] = {"link":link,"path":path,"parts":parts_n}
        return redirect(url_for("stream_page",job_id=job_id))

    return render_template("index.html")


@app.route("/stream/<job_id>")
def stream_page(job_id):
    return render_template("stream.html", job_id=job_id)


@app.route("/stream_raw/<job_id>")
def stream_raw(job_id):
    job = JOBS.pop(job_id, {})
    link = job.get("link","")
    path = job.get("path")
    parts= job.get("parts",5)

    @stream_with_context
    def generate():
        try:
            # ── **Wipe out last run’s clips & logs** ────────────────────
            for d in (CLIPS, LOGS):
                shutil.rmtree(d)
                d.mkdir(parents=True, exist_ok=True)

            # 1) fetch or upload
            if link:
                vid_id   = yt_id(link)
                vid_path = FULL / f"{vid_id}.mp4"
                if not vid_path.exists():
                    yield "## Shkarkimi i videos ##\n"
                    tpl = str(FULL / "%(id)s.%(ext)s")
                    cmd = [
                      "yt-dlp","-f","bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]",
                      "--remux-video","mp4",
                      "--ffmpeg-location",FFMPEG,
                      "-o",tpl, link, "--newline"
                    ]
                    for ln in run_cmd(cmd):
                        yield ln+"\n"
                else:
                    yield "Video ekziston në cache ✅\n"
                src = vid_path
            else:
                up = Path(path)
                yield f"Skedari u ngarkua ({up.name}) ✅\n"
                src = up

            # 2) cut
            yield f"\n## Prerja në {parts} pjesë ##\n"
            for ln in run_cmd([PYTHON,CUT_SCRIPT,str(src),"--parts",str(parts)]):
                yield ln+"\n"

            # 3) titling
            yield "\n## Gjenerimi i titujve ##\n"
            for ln in run_cmd([PYTHON,TITLE_SCRIPT]):
                yield ln+"\n"

            yield "\nFINISHED\n"
        except Exception as e:
            yield f"\n⛔ Error: {e}\n"

    return Response(generate(), mimetype="text/plain")


@app.route("/done")
def done():
    # only the newest CSV will ever exist
    csvs = sorted(LOGS.glob("clip_titles_*.csv"),
                  key=lambda p:p.stat().st_mtime, reverse=True)
    clips = []
    if csvs:
        with csvs[0].open(encoding="utf-8") as fh:
            clips = list(csv.DictReader(fh))
    return render_template("done.html", clips=clips)


@app.route("/clips/<path:filename>")
def serve_clip(filename):
    return send_from_directory(CLIPS, filename)


if __name__=="__main__":
    app.run(debug=True)
