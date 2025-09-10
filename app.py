#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time
import uuid
import json
import logging
import traceback
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Iterable, Optional

from flask import (
    Flask, request, jsonify, Response,
    render_template, send_from_directory, redirect, url_for
)
from jinja2 import TemplateNotFound
from dotenv import load_dotenv

# ──────────────────────────────────────────────────────────────────────
# Paths & env
# ──────────────────────────────────────────────────────────────────────
BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")  # ok if missing

LOG_DIR   = BASE / "logs";            LOG_DIR.mkdir(exist_ok=True)
VIDEO_DIR = BASE / "videos"
FULLS     = VIDEO_DIR / "full";       FULLS.mkdir(parents=True, exist_ok=True)
CLIPS     = Path(os.getenv("CLIPS_DIR", VIDEO_DIR / "clips"))
CLIPS.mkdir(parents=True, exist_ok=True)

PYTHON       = os.getenv("PYTHON_BIN", sys.executable)
CUT_SCRIPT   = str(BASE / "cut.py")
TITLE_SCRIPT = str(BASE / "title_clips.py")

# S3 helpers (match your s3_utils.py)
from s3_utils import (
    presign_single_post, presign_multipart, download_to_temp, presigned_url
)

# Pick a template directory smartly:
# 1) TEMPLATE_DIR env → use it
# 2) if ./templates/index.html exists → use ./templates
# 3) if ./index.html exists → use repo root
def _choose_templates_dir() -> str:
    override = os.getenv("TEMPLATE_DIR")
    if override:
        return override
    if (BASE / "templates" / "index.html").exists():
        return str(BASE / "templates")
    if (BASE / "index.html").exists():
        return str(BASE)
    return str(BASE / "templates")  # default (even if file missing)

TEMPLATES_DIR = _choose_templates_dir()
app = Flask(__name__, template_folder=TEMPLATES_DIR, static_folder=None)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s, %(levelname)s → %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("app")

# In-memory job params (streaming itself is per-request)
JOBS: Dict[str, Dict[str, Any]] = {}

# Up to 5 GB as single POST; above that use multipart
SINGLE_POST_MAX = 5_000_000_000  # 5 GB


# ──────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────
def _run(cmd: list[str]) -> Iterable[str]:
    """Run a subprocess and stream its stdout; raise on non-zero exit."""
    with subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True) as p:
        assert p.stdout is not None
        for ln in p.stdout:
            yield ln.rstrip()
        p.wait()
        if p.returncode:
            raise subprocess.CalledProcessError(p.returncode, cmd)

def _fmt_exc(e: BaseException) -> str:
    return "".join(traceback.format_exception(type(e), e, e.__traceback__))

def _render_html(name: str, **ctx) -> Response:
    """
    Robust renderer:
    - Try Jinja (render_template).
    - If the template isn't installed, try sending a static file from either
      the chosen template dir, repo root, or ./templates.
    - Minimal substitution for {{ job_id }} and {{ url_for('done') }} if needed.
    """
    try:
        return render_template(name, **ctx)
    except TemplateNotFound:
        # Try known locations
        for folder in [Path(TEMPLATES_DIR), BASE / "templates", BASE]:
            p = folder / name
            if p.exists():
                html = p.read_text(encoding="utf-8")
                # naive replacements if Jinja isn't available in static file
                if "job_id" in ctx:
                    for tok in (f"{{{{ job_id }}}}", "{{job_id}}"):
                        html = html.replace(tok, str(ctx["job_id"]))
                html = html.replace("{{ url_for('done') }}", "/done")
                return Response(html, mimetype="text/html; charset=utf-8")
        # Last resort
        return Response(f"<h1>Template not found: {name}</h1>", status=500, mimetype="text/html")


# ──────────────────────────────────────────────────────────────────────
# Pages
# ──────────────────────────────────────────────────────────────────────
@app.route("/")
def home():
    return _render_html("index.html")

@app.route("/stream/<job_id>")
def stream_page(job_id: str):
    return _render_html("stream.html", job_id=job_id)

@app.route("/done")
def done():
    return "✅ Done. You can close this tab.", 200

@app.route("/ping")
def ping():
    return ("", 204)


