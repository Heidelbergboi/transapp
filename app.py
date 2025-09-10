#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import time
import uuid
import json
import logging
import datetime
import subprocess
from pathlib import Path
from typing import Iterable

from flask import (
    Flask, render_template, request, Response,
    send_from_directory, jsonify, redirect
)
from dotenv import load_dotenv

# S3 helpers (already in your repo)
from s3_utils import (
    presign_single_post, presign_multipart, download_to_temp, presigned_url
)

# ── Env & paths ──────────────────────────────────────────────────────────
BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

LOG_DIR   = BASE / "logs";            LOG_DIR.mkdir(exist_ok=True)
VIDEO_DIR = BASE / "videos"
FULLS     = VIDEO_DIR / "full";       FULLS.mkdir(parents=True, exist_ok=True)
CLIPS     = Path(os.getenv("CLIPS_DIR", VIDEO_DIR / "clips"))
CLIPS.mkdir(parents=True, exist_ok=True)

PYTHON        = os.getenv("PYTHON_BIN", sys.executable)
CUT_SCRIPT    = str(BASE / "cut.py")
TITLE_SCRIPT  = str(BASE / "title_clips.py")

# ── Flask ────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=None)

# Logging: 2025-09-09 10:29:16,408 INFO → message
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s, %(levelname)s → %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
log = logging.getLogger("app")

# ── Utilities ────────────────────────────────────────────────────────────
def _run(cmd: list[str]) -> Iterable[str]:
    """Run a subprocess and yield its stdout lines. Raise if non-zero exit."""
    with subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    ) as p:
        assert p.stdout is not None
        for ln in p.stdout:
            yield ln.rstrip()
        p.wait()
        if p.returncode:
            raise subprocess.CalledProcessError(p.returncode, cmd)

# ── Routes ───────────────────────────────────────────────────────────────
@app.route("/")
def index():
    # If you have templates/index.html it will render; otherwise show JSON.
    tpl = BASE / "templates" / "index.html"
    if tpl.exists():
        return render_template("index.html")
    return jsonify({"ok": True, "service": "video-split", "clips_dir": str(CLIPS)})

@app.route("/healthz")
def healthz():
    return "ok", 200

# --- S3 presign endpoints for client-side upload -------------------------
@app.route("/presign/single", methods=["POST"])
def presign_single():
    data = request.get_json(silent=True) or {}
    filename = data.get("filename") or f"upload-{uuid.uuid4().hex}.mp4"
    key = data.get("key") or f"full/{filename}"
    out = presign_single_post(key=key)
    return jsonify(out)

@app.route("/presign/multipart", methods=["POST"])
def presign_multi():
    data = request.get_json(silent=True) or {}
    filename = data.get("filename") or f"upload-{uuid.uuid4().hex}.mp4"
    key = data.get("key") or f"full/{filename}"
    size = int(data.get("size") or 0)
    out = presign_multipart(key=key, size=size)
    return jsonify(out)

# --- Main job: split + titles -------------------------------------------
@app.route("/gen", methods=["POST"])
def generate():
    """
    Body JSON:
      { "s3": "full/yourfile.mp4", "parts": 5 }   OR   { "s3": "...", "interval": 120 }
    Streams plain-text logs back to client.
    """
    data   = request.get_json(force=True)
    s3_key = data.get("s3")
    parts  = data.get("parts")
    interval = data.get("interval")

    if not s3_key:
        return Response("⛔ Missing 's3' key in JSON.\n", mimetype="text/plain", status=400)
    if not parts and not interval:
        return Response("⛔ Give either 'parts' or 'interval' in JSON.\n", mimetype="text/plain", status=400)

    job_id = uuid.uuid4().hex
    log.info("JOB %s start parts=%s interval=%s s3=%s", job_id, parts, interval, s3_key)

    def gen():
        src = None
        try:
            # 1) Download source to the Render disk (keeps current behavior)
            t0 = time.perf_counter()
            src = download_to_temp(s3_key)
            yield f"⬇️  Downloaded source to {src}\n"
            log.info("download %.1fs", time.perf_counter() - t0)

            # 2) Split (cut.py prints its own progress lines)
            yield "\n✂️  Duke ndarë videon në pjesë…\n"
            t1 = time.perf_counter()
            if parts:
                for ln in _run([PYTHON, CUT_SCRIPT, str(src), "--parts", str(parts)]):
                    yield ln + "\n"
            else:
                for ln in _run([PYTHON, CUT_SCRIPT, str(src), "--interval", str(interval)]):
                    yield ln + "\n"
            log.info("cut %.1fs", time.perf_counter() - t1)

            # 3) Generate titles
            yield "\n📝 Gjenerimi i titujve…\n"
            t2 = time.perf_counter()
            for ln in _run([PYTHON, TITLE_SCRIPT]):
                yield ln + "\n"
            log.info("title %.1fs", time.perf_counter() - t2)

            yield "\n🎉 FINISHED\n"
            log.info("JOB %s OK", job_id)
        except Exception as e:
            log.exception("JOB %s failed", job_id)
            yield f"\n⛔ {e}\n"
        finally:
            try:
                if isinstance(src, Path):
                    src.unlink(missing_ok=True)
            except Exception:
                pass

    return Response(gen(), mimetype="text/plain")

# --- Serve clips: local first; if missing, redirect to S3 ---------------
@app.route("/clips/<path:filename>")
def serve_clip(filename: str):
    local = CLIPS / filename
    if local.exists():
        return send_from_directory(CLIPS, filename)
    # Fallback: redirect to S3 if local file was deleted after upload
    try:
        url = presigned_url(f"clips/{filename}", expires=86400)
        return redirect(url, code=302)
    except Exception:
        return ("Not found", 404)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, threaded=True)
 