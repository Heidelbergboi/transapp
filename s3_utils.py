#!/usr/bin/env python3
"""
s3_utils.py  –  single-POST (≤100 MB) + multipart (›100 MB) uploads
Automatically falls back to the standard endpoint if Transfer
Acceleration is not configured on the bucket.
"""

from __future__ import annotations
import os, math, tempfile, boto3, logging
from pathlib import Path
from botocore.client import Config
from botocore.exceptions import ClientError

log = logging.getLogger("s3_utils")

# ── config ───────────────────────────────────────────────────────────
BUCKET   = os.getenv("S3_BUCKET")
REGION   = os.getenv("AWS_REGION", "eu-south-1")          # Milan ≈ fastest
ACCEL_ON = os.getenv("S3_ACCEL", "0") == "1"              # opt-in via env var
PART_MB  = 8                                              # chunk size

if not BUCKET:
    raise RuntimeError("S3_BUCKET must be set in env vars")

_session = boto3.session.Session(
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=REGION,
)

# regular client – always works
_s3  = _session.client("s3")

# accelerated client – may fail if TA disabled
_acc = _session.client("s3",
        config=Config(s3={"use_accelerate_endpoint": ACCEL_ON}))

# ── helper: accel domain string ──────────────────────────────────────
def _acc_url() -> str:
    return f"https://{BUCKET}.s3-accelerate.amazonaws.com"

# --------------------------------------------------------------------
# SINGLE-POST  (≤100 MB)
# --------------------------------------------------------------------
def presign_single_post(filename: str, expires: int = 3600) -> dict:
    key  = f"full/{filename}"
    post = _s3.generate_presigned_post(
        Bucket=BUCKET, Key=key, ExpiresIn=expires,
        Fields={"Content-Type": "video/mp4"},
        Conditions=[
            ["starts-with", "$Content-Type", "video/"],
            ["content-length-range", 0, 5_368_709_120],
        ],
    )
    post.setdefault("url", f"https://{BUCKET}.s3.{REGION}.amazonaws.com")
    return dict(**post, multipart=False, s3_key=key)

# --------------------------------------------------------------------
# MULTIPART + PARALLEL PUT  (>100 MB)
# --------------------------------------------------------------------
def presign_multipart(filename: str, size: int,
                      part_mb: int = PART_MB, expires: int = 3600) -> dict:
    key   = f"full/{filename}"
    parts = math.ceil(size / (part_mb * 1024 * 1024))

    # 1) try accelerated first (if opted-in)
    use_accel = ACCEL_ON
    try:
        resp = _acc.create_multipart_upload(Bucket=BUCKET, Key=key)
        client_for_urls = _acc
        log.info("TA presign OK (upload_id=%s)", resp["UploadId"])
    except ClientError as e:
        if ACCEL_ON and e.response["Error"]["Code"] == "InvalidRequest":
            log.warning("Transfer Acceleration not enabled – falling back")
            use_accel = False
        else:
            raise
    if not use_accel:
        resp = _s3.create_multipart_upload(Bucket=BUCKET, Key=key)
        client_for_urls = _s3

    upload_id = resp["UploadId"]

    part_urls = [
        client_for_urls.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket":     BUCKET,
                "Key":        key,
                "UploadId":   upload_id,
                "PartNumber": i,
            },
            ExpiresIn=expires,
            HttpMethod="PUT",
        )
        for i in range(1, parts + 1)
    ]

    complete_url = client_for_urls.generate_presigned_url(
        "complete_multipart_upload",
        Params={"Bucket": BUCKET, "Key": key, "UploadId": upload_id},
        ExpiresIn=expires,
        HttpMethod="POST",
    )

    return dict(
        multipart    = True,
        upload_id    = upload_id,
        s3_key       = key,
        part_mb      = part_mb,
        part_urls    = part_urls,
        complete_url = complete_url,
    )

# --------------------------------------------------------------------
# utility helpers (unchanged)
# --------------------------------------------------------------------
def upload_file(local_path: Path, key: str) -> str:
    _s3.upload_file(str(local_path), BUCKET, key)
    return key

def download_to_temp(key: str) -> Path:
    tmp = Path(tempfile.mktemp(suffix="__" + Path(key).name))
    _s3.download_file(BUCKET, key, str(tmp))
    return tmp

def presigned_url(key: str, expires: int = 86_400) -> str:
    try:
        return _s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET, "Key": key},
            ExpiresIn=expires,
        )
    except ClientError:
        return ""
