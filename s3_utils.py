#!/usr/bin/env python3
"""s3_utils.py – thin wrapper around boto3 for basic upload/download"""
from __future__ import annotations
import os, boto3, tempfile
from pathlib import Path
from botocore.exceptions import ClientError

BUCKET   = os.getenv("S3_BUCKET")
REGION   = os.getenv("AWS_REGION", "us-east-1")
_session = boto3.session.Session(
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
    region_name=REGION,
)
_s3 = _session.client("s3")

def upload_file(local_path: Path, key: str) -> str:
    """Upload and return the s3://… key"""
    _s3.upload_file(str(local_path), BUCKET, key)
    return key

def download_to_temp(key: str) -> Path:
    """Download S3 object to a temp file and return path"""
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
