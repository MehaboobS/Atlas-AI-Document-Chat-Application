"""
Atlas AI - Database Access Layer
Manages PostgreSQL connections via Psycopg 3 with dict_row factory,
transaction safety, audit logging, and session conversation memory.
"""

import json
import logging
from contextlib import contextmanager
from typing import Any, Generator, Sequence
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.config import settings

logger = logging.getLogger(__name__)


@contextmanager
def connection() -> Generator[psycopg.Connection, None, None]:
    """
    Context manager providing a PostgreSQL connection configured with dict_row.
    Automatically commits on normal exit and rolls back on unhandled exceptions.
    """
    url = settings().database_url.strip()
    if not url:
        raise RuntimeError("DATABASE_URL is missing or empty in .env")

    with psycopg.connect(url, row_factory=dict_row, connect_timeout=15) as conn:
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def audit(
    conn: psycopg.Connection,
    actor: str,
    tenant_id: str,
    action: str,
    detail: dict[str, Any],
) -> None:
    """
    Record an immutable audit event in audit_events table.
    detail is stored as native JSONB.
    """
    sql = """
        INSERT INTO audit_events (actor, tenant_id, action, detail)
        VALUES (%s, %s, %s, %s)
    """
    conn.execute(sql, (actor, tenant_id, action, Jsonb(detail)))
    logger.debug("Audit logged: tenant=%s action=%s actor=%s", tenant_id, action, actor)


def save_chat_message(
    conn: psycopg.Connection,
    tenant_id: str,
    conversation_id: str,
    role: str,
    content: str,
) -> int:
    """
    Persist a user or assistant chat message to chat_messages table.
    Returns the generated message id.
    """
    sql = """
        INSERT INTO chat_messages (tenant_id, conversation_id, role, content)
        VALUES (%s, %s, %s, %s)
        RETURNING id
    """
    cur = conn.execute(sql, (tenant_id, conversation_id, role, content))
    row = cur.fetchone()
    return row["id"] if row else 0


def get_chat_history(
    conn: psycopg.Connection,
    tenant_id: str,
    conversation_id: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """
    Fetch the most recent chat turns for a conversation in chronological order.
    """
    sql = """
        SELECT id, role, content, created_at
        FROM chat_messages
        WHERE tenant_id = %s AND conversation_id = %s
        ORDER BY id DESC
        LIMIT %s
    """
    cur = conn.execute(sql, (tenant_id, conversation_id, limit))
    rows = cur.fetchall()
    # Reverse to return chronological order (oldest first)
    return list(reversed(rows))


def register_document(
    conn: psycopg.Connection,
    doc_id: str,
    tenant_id: str,
    title: str,
    owner_sub: str,
    allowed_groups: Sequence[str],
    s3_key: str,
    status: str = "processing",
) -> None:
    """Register or update a document in the documents table."""
    sql = """
        INSERT INTO documents (
            id, tenant_id, title, owner_sub, allowed_groups, s3_key, status
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            title = EXCLUDED.title,
            status = EXCLUDED.status,
            current_version = documents.current_version + 1
    """
    conn.execute(
        sql,
        (doc_id, tenant_id, title, owner_sub, list(allowed_groups), s3_key, status),
    )


def update_document_status(
    conn: psycopg.Connection,
    doc_id: str,
    status: str,
) -> None:
    """Update document lifecycle status (pending, processing, ready, failed, retired)."""
    sql = "UPDATE documents SET status = %s WHERE id = %s"
    conn.execute(sql, (status, doc_id))
