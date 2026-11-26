"""
Atlas AI - Database Access Layer
Manages PostgreSQL connections via Psycopg 3 with dict_row factory,
transaction safety, audit logging, session conversation memory,
HITL approvals, feedback, and PostgreSQL blob storage.
"""

import atexit
import logging
from contextlib import contextmanager
from functools import lru_cache
from typing import Any, Generator, Sequence
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool
from app.config import settings

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _pool() -> ConnectionPool:
    """Create and cache a warm PostgreSQL connection pool for low-latency queries."""
    url = settings().database_url.strip()
    if not url:
        raise RuntimeError("DATABASE_URL is missing or empty in .env")

    pool = ConnectionPool(
        conninfo=url,
        min_size=1,
        max_size=5,
        open=True,
        timeout=30.0,
        kwargs={"row_factory": dict_row, "connect_timeout": 20},
        check=ConnectionPool.check_connection,
    )
    atexit.register(pool.close)
    return pool


@contextmanager
def connection() -> Generator[psycopg.Connection, None, None]:
    """
    Context manager providing a pooled PostgreSQL connection configured with dict_row.
    Automatically commits on normal exit and rolls back on unhandled exceptions.
    """
    with _pool().connection() as conn:
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
    """Record an immutable audit event in audit_events table as JSONB."""
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
    """Persist a user or assistant chat message to chat_messages table."""
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
    """Fetch the most recent chat turns for a conversation in chronological order."""
    sql = """
        SELECT id, role, content, created_at
        FROM chat_messages
        WHERE tenant_id = %s AND conversation_id = %s
        ORDER BY id DESC
        LIMIT %s
    """
    cur = conn.execute(sql, (tenant_id, conversation_id, limit))
    rows = cur.fetchall()
    return list(reversed(rows))


