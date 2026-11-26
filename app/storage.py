"""
Atlas AI - Pluggable Object Storage Layer (Deployment-Ready S3 Alternative)

Supports three seamless backends configured via STORAGE_BACKEND in .env:
1. "postgres" (Default): Stores files persistently in Render PostgreSQL (`stored_objects` table).
   Ideal for deployment on Render/Railway/Fly without requiring AWS S3 or extra cloud accounts.
2. "s3": Any S3-compatible object storage (Cloudflare R2, Backblaze B2, Supabase Storage S3, MinIO, AWS S3)
   via optional S3_ENDPOINT_URL and boto3 presigned PUT URLs.
3. "local": Stores files on local disk under LOCAL_STORAGE_DIR for completely offline local testing.
"""

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import quote
import boto3
from botocore.exceptions import ClientError
from app.config import settings
from app.db import connection, delete_stored_object, get_stored_object, put_stored_object
from app.observability import trace

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def s3_client():
    """Return a cached boto3 S3 client supporting custom endpoints (Cloudflare R2, Backblaze B2, Supabase S3)."""
    cfg = settings()
    kwargs: dict[str, Any] = {"region_name": cfg.aws_region or "us-east-1"}
    if cfg.s3_endpoint_url.strip():
        kwargs["endpoint_url"] = cfg.s3_endpoint_url.strip()
    if cfg.aws_access_key_id and cfg.aws_secret_access_key:
        kwargs["aws_access_key_id"] = cfg.aws_access_key_id
        kwargs["aws_secret_access_key"] = cfg.aws_secret_access_key
    return boto3.client("s3", **kwargs)


def _safe_local_path(key: str) -> Path:
    """Resolve storage key safely inside local_storage_dir, preventing directory traversal."""
    root = Path(settings().local_storage_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = (root / key.lstrip("/")).resolve()
    if root not in target.parents and target != root:
        raise ValueError(f"Unsafe storage key path: {key}")
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


@trace("storage_put")
def put_object(
    key: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> str:
    """Persist bytes under `key` in the configured storage backend."""
    cfg = settings()
    backend = cfg.storage_backend.strip().lower()

    if backend == "s3":
        s3_client().put_object(
            Bucket=cfg.s3_bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )
    elif backend == "local":
        _safe_local_path(key).write_bytes(data)
    else:
        # Default: "postgres" persistent storage
        with connection() as conn:
            put_stored_object(conn, key, data, content_type)

    logger.info("Stored object [%s]: %s (%d bytes)", backend, key, len(data))
    return key


@trace("storage_get")
def get_object(key: str) -> tuple[bytes, str]:
    """Retrieve (data_bytes, content_type) for `key` from the configured storage backend."""
    cfg = settings()
    backend = cfg.storage_backend.strip().lower()

    if backend == "s3":
        resp = s3_client().get_object(Bucket=cfg.s3_bucket, Key=key)
        return resp["Body"].read(), resp.get("ContentType", "application/octet-stream")

    if backend == "local":
        path = _safe_local_path(key)
        if not path.is_file():
            raise FileNotFoundError(f"Object not found in local storage: {key}")
        return path.read_bytes(), "application/octet-stream"

    with connection() as conn:
        result = get_stored_object(conn, key)
    if result is None:
        raise FileNotFoundError(f"Object not found in PostgreSQL storage: {key}")
    return result


def get_object_bytes(key: str) -> bytes:
    """Convenience wrapper returning only raw bytes for `key`."""
    data, _ = get_object(key)
    return data


@trace("storage_delete")
def delete_object(key: str) -> bool:
    """Delete `key` from the configured storage backend."""
    cfg = settings()
    backend = cfg.storage_backend.strip().lower()

    if backend == "s3":
        try:
            s3_client().delete_object(Bucket=cfg.s3_bucket, Key=key)
            return True
        except ClientError as exc:
            logger.error("Failed deleting S3 object %s: %s", key, exc)
            return False

    if backend == "local":
        path = _safe_local_path(key)
        if path.is_file():
            path.unlink()
            return True
        return False

    with connection() as conn:
        return delete_stored_object(conn, key)


@trace("storage_upload_url")
def create_upload_target(
    key: str,
    content_type: str = "application/octet-stream",
    expires_in: int = 300,
) -> dict[str, Any]:
    """
    Generate a direct browser PUT upload descriptor.
    - For S3-compatible backends (R2, B2, S3): returns a real presigned PUT URL.
    - For PostgreSQL/local backends: returns the FastAPI PUT storage endpoint,
      giving browsers the exact same presigned-PUT workflow with zero CORS headaches.
    """
    cfg = settings()
    backend = cfg.storage_backend.strip().lower()

    if backend == "s3":
        url = s3_client().generate_presigned_url(
            "put_object",
            Params={
                "Bucket": cfg.s3_bucket,
                "Key": key,
                "ContentType": content_type,
            },
            ExpiresIn=expires_in,
        )
        return {
            "upload_url": url,
            "method": "PUT",
            "headers": {"Content-Type": content_type},
            "backend": "s3",
        }

    encoded_key = quote(key, safe="/")
    return {
        "upload_url": f"/api/storage/upload/{encoded_key}",
        "method": "PUT",
        "headers": {"Content-Type": content_type},
        "backend": backend,
    }
