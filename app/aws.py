import json
import logging
from typing import Any, BinaryIO
import boto3
from botocore.exceptions import ClientError
from app.config import settings

logger = logging.getLogger(__name__)


def get_s3_client():
    """Create and return a boto3 S3 client using project settings."""
    cfg = settings()
    return boto3.client(
        "s3",
        region_name=cfg.aws_region,
        aws_access_key_id=cfg.aws_access_key_id,
        aws_secret_access_key=cfg.aws_secret_access_key,
    )


def upload_document_to_s3(
    file_obj: BinaryIO | bytes,
    doc_id: str,
    filename: str,
    content_type: str = "application/octet-stream",
) -> str:
    """
    Upload an ingested raw document to S3 (Free Tier: 5GB storage).
    Key format: documents/{doc_id}/{filename}
    """
    cfg = settings()
    s3 = get_s3_client()
    s3_key = f"documents/{doc_id}/{filename}"

    if isinstance(file_obj, bytes):
        s3.put_object(
            Bucket=cfg.s3_bucket,
            Key=s3_key,
            Body=file_obj,
            ContentType=content_type,
        )
    else:
        s3.upload_fileobj(
            file_obj,
            cfg.s3_bucket,
            s3_key,
            ExtraArgs={"ContentType": content_type},
        )

    logger.info("Uploaded document to S3: s3://%s/%s", cfg.s3_bucket, s3_key)
    return s3_key


def download_document_from_s3(s3_key: str) -> bytes:
    """Retrieve raw document bytes from S3."""
    cfg = settings()
    s3 = get_s3_client()
    response = s3.get_object(Bucket=cfg.s3_bucket, Key=s3_key)
    return response["Body"].read()


def save_embeddings_to_s3(doc_id: str, embeddings_data: dict[str, Any]) -> str:
    """
    Store computed chunk embeddings and metadata as JSON in S3.
    Key format: embeddings/{doc_id}.json
    """
    cfg = settings()
    s3 = get_s3_client()
    s3_key = f"embeddings/{doc_id}.json"

    s3.put_object(
        Bucket=cfg.s3_bucket,
        Key=s3_key,
        Body=json.dumps(embeddings_data, ensure_ascii=False),
        ContentType="application/json",
    )
    logger.info("Saved embeddings to S3: s3://%s/%s", cfg.s3_bucket, s3_key)
    return s3_key


def get_embeddings_from_s3(doc_id: str) -> dict[str, Any] | None:
    """Load chunk embeddings and metadata JSON from S3."""
    cfg = settings()
    s3 = get_s3_client()
    s3_key = f"embeddings/{doc_id}.json"

    try:
        response = s3.get_object(Bucket=cfg.s3_bucket, Key=s3_key)
        return json.loads(response["Body"].read().decode("utf-8"))
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchKey":
            return None
        raise


def delete_from_s3(s3_key: str) -> bool:
    """Delete a file from S3."""
    cfg = settings()
    s3 = get_s3_client()
    try:
        s3.delete_object(Bucket=cfg.s3_bucket, Key=s3_key)
        return True
    except ClientError as e:
        logger.error("Failed to delete %s: %s", s3_key, e)
        return False
