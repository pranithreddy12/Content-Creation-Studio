"""S3-compatible storage helper."""
from __future__ import annotations

from functools import lru_cache
from typing import BinaryIO

import boto3
from botocore.config import Config

from app.core.config import settings


@lru_cache
def s3():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        config=Config(signature_version="s3v4", retries={"max_attempts": 3, "mode": "standard"}),
    )


def upload_fileobj(fileobj: BinaryIO, key: str, content_type: str | None = None) -> str:
    extra = {"ContentType": content_type} if content_type else None
    s3().upload_fileobj(fileobj, settings.s3_bucket, key, ExtraArgs=extra)
    return key


def presign(key: str, expires_in: int = 3600) -> str:
    return s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=expires_in,
    )


def delete_prefix(prefix: str) -> int:
    """Delete every object under `prefix`; returns the count deleted. Idempotent
    (an empty prefix deletes nothing). Used by the account purge."""
    s = s3()
    bucket = settings.s3_bucket
    deleted = 0
    paginator = s.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        objs = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if objs:
            s.delete_objects(Bucket=bucket, Delete={"Objects": objs})
            deleted += len(objs)
    return deleted
