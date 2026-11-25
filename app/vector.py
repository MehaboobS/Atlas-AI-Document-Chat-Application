"""
Atlas AI - Vector Database Layer (Pinecone Serverless)
Manages dense vector indexing, multi-tenant namespace partitioning,
Access Control List (ACL) metadata filtering, and semantic similarity search.
"""

import logging
import time
from functools import lru_cache
from typing import Any, Sequence
from pinecone import Pinecone, ServerlessSpec
from app.config import settings
from app.observability import trace

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def index():
    """
    Return a cached, initialized Pinecone Index instance.
    Safe for repeated requests in one process lifecycle.
    Idempotently creates the serverless index if it doesn't exist.
    """
    s = settings()
    if not s.pinecone_api_key:
        raise RuntimeError("PINECONE_API_KEY is missing from .env")
    if not s.pinecone_index_name:
        raise RuntimeError("PINECONE_INDEX_NAME is missing from .env")

    pc = Pinecone(api_key=s.pinecone_api_key)
    name = s.pinecone_index_name

    # Describe rather than list to avoid listing all project indexes
    try:
        info = pc.describe_index(name)
    except Exception as exc:
        # Only create on an actual 404 Not Found; never suppress auth/network errors
        if getattr(exc, "status", None) != 404:
            raise

        log.info(
            "Creating Pinecone index %s (dimension=%d, metric=cosine, region=%s)",
            name,
            s.embed_dim,
            s.pinecone_region,
        )
        try:
            pc.create_index(
                name=name,
                dimension=s.embed_dim,
                metric="cosine",
                spec=ServerlessSpec(cloud=s.pinecone_cloud, region=s.pinecone_region),
            )
        except Exception as create_exc:
            # Another process/worker may have created the index concurrently
            if getattr(create_exc, "status", None) != 409:
                raise
        info = pc.describe_index(name)

    # Validate vector dimension matches our embedding model (e.g. 384 for bge-small)
    if int(info.dimension) != s.embed_dim:
        raise ValueError(
            f"Pinecone index '{name}' dimension is {info.dimension}, expected {s.embed_dim}. "
            "Update EMBED_DIM in .env or use a matching index name."
        )

    if str(info.metric).lower() != "cosine":
        raise ValueError(f"Pinecone index '{name}' must use cosine metric, got '{info.metric}'")

    # Await index readiness up to configured timeout
    deadline = time.monotonic() + s.pinecone_ready_timeout
    while True:
        status_obj = pc.describe_index(name).status
        is_ready = status_obj.get("ready") if isinstance(status_obj, dict) else getattr(status_obj, "ready", False)
        if is_ready:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(f"Pinecone index '{name}' was not ready within {s.pinecone_ready_timeout}s")
        time.sleep(3)

    log.info("Pinecone index '%s' ready", name)
    return pc.Index(name)


def namespace(tenant: str) -> str:
    """Return isolated Pinecone namespace for multi-tenant data partitioning."""
    return f"tenant-{tenant}"


@trace("upsert")
def upsert(tenant: str, records: Sequence[dict[str, Any]], batch_size: int = 100) -> dict[str, Any]:
    """
    Upsert vector records into the tenant-specific namespace.
    Batches records to prevent exceeding Pinecone's payload limit (2MB).
    Record format: {'id': str, 'values': list[float], 'metadata': dict}
    """
    idx = index()
    ns = namespace(tenant)
    total_upserted = 0

    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        res = idx.upsert(vectors=batch, namespace=ns)
        total_upserted += getattr(res, "upserted_count", len(batch))

    return {"upserted_count": total_upserted}


@trace("search")
def search(
    tenant: str,
    vector: Sequence[float],
    groups: Sequence[str],
    owner: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """
    Search for similar vectors within tenant namespace enforcing ACL metadata boundaries.
    Access is granted if the user belongs to allowed_groups OR is the document owner.
    """
    groups_list = list(groups) if groups else []

    # Build access control list (ACL) metadata filter
    if groups_list:
        acl_filter = {
            "$or": [
                {"allowed_groups": {"$in": groups_list}},
                {"owner_sub": {"$eq": owner}},
            ]
        }
    else:
        acl_filter = {"owner_sub": {"$eq": owner}}

    result = index().query(
        vector=list(vector),
        top_k=limit,
        include_metadata=True,
        namespace=namespace(tenant),
        filter=acl_filter,
    )

    return [
        {
            "id": match.id,
            "score": float(match.score),
            "metadata": match.metadata or {},
        }
        for match in result.matches
    ]


def delete_document(tenant: str, ids: Sequence[str]) -> None:
    """Delete vector records by ID from tenant namespace."""
    if ids:
        index().delete(ids=list(ids), namespace=namespace(tenant))
        log.info("Deleted %d vectors from namespace %s", len(ids), namespace(tenant))
