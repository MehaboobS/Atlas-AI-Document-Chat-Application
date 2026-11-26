"""
Atlas AI - Document Ingestion Worker
Processes uploaded documents stored in the active storage backend
(PostgreSQL blob storage, Cloudflare R2, Backblaze B2, or AWS S3):
1. Downloads document bytes via app.storage.
2. Extracts raw text from PDF, DOCX, or TXT.
3. Chunks text into overlapping windows for semantic continuity.
4. Generates dense vector embeddings using local open-source Hugging Face FastEmbed.
5. Backs up chunk embeddings JSON via app.storage.
6. Upserts vector embeddings with ACL metadata into Pinecone.
7. Updates document status in PostgreSQL and logs an audit event.

Note: Files inside `worker/` do not auto-execute on their own.
In FastAPI, `process_s3_key` is triggered asynchronously via BackgroundTasks
when POST /api/documents/{document_id}/ingest is called (or via a dedicated worker queue).
"""

import io
import logging
from pathlib import Path
from typing import Generator
from docx import Document
from pypdf import PdfReader
from app.aws import save_embeddings_to_s3
from app.config import settings
from app.db import audit, connection
from app.observability import initialize, trace
from app.rag import embed_batch
from app.storage import get_object_bytes
from app.vector import upsert

initialize()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("atlas.worker")


def parse(blob: bytes, suffix: str) -> list[tuple[int, str]]:
    """
    Extract page-indexed text from PDF, DOCX, or plain text bytes.
    Returns list of (page_number, page_text).
    """
    if suffix == ".pdf":
        reader = PdfReader(io.BytesIO(blob))
        return [(i + 1, page.extract_text() or "") for i, page in enumerate(reader.pages)]

    if suffix == ".docx":
        doc = Document(io.BytesIO(blob))
        full_text = "\n".join(p.text for p in doc.paragraphs if p.text)
        return [(1, full_text)]

    return [(1, blob.decode("utf-8", errors="ignore"))]


def chunks(text: str, size: int = 1200, overlap: int = 180) -> Generator[str, None, None]:
    """
    Sliding window chunker: splits text into overlapping character slices
    while normalizing excessive whitespace.
    """
    normalized_text = " ".join(text.split())
    pos = 0
    while pos < len(normalized_text):
        chunk_slice = normalized_text[pos : pos + size]
        yield chunk_slice
        if pos + size >= len(normalized_text):
            break
        pos += size - overlap


@trace("process_s3_key")
def process_s3_key(key: str) -> None:
    """
    Ingest a document referenced by its storage key (`s3_key` column).
    Safe and idempotent: skips if already ready or currently processing.
    """
    s = settings()

    with connection() as conn:
        doc = conn.execute(
            "SELECT * FROM documents WHERE s3_key=%s FOR UPDATE",
            (key,),
        ).fetchone()

        if not doc or doc["status"] == "ready":
            log.info("Document for key '%s' is already ready or not found. Skipping.", key)
            return

        if doc["status"] not in ("pending", "failed"):
            log.info("Document for key '%s' is in state '%s'. Skipping.", key, doc["status"])
            return

        conn.execute("UPDATE documents SET status='processing' WHERE id=%s", (doc["id"],))

    try:
        # 1. Fetch bytes from configured storage backend (PostgreSQL / R2 / B2 / S3 / Local)
        blob = get_object_bytes(key)
        if len(blob) > s.max_upload_bytes:
            raise ValueError(f"Upload exceeds maximum allowed size of {s.max_upload_bytes} bytes")

        suffix = Path(key).suffix.lower()
        if suffix == ".pdf" and not blob.startswith(b"%PDF"):
            raise ValueError("Invalid PDF format: missing %PDF magic header")

        # 2. Extract text and split into chunks
        pages = parse(blob, suffix)
        parts = [(page, part) for page, text in pages for part in chunks(text) if part.strip()]

        if not parts:
            raise ValueError("No extractable text found in document; scanned PDFs require OCR")

        # 3. Generate embeddings using open-source Hugging Face FastEmbed
        chunk_texts = [part for _, part in parts]
        embeddings = embed_batch(chunk_texts)

        # 4. Build vector records with ACL metadata for Pinecone
        vectors = []
        for i, ((page, part), vector) in enumerate(zip(parts, embeddings)):
            vectors.append({
                "id": f"{doc['id']}:{doc['current_version']}:{i}",
                "values": vector,
                "metadata": {
                    "document_id": str(doc["id"]),
                    "title": doc["title"],
                    "page": page,
                    "text": part,
                    "owner_sub": doc["owner_sub"],
                },
            })

        # 5. Upsert into Pinecone in batches of 50
        for i in range(0, len(vectors), 50):
            upsert(doc["tenant_id"], vectors[i : i + 50])

        # 6. Backup chunk metadata to active storage backend
        backup_payload = {
            "document_id": str(doc["id"]),
            "tenant_id": doc["tenant_id"],
            "title": doc["title"],
            "chunks_count": len(parts),
            "chunks": [
                {"chunk_index": idx, "page": page, "text": part}
                for idx, (page, part) in enumerate(parts)
            ],
        }
        save_embeddings_to_s3(str(doc["id"]), backup_payload)

        # 7. Mark document 'ready' and write audit event
        with connection() as conn:
            conn.execute("UPDATE documents SET status='ready' WHERE id=%s", (doc["id"],))
            audit(
                conn,
                doc["owner_sub"],
                doc["tenant_id"],
                "ingest_ready",
                {"document_id": str(doc["id"]), "chunks": len(parts)},
            )

        log.info("Successfully ingested document %s (%s) with %d chunks", doc["id"], doc["title"], len(parts))

    except Exception:
        log.exception("Ingestion failed for key '%s'", key)
        try:
            with connection() as conn:
                conn.execute("UPDATE documents SET status='failed' WHERE id=%s", (doc["id"],))
        except Exception:
            log.exception("Could not update status='failed' for key '%s'", key)
        raise
