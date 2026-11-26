"""
Atlas AI - Cloud & Storage Compatibility Module
Delegates storage operations to app.storage (supporting PostgreSQL persistence,
Cloudflare R2, Backblaze B2, Supabase Storage S3, MinIO, or AWS S3)
and re-exports embedding/RAG utilities.
"""

import json
import logging
from typing import Any, BinaryIO
from app.rag import embed, embed_batch
from app.storage import (
    create_upload_target,
    delete_object,
    get_object_bytes,
    put_object,
    s3_client,
)

logger = logging.getLogger(__name__)


def get_s3_client():
    """Return configured S3-compatible client (supports S3, R2, B2, Supabase S3)."""
    return s3_client()


def client(service_name: str = "s3"):
    """Compatibility wrapper returning S3-compatible client."""
    return s3_client()


def upload_document_to_s3(
    file_obj: BinaryIO | bytes,
    doc_id: str,
    filename: str,
    content_type: str = "application/octet-stream",
) -> str:
    """Store an uploaded raw document under documents/{doc_id}/{filename}."""
    data = file_obj if isinstance(file_obj, bytes) else file_obj.read()
    key = f"documents/{doc_id}/{filename}"
    return put_object(key, data, content_type=content_type)


def download_document_from_s3(s3_key: str) -> bytes:
    """Retrieve raw document bytes by storage key."""
    return get_object_bytes(s3_key)


def save_embeddings_to_s3(doc_id: str, embeddings_data: dict[str, Any]) -> str:
    """Persist computed chunk embeddings and metadata as JSON."""
    key = f"embeddings/{doc_id}.json"
    payload = json.dumps(embeddings_data, ensure_ascii=False).encode("utf-8")
    return put_object(key, payload, content_type="application/json")


def get_embeddings_from_s3(doc_id: str) -> dict[str, Any] | None:
    """Load chunk embeddings and metadata JSON by document ID."""
    key = f"embeddings/{doc_id}.json"
    try:
        raw = get_object_bytes(key)
        return json.loads(raw.decode("utf-8"))
    except FileNotFoundError:
        return None


def delete_from_s3(s3_key: str) -> bool:
    """Delete an object by storage key."""
    return delete_object(s3_key)


__all__ = [
    "client",
    "create_upload_target",
    "delete_from_s3",
    "download_document_from_s3",
    "embed",
    "embed_batch",
    "get_embeddings_from_s3",
    "get_s3_client",
    "save_embeddings_to_s3",
    "upload_document_to_s3",
]
