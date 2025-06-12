#!/usr/bin/env python3
"""
s3_utils.py – small wrapper for S3 uploads/downloads

• Single-POST  (≤100 MB)  → normal bucket endpoint
• Multipart    (>100 MB)  → transfer-accelerated, parallel PUT
"""

from __future__ import annotations
import os, math, tempfile, boto3
from pathlib import Path
from botocore.client import Config
from botocore.exceptions import ClientError

# ── config ───────────────────────────────────────────────────────────
BUCKET   = os.getenv("S3_BUCKET")
REGION   = os.getenv("AWS_REGION", "eu-south-1")      # Milan ≈ fastest for AL
ACCEL    = True                                       # use TA for multipart
PART_MB  = 8                                          # multipart chunk size MB

_session = boto3.session.Session(
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=REGION,
)

# Plain client (no acceleration) – used for single-POST & downloads
_s3  = _session.client("s3")

# Accelerated client – used only for presigning multipart URLs
_acc = _session.client(
    "s3",
    config=Config(s3={"use_accelerate_endpoint": ACCEL})
)

# ── helper: accel domain string ──────────────────────────────────────
def _acc_url() -> str:
    """https://<bucket>.s3-accelerate.amazonaws.com"""
    return f"https://{BUCKET}.s3-accelerate.amazonaws.com"

# --------------------------------------------------------------------
# SINGLE-POST  (≤100 MB)
# --------------------------------------------------------------------
def presign_single_post(filename: str, expires: int = 3600) -> dict:
    """
    Classic HTML <form> POST, good up to ~100 MB.
    We DO NOT force the accelerate domain here, so the code
    works even if Transfer Acceleration hasn’t been enabled yet.
    """
    key  = f"full/{filename}"
    post = _s3.generate_presigned_post(
        Bucket=BUCKET,
        Key=key,
        ExpiresIn=expires,
        Fields={"Content-Type": "video/mp4"},
        Conditions=[
            ["starts-with", "$Content-Type", "video/"],
            ["content-length-range", 0, 5_368_709_120],
        ],
    )
    # Ensure URL is present (SDK sometimes omits it)
    post.setdefault("url", f"https://{BUCKET}.s3.{REGION}.amazonaws.com")
    return dict(**post, multipart=False, s3_key=key)

# --------------------------------------------------------------------
# MULTIPART + PARALLEL PUT  (>100 MB)
# --------------------------------------------------------------------
def presign_multipart(filename: str, size: int,
                      part_mb: int = PART_MB, expires: int = 3600) -> dict:
    """
    Return { multipart=True, upload_id, part_mb, part_urls[], complete_url, … }
    Browser can PUT each part in parallel and then POST complete_url.
    """
    key   = f"full/{filename}"
    parts = math.ceil(size / (part_mb * 1024 * 1024))

    resp = _acc.create_multipart_upload(Bucket=BUCKET, Key=key)
    upload_id = resp["UploadId"]

    part_urls = [
        _acc.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": BUCKET,
                "Key":    key,
                "UploadId":  upload_id,
                "PartNumber": i,
            },
            ExpiresIn=expires,
            HttpMethod="PUT",
        )
        for i in range(1, parts + 1)
    ]

    complete_url = _acc.generate_presigned_url(
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
# Simple helper utilities (unchanged)
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