# ──────────────────────────────────────────────────────────────────────
# Presign API (what your index.html expects)
# ──────────────────────────────────────────────────────────────────────
@app.route("/sign", methods=["POST"])
def sign():
    data = request.get_json(force=True)
    size = int(data.get("size") or 0)
    filename = data.get("filename") or f"upload-{uuid.uuid4().hex}.mp4"

    if size <= SINGLE_POST_MAX:
        out = presign_single_post(filename)         # fields+url for HTML form POST
        mp = False
    else:
        out = presign_multipart(filename, size)     # presigned multipart URLs
        mp = True

    log.info("sign → %s (%s bytes) multipart=%s", filename, f"{size:,}", mp)
    return jsonify(out)


# ──────────────────────────────────────────────────────────────────────
# Start job; returns JSON containing a link to the streaming page
# ──────────────────────────────────────────────────────────────────────
@app.route("/start-job", methods=["POST"])
def start_job():
    data = request.get_json(force=True)
    s3_key  = data.get("s3_key") or data.get("s3Key") or data.get("key")
    parts   = data.get("parts")
    interval = data.get("interval")

    if not s3_key:
        return jsonify({"error": "missing s3_key"}), 400
    if not parts and not interval:
        return jsonify({"error": "provide parts or interval"}), 400

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        "s3_key": s3_key,
        "parts": int(parts) if parts else None,
        "interval": float(interval) if interval else None,
        "created_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    }
    log.info("JOB %s queued parts=%s interval=%s s3=%s", job_id, parts, interval, s3_key)
    return jsonify({
        "ok": True,
        "job_id": job_id,
        "stream": url_for("stream_page", job_id=job_id),
    })


# ──────────────────────────────────────────────────────────────────────
# Raw streaming endpoint consumed by stream.html via fetch()
# ──────────────────────────────────────────────────────────────────────
@app.route("/stream/raw/<job_id>")
def stream_raw(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return Response(f"⛔ Unknown job id {job_id}\n", mimetype="text/plain", status=404)

    def gen():
        src_path: Optional[Path] = None
        try:
            log.info("JOB %s start parts=%s interval=%s s3=%s",
                     job_id, job["parts"], job["interval"], job["s3_key"])

            # 1) Download source to persistent disk (keeps your current flow)
            t0 = time.perf_counter()
            src_path = download_to_temp(job["s3_key"])
            yield f"⬇️  Shkarkim nga S3: {job['s3_key']}\n"
            yield "Shkarkuar ✅\n"
            log.info("download %.1fs", time.perf_counter() - t0)

            # 2) Split into parts
            yield "\n✂️  Prerja në pjesë…\n"
            t1 = time.perf_counter()
            if job["parts"]:
                for ln in _run([PYTHON, CUT_SCRIPT, str(src_path), "--parts", str(job["parts"])]):
                    yield ln + "\n"
            else:
                for ln in _run([PYTHON, CUT_SCRIPT, str(src_path), "--interval", str(job["interval"])]):
                    yield ln + "\n"
            log.info("cut %.1fs", time.perf_counter() - t1)

            # 3) Titles (title_clips.py prefers .ogg, falls back to .mp4)
            yield "\n📝 Gjenerimi i titujve…\n"
            t2 = time.perf_counter()
            for ln in _run([PYTHON, TITLE_SCRIPT]):
                yield ln + "\n"
            log.info("title %.1fs", time.perf_counter() - t2)

            yield "\n🎉 FINISHED\n"
            log.info("JOB %s OK", job_id)
        except subprocess.CalledProcessError as e:
            msg = f"⛔ Subprocess failed: {e}\n"
            yield msg
            log.error(msg.strip())
        except Exception as e:
            msg = _fmt_exc(e)
            yield "⛔ " + msg + "\n"
            log.error("JOB %s failed\n%s", job_id, msg)
        finally:
            try:
                if isinstance(src_path, Path):
                    src_path.unlink(missing_ok=True)
            except Exception:
                pass

    return Response(gen(), mimetype="text/plain; charset=utf-8")


# ──────────────────────────────────────────────────────────────────────
# Serve clips: local if present; otherwise redirect to S3 (fallback)
# ──────────────────────────────────────────────────────────────────────
@app.route("/clips/<path:filename>")
def serve_clip(filename: str):
    local = CLIPS / filename
    if local.exists():
        return send_from_directory(str(CLIPS), filename)
    try:
        url = presigned_url(f"clips/{filename}")
        return redirect(url, code=302)
    except Exception:
        return ("Not found", 404)


# ──────────────────────────────────────────────────────────────────────
# Entrypoint
# ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, threaded=True)
