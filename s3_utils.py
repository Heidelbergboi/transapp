#!/usr/bin/env python3
"""
s3_utils.py  –  single-POST (≤100 MB) and multipart (>100 MB) presign helpers,
plus thin wrappers to upload/download objects.  Now:
 • writes temp files in TMPDIR (Render disk)
 • uses 32 MB parts to cut presign chatter in half
 • falls back gracefully if Transfer Acceleration is off
"""
from __future__ import annotations
import os, math, tempfile, uuid, logging
from pathlib import Path
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

log = logging.getLogger("s3_utils")

# ── env & constants ───────────────────────────────────────────────────
BUCKET   = os.getenv("S3_BUCKET")
REGION   = os.getenv("AWS_REGION", "eu-south-1")
ACCEL_ON = os.getenv("S3_ACCEL", "0") == "1"
PART_MB  = int(os.getenv("PART_MB", 32))          # ← was 8 MB

if not BUCKET:
    raise RuntimeError("S3_BUCKET env var missing")

# scratch dir lives on the 10 GB Render disk
TMP_ROOT = Path(os.getenv("TMPDIR", "/tmp"))
TMP_ROOT.mkdir(parents=True, exist_ok=True)

# Boto3 clients
_session = boto3.session.Session(
    aws_access_key_id     = os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name           = REGION,
)
s3  = _session.client("s3")
acc = _session.client("s3",
        config=Config(s3={"use_accelerate_endpoint": ACCEL_ON}))

# ── presign helpers ───────────────────────────────────────────────────
def presign_single_post(filename: str, expires: int = 3600) -> dict:
    """For files ≤ 100 MB – HTML form upload."""
    key  = f"full/{filename}"
    post = s3.generate_presigned_post(
        Bucket     = BUCKET,
        Key        = key,
        ExpiresIn  = expires,
        Fields     = {"Content-Type": "video/mp4"},
        Conditions = [
            ["starts-with", "$Content-Type", "video/"],
            ["content-length-range", 0, 5_368_709_120],
        ],
    )
    post.setdefault("url", f"https://{BUCKET}.s3.{REGION}.amazonaws.com")
    return dict(**post, multipart=False, s3_key=key)

def presign_multipart(filename: str, size: int, expires: int = 3600) -> dict:
    """For > 100 MB uploads – many presigned PUT URLs."""
    key   = f"full/{filename}"
    parts = math.ceil(size / (PART_MB * 1_048_576))

    # 1) try accelerated endpoint if opted-in
    client_for_urls = acc
    try:
        resp = acc.create_multipart_upload(Bucket=BUCKET, Key=key)
    except ClientError as e:
        if ACCEL_ON and e.response["Error"]["Code"] == "InvalidRequest":
            log.warning("Transfer Acceleration disabled – falling back")
            client_for_urls = s3
            resp            = s3.create_multipart_upload(Bucket=BUCKET, Key=key)
        else:
            raise

    upload_id = resp["UploadId"]

    part_urls = [
        client_for_urls.generate_presigned_url(
            "upload_part",
            Params     = {
                "Bucket":     BUCKET,
                "Key":        key,
                "UploadId":   upload_id,
                "PartNumber": i,
            },
            ExpiresIn  = expires,
            HttpMethod = "PUT",
        )
        for i in range(1, parts + 1)
    ]

    complete_url = client_for_urls.generate_presigned_url(
        "complete_multipart_upload",
        Params     = {"Bucket": BUCKET, "Key": key, "UploadId": upload_id},
        ExpiresIn  = expires,
        HttpMethod = "POST",
    )

    return dict(
        multipart    = True,
        upload_id    = upload_id,
        s3_key       = key,
        part_mb      = PART_MB,
        part_urls    = part_urls,
        complete_url = complete_url,
    )

# ── convenience wrappers ──────────────────────────────────────────────
def upload_file(local: Path, key: str) -> str:
    s3.upload_file(str(local), BUCKET, key)
    return key

def download_to_temp(key: str) -> Path:
    """Pull <key> to the tmp disk; return its local path."""
    tmp = TMP_ROOT / f"{uuid.uuid4().hex}__{Path(key).name}"
    s3.download_file(BUCKET, key, str(tmp))
    return tmp

def presigned_url(key: str, expires: int = 86_400) -> str:
    return s3.generate_presigned_url(
        "get_object",
        Params    = {"Bucket": BUCKET, "Key": key},
        ExpiresIn = expires,
    )
