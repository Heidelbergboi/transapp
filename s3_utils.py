#!/usr/bin/env python3
"""s3_utils.py – presigned POST *or* multipart-PUT with acceleration"""

from __future__ import annotations
import os, math, boto3, tempfile
from pathlib import Path
from botocore.client import Config
from botocore.exceptions import ClientError

# ────────────────────────────────────────────────────────────────────
BUCKET   = os.getenv("S3_BUCKET")
REGION   = os.getenv("AWS_REGION", "eu-south-1")      # Milan is closest to AL
ACCEL    = True                                       # always use acceleration
PART_MB  = 8                                          # multipart chunk size

_session = boto3.session.Session(
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=REGION,
)

# Standard client (no accel) – used for simple uploads & downloads
_s3 = _session.client("s3")

# Accel client – used only for presigning upload URLs
_acc = _session.client("s3",
        config=Config(s3={"use_accelerate_endpoint": ACCEL}))

# ────────────────────────────────────────────────────────────────────
def _acc_url() -> str:
    """https://<bucket>.s3-accelerate.amazonaws.com"""
    return f"https://{BUCKET}.s3-accelerate.amazonaws.com" if ACCEL \
           else f"https://{BUCKET}.s3.{REGION}.amazonaws.com"

# ---------- traditional single-POST --------------------------------
def presign_single_post(filename: str, expires: int = 3600) -> dict:
    """Return fields+url for a browser direct-POST."""
    key = f"full/{filename}"
    post = _s3.generate_presigned_post(
        Bucket=BUCKET, Key=key, ExpiresIn=expires)
    # overwrite URL to use acceleration domain
    post["url"] = _acc_url()
    return dict(**post, multipart=False, s3_key=key)


# ---------- multipart, parallel PUT --------------------------------
def presign_multipart(filename: str, size: int,
                      part_mb: int = PART_MB, expires: int = 3600) -> dict:
    """Return {upload_id, part_urls[], complete_url, …}"""
    key = f"full/{filename}"
    parts = math.ceil(size / (part_mb * 1024 * 1024))

    resp = _acc.create_multipart_upload(Bucket=BUCKET, Key=key)
    upload_id = resp["UploadId"]

    part_urls = [
        _acc.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": BUCKET,
                "Key": key,
                "UploadId": upload_id,
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
        multipart=True,
        upload_id=upload_id,
        s3_key=key,
        part_mb=part_mb,
        part_urls=part_urls,
        complete_url=complete_url,
    )


# ---------- helpers used elsewhere ---------------------------------
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
