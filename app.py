#!/usr/bin/env python3
from __future__ import annotations
import os, subprocess, uuid, sys, csv, shutil, time, logging, datetime
from pathlib import Path
from flask import (
    Flask, render_template, request, url_for,
    Response, stream_with_context, send_from_directory, jsonify
)
from dotenv import load_dotenv
from s3_utils import presign_single_post, presign_multipart, download_to_temp

# ── env & paths ──────────────────────────────────────────────────────
BASE         = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

LOG_DIR      = BASE / "logs"; LOG_DIR.mkdir(exist_ok=True)
DEFAULT_DIR  = BASE / "videos" / "clips"
CLIPS        = Path(os.getenv("CLIPS_DIR", DEFAULT_DIR))  # uses /var/data/clips
CUT_SCRIPT   = str(BASE / "cut.py")
TITLE_SCRIPT = str(BASE / "title_clips.py")
PYTHON       = sys.executable

# ── logging setup ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s → %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_DIR / f"run_{datetime.datetime.now():%Y%m%d_%H%M%S}.log",
            encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("transapp")

# ── Flask app ────────────────────────────────────────────────────────
app            = Flask(__name__, template_folder="templates")
app.secret_key = os.getenv("SECRET_KEY", "dev")

JOBS: dict[str, dict] = {}

@app.route("/")
def index(): return render_template("index.html")

@app.route("/ping")
def ping(): return ("", 204)

@app.route("/sign", methods=["POST"])
def sign():
    d    = request.get_json(force=True)
    size = int(d["size"])
    fn   = d["filename"]
    return jsonify(
        presign_multipart(fn, size) if size > 100 * 1024 * 1024
        else presign_single_post(fn)
    )

@app.route("/start-job", methods=["POST"])
def start_job():
    d   = request.get_json(force=True)
    jid = uuid.uuid4().hex
    JOBS[jid] = dict(s3_key=d["s3_key"],
                     parts=max(2, int(d.get("parts", 5))))
    return jsonify(stream=url_for("stream_page", job_id=jid))

@app.route("/stream/<job_id>")
def stream_page(job_id):
    return render_template("stream.html", job_id=job_id)

@app.route("/stream_raw/<job_id>")
def stream_raw(job_id):
    job = JOBS.pop(job_id, None)
    if not job:
        return Response("job not found\n", mimetype="text/plain")

    @stream_with_context
    def gen():
        s3key, parts = job["s3_key"], job["parts"]
        src: Path | None = None
        try:
            log.info("JOB %s start parts=%s s3=%s", job_id, parts, s3key)

            shutil.rmtree(CLIPS, ignore_errors=True)
            CLIPS.mkdir(parents=True, exist_ok=True)

            yield f"🔻 Shkarkimi nga S3: {s3key}\n"
            src = download_to_temp(s3key); yield "✅ Shkarkuar\n"

            yield f"\n✂️  Prerja në {parts} pjesë…\n"
            t0 = time.perf_counter()
            for ln in _run([PYTHON, CUT_SCRIPT, str(src),
                            "--parts", str(parts)]):
                yield ln + "\n"
            log.info("cut %.1fs", time.perf_counter() - t0)

            yield "\n💬 Gjenerimi i titujve…\n"
            t1 = time.perf_counter()
            for ln in _run([PYTHON, TITLE_SCRIPT]):
                yield ln + "\n"
            log.info("title %.1fs", time.perf_counter() - t1)

            yield "\n🎉 FINISHED\n"; log.info("JOB %s OK", job_id)
        except Exception as e:
            log.exception("JOB %s failed", job_id)
            yield f"\n⛔ {e}\n"
        finally:
            try:
                if src and src.exists():
                    src.unlink(missing_ok=True)
            except Exception:
                pass
    return Response(gen(), mimetype="text/plain")

@app.route("/done")
def done():
    csvs = sorted(LOG_DIR.glob("clip_titles_*.csv"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    clips = []
    if csvs:
        with csvs[0].open(encoding="utf-8") as fh:
            clips = list(csv.DictReader(fh))
    return render_template("done.html", clips=clips)

@app.route("/clips/<path:filename>")
def serve_clip(filename):
    return send_from_directory(CLIPS, filename)

# ── helper ───────────────────────────────────────────────────────────
def _run(cmd):
    with subprocess.Popen(cmd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True) as p:
        yield from (ln.rstrip() for ln in p.stdout)
        p.wait()
        if p.returncode:
            raise subprocess.CalledProcessError(p.returncode, cmd)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000, threaded=True)
