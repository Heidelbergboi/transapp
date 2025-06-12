#!/usr/bin/env python3
from __future__ import annotations

import os, re, subprocess, uuid, sys, csv, shutil, tempfile, time, logging, datetime
from pathlib import Path
from flask import (
    Flask, render_template, request, url_for,
    stream_with_context, Response, send_from_directory, jsonify
)
from dotenv import load_dotenv

# ── internal helpers ────────────────────────────────────────────────
BASE = Path(__file__).parent.resolve()
load_dotenv(BASE / ".env")

# local utilities (you already have these modules)
from s3_utils import (
    presign_single_post, presign_multipart,
    upload_file, download_to_temp
)

CUT_SCRIPT   = str(BASE / "cut.py")
TITLE_SCRIPT = str(BASE / "title_clips.py")
PYTHON       = sys.executable

# ── Flask init & logging ────────────────────────────────────────────
app            = Flask(__name__, template_folder="templates")
app.secret_key = os.getenv("SECRET_KEY", "dev")

LOG_DIR  = BASE / "logs";   LOG_DIR.mkdir(exist_ok=True)
FULL     = BASE / "videos" / "full";  FULL.mkdir(parents=True, exist_ok=True)
CLIPS    = BASE / "videos" / "clips"; CLIPS.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s → %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"run_{datetime.datetime.now():%Y%m%d_%H%M%S}.log",
                            encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ],
)
log = logging.getLogger("transapp")

# one-shot job state (kept in memory)
JOBS: dict[str, dict] = {}

# ────────────────────────────────────────────────────────────────────
# 1) Landing page
@app.route("/")
def index():
    return render_template("index.html")

# 2) /sign – decide single-POST vs multipart based on file size
@app.route("/sign", methods=["POST"])
def sign():
    data      = request.get_json(force=True)
    filename  = data["filename"]
    size      = int(data.get("size", 0))          # sent by the browser

    if size > 100 * 1024 * 1024:                  # >100 MB → multipart
        resp = presign_multipart(filename, size)
    else:
        resp = presign_single_post(filename)

    return jsonify(resp)

# 3) /start-job  (called after the upload finishes)
@app.route("/start-job", methods=["POST"])
def start_job():
    d       = request.get_json(force=True)
    job_id  = uuid.uuid4().hex
    JOBS[job_id] = dict(
        s3_key=d["s3_key"],
        parts=max(2, int(d.get("parts", 5)))
    )
    return jsonify(stream=url_for("stream_page", job_id=job_id))

# 4) small html wrapper that loads the streaming log
@app.route("/stream/<job_id>")
def stream_page(job_id):
    return render_template("stream.html", job_id=job_id)

# 5) server-sent streaming log
@app.route("/stream_raw/<job_id>")
def stream_raw(job_id):
    job   = JOBS.pop(job_id, {})
    s3key = job["s3_key"]; parts = job["parts"]

    @stream_with_context
    def generate():
        try:
            log.info("JOB %s start parts=%s s3=%s", job_id, parts, s3key)
            shutil.rmtree(CLIPS, ignore_errors=True); CLIPS.mkdir(exist_ok=True)

            yield f"🔻 Shkarkimi nga S3: {s3key}\n"
            src = download_to_temp(s3key);          yield "✅ Shkarkuar\n"

            yield f"\n✂️  Prerja në {parts} pjesë…\n"
            t0 = time.perf_counter()
            for ln in _run_cmd([PYTHON, CUT_SCRIPT, str(src), "--parts", str(parts)]):
                yield ln + "\n"
            log.info("cut %.1fs", time.perf_counter() - t0)

            yield "\n💬 Gjenerimi i titujve…\n"
            t1 = time.perf_counter()
            for ln in _run_cmd([PYTHON, TITLE_SCRIPT]):
                yield ln + "\n"
            log.info("title %.1fs", time.perf_counter() - t1)

            yield "\n🎉 FINISHED\n"; log.info("JOB %s OK", job_id)
        except Exception as e:
            log.exception("JOB %s failed", job_id)
            yield f"\n⛔ {e}\n"

    return Response(generate(), mimetype="text/plain")

# 6) done page
@app.route("/done")
def done():
    csvs = sorted(LOG_DIR.glob("clip_titles_*.csv"),
                  key=lambda p: p.stat().st_mtime,
                  reverse=True)
    clips, msg = [], None
    if csvs:
        with csvs[0].open(encoding="utf-8") as fh:
            clips = list(csv.DictReader(fh))
            if not clips:
                fh.seek(0)
                last = list(csv.reader(fh))[-1]
                if last and last[0].startswith("#"):
                    msg = last[0][1:].strip()
    return render_template("done.html", clips=clips, msg=msg)

# 7) raw clip files (served from local disk)
@app.route("/clips/<path:filename>")
def serve_clip(filename):
    return send_from_directory(CLIPS, filename)

# ── helpers ─────────────────────────────────────────────────────────
def _run_cmd(cmd: list[str]):
    with subprocess.Popen(cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True) as p:
        for line in p.stdout:
            yield line.rstrip()
        p.wait()
        if p.returncode:
            raise subprocess.CalledProcessError(p.returncode, cmd)

if __name__ == "__main__":
    # 0.0.0.0:10000 is what Render expects
    app.run(host="0.0.0.0", port=10000, threaded=True)