def register_document(
    conn: psycopg.Connection,
    doc_id: str,
    tenant_id: str,
    title: str,
    owner_sub: str,
    allowed_groups: Sequence[str],
    s3_key: str,
    status: str = "pending",
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
    conn.execute("UPDATE documents SET status = %s WHERE id = %s", (status, doc_id))


def get_document(
    conn: psycopg.Connection,
    doc_id: str,
    tenant_id: str,
) -> dict[str, Any] | None:
    """Fetch a single document row scoped by tenant."""
    return conn.execute(
        "SELECT * FROM documents WHERE id = %s AND tenant_id = %s",
        (doc_id, tenant_id),
    ).fetchone()


def list_documents(
    conn: psycopg.Connection,
    tenant_id: str,
    owner_sub: str,
    groups: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """List non-retired private documents belonging exclusively to the authenticated user."""
    sql = """
        SELECT id, tenant_id, title, owner_sub, s3_key, status, current_version, created_at
        FROM documents
        WHERE tenant_id = %s
          AND owner_sub = %s
          AND status != 'retired'
        ORDER BY created_at DESC
    """
    return conn.execute(sql, (tenant_id, owner_sub)).fetchall()


# --- User Authentication Helpers ---

def create_user(
    conn: psycopg.Connection,
    email: str,
    password_hash: str,
    full_name: str = "",
) -> dict[str, Any]:
    """Create a new user account in users table."""
    return conn.execute(
        """
        INSERT INTO users (email, password_hash, full_name)
        VALUES (%s, %s, %s)
        RETURNING id, email, full_name, created_at
        """,
        (email.strip().lower(), password_hash, full_name.strip()),
    ).fetchone()


def get_user_by_email(
    conn: psycopg.Connection,
    email: str,
) -> dict[str, Any] | None:
    """Look up user account by normalized email address."""
    return conn.execute(
        "SELECT id, email, password_hash, full_name, created_at FROM users WHERE email = %s",
        (email.strip().lower(),),
    ).fetchone()


def get_user_by_id(
    conn: psycopg.Connection,
    user_id: str,
) -> dict[str, Any] | None:
    """Look up user account profile by UUID."""
    return conn.execute(
        "SELECT id, email, full_name, created_at FROM users WHERE id = %s",
        (user_id,),
    ).fetchone()


def delete_document_record(
    conn: psycopg.Connection,
    doc_id: str,
    tenant_id: str,
) -> dict[str, Any] | None:
    """Mark a document as retired or return deleted metadata."""
    return conn.execute(
        "DELETE FROM documents WHERE id = %s AND tenant_id = %s RETURNING id, s3_key, current_version, title",
        (doc_id, tenant_id),
    ).fetchone()


# --- PostgreSQL Blob Storage Helpers (Deployment-Ready S3 Alternative) ---

def put_stored_object(
    conn: psycopg.Connection,
    object_key: str,
    data: bytes,
    content_type: str = "application/octet-stream",
) -> None:
    """Persist binary object in PostgreSQL stored_objects table."""
    sql = """
        INSERT INTO stored_objects (object_key, content_type, data, size_bytes)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (object_key) DO UPDATE SET
            content_type = EXCLUDED.content_type,
            data = EXCLUDED.data,
            size_bytes = EXCLUDED.size_bytes,
            created_at = now()
    """
    conn.execute(sql, (object_key, content_type, data, len(data)))


def get_stored_object(
    conn: psycopg.Connection,
    object_key: str,
) -> tuple[bytes, str] | None:
    """Fetch binary object and content_type from PostgreSQL stored_objects table."""
    row = conn.execute(
        "SELECT data, content_type FROM stored_objects WHERE object_key = %s",
        (object_key,),
    ).fetchone()
    if not row:
        return None
    return bytes(row["data"]), row["content_type"]


def delete_stored_object(conn: psycopg.Connection, object_key: str) -> bool:
    """Delete binary object from stored_objects table."""
    cur = conn.execute("DELETE FROM stored_objects WHERE object_key = %s", (object_key,))
    return cur.rowcount > 0


# --- Feedback, Approvals & Metrics Helpers ---

def save_feedback(
    conn: psycopg.Connection,
    tenant_id: str,
    actor: str,
    rating: int,
    note: str = "",
) -> int:
    """Insert user feedback rating (-1, 0, or 1) and return its ID."""
    row = conn.execute(
        """
        INSERT INTO feedback (tenant_id, actor, rating, note)
        VALUES (%s, %s, %s, %s)
        RETURNING id
        """,
        (tenant_id, actor, rating, note),
    ).fetchone()
    return row["id"] if row else 0


def create_approval(
    conn: psycopg.Connection,
    tenant_id: str,
    requester: str,
    question: str,
    document_id: str | None = None,
) -> dict[str, Any]:
    """Create a pending HITL approval request optionally bound to a document."""
    return conn.execute(
        """
        INSERT INTO approvals (tenant_id, requester, question, document_id, status)
        VALUES (%s, %s, %s, %s, 'pending')
        RETURNING id, tenant_id, requester, question, document_id, status, created_at
        """,
        (tenant_id, requester, question, document_id),
    ).fetchone()


def list_approvals(
    conn: psycopg.Connection,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """List approval requests for a tenant ordered by most recent."""
    return conn.execute(
        """
        SELECT id, tenant_id, requester, question, document_id, status, reviewer, created_at, reviewed_at
        FROM approvals
        WHERE tenant_id = %s
        ORDER BY created_at DESC
        LIMIT 50
        """,
        (tenant_id,),
    ).fetchall()


def decide_approval(
    conn: psycopg.Connection,
    approval_id: str,
    tenant_id: str,
    status: str,
    reviewer: str,
) -> dict[str, Any] | None:
    """Approve or reject a pending HITL request."""
    return conn.execute(
        """
        UPDATE approvals
        SET status = %s, reviewer = %s, reviewed_at = now()
        WHERE id = %s AND tenant_id = %s
        RETURNING id, tenant_id, requester, question, document_id, status, reviewer, created_at, reviewed_at
        """,
        (status, reviewer, approval_id, tenant_id),
    ).fetchone()


# --- Per-Document Conversation Helpers ---

def create_conversation_for_document(
    conn: psycopg.Connection,
    tenant_id: str,
    owner_sub: str,
    document_id: str,
    title: str,
) -> dict[str, Any]:
    """Create (or return existing) 1-to-1 conversation bound to a single document."""
    return conn.execute(
        """
        INSERT INTO conversations (tenant_id, owner_sub, document_id, title)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (tenant_id, document_id) DO UPDATE SET
            title = EXCLUDED.title,
            updated_at = now()
        RETURNING id, tenant_id, owner_sub, document_id, title, created_at, updated_at
        """,
        (tenant_id, owner_sub, document_id, title),
    ).fetchone()


def list_conversations(
    conn: psycopg.Connection,
    tenant_id: str,
    owner_sub: str,
) -> list[dict[str, Any]]:
    """List all document-scoped conversations for the authenticated user, newest activity first."""
    return conn.execute(
        """
        SELECT
            c.id,
            c.document_id,
            c.title,
            d.status AS document_status,
            d.current_version,
            c.created_at,
            c.updated_at,
            (
                SELECT COUNT(*)
                FROM chat_messages m
                WHERE m.tenant_id = c.tenant_id AND m.conversation_id = c.id
            ) AS message_count
        FROM conversations c
        JOIN documents d ON d.id = c.document_id
        WHERE c.tenant_id = %s AND c.owner_sub = %s AND d.status != 'retired'
        ORDER BY c.updated_at DESC, c.created_at DESC
        """,
        (tenant_id, owner_sub),
    ).fetchall()


def get_conversation(
    conn: psycopg.Connection,
    conversation_id: str,
    tenant_id: str,
    owner_sub: str,
) -> dict[str, Any] | None:
    """Fetch a single conversation owned by the user."""
    return conn.execute(
        """
        SELECT c.id, c.tenant_id, c.owner_sub, c.document_id, c.title, d.status AS document_status, c.created_at, c.updated_at
        FROM conversations c
        JOIN documents d ON d.id = c.document_id
        WHERE c.id = %s AND c.tenant_id = %s AND c.owner_sub = %s
        """,
        (conversation_id, tenant_id, owner_sub),
    ).fetchone()


def get_conversation_by_document(
    conn: psycopg.Connection,
    document_id: str,
    tenant_id: str,
    owner_sub: str,
) -> dict[str, Any] | None:
    """Fetch the conversation bound to a specific document."""
    return conn.execute(
        """
        SELECT c.id, c.tenant_id, c.owner_sub, c.document_id, c.title, d.status AS document_status, c.created_at, c.updated_at
        FROM conversations c
        JOIN documents d ON d.id = c.document_id
        WHERE c.document_id = %s AND c.tenant_id = %s AND c.owner_sub = %s
        """,
        (document_id, tenant_id, owner_sub),
    ).fetchone()


def touch_conversation(conn: psycopg.Connection, conversation_id: str) -> None:
    """Bump conversation updated_at timestamp on new message activity."""
    conn.execute("UPDATE conversations SET updated_at = now() WHERE id = %s", (conversation_id,))


def delete_conversation_messages(
    conn: psycopg.Connection,
    tenant_id: str,
    conversation_id: str,
) -> None:
    """Remove all chat messages for a deleted document conversation."""
    conn.execute(
        "DELETE FROM chat_messages WHERE tenant_id = %s AND conversation_id = %s",
        (tenant_id, conversation_id),
    )


def get_metrics(conn: psycopg.Connection, tenant_id: str) -> dict[str, Any]:
    """Aggregate operational, feedback, approval, and audit metrics for a tenant."""
    doc_rows = conn.execute(
        "SELECT status, COUNT(*) AS count FROM documents WHERE tenant_id = %s GROUP BY status",
        (tenant_id,),
    ).fetchall()
    doc_counts = {row["status"]: int(row["count"]) for row in doc_rows}

    msg_row = conn.execute(
        "SELECT COUNT(*) AS count FROM chat_messages WHERE tenant_id = %s",
        (tenant_id,),
    ).fetchone()

    fb_rows = conn.execute(
        "SELECT rating, COUNT(*) AS count FROM feedback WHERE tenant_id = %s GROUP BY rating",
        (tenant_id,),
    ).fetchall()
    fb_counts = {str(row["rating"]): int(row["count"]) for row in fb_rows}

    appr_rows = conn.execute(
        "SELECT status, COUNT(*) AS count FROM approvals WHERE tenant_id = %s GROUP BY status",
        (tenant_id,),
    ).fetchall()
    appr_counts = {row["status"]: int(row["count"]) for row in appr_rows}

    audit_rows = conn.execute(
        """
        SELECT id, actor, action, detail, created_at
        FROM audit_events
        WHERE tenant_id = %s
        ORDER BY id DESC
        LIMIT 20
        """,
        (tenant_id,),
    ).fetchall()

    return {
        "documents": doc_counts,
        "total_documents": sum(doc_counts.values()),
        "total_messages": int(msg_row["count"]) if msg_row else 0,
        "feedback": fb_counts,
        "approvals": appr_counts,
        "recent_audit": audit_rows,
    }
