"""
Atlas AI - Persistent Conversation Memory
Loads and persists multi-turn chat history per tenant and conversation session
in Render PostgreSQL (`chat_messages` table).
"""

from typing import Any
from app.config import settings
from app.db import connection, get_chat_history, save_chat_message
from app.observability import trace


@trace("memory_load")
def load_history(
    tenant_id: str,
    conversation_id: str,
    turns: int | None = None,
) -> list[dict[str, Any]]:
    """
    Retrieve the most recent conversation turns (user + assistant pairs)
    for a given tenant and conversation UUID in chronological order.
    """
    max_turns = turns if turns is not None else settings().memory_turns
    if max_turns <= 0:
        return []

    limit = max_turns * 2
    with connection() as conn:
        return get_chat_history(conn, tenant_id, conversation_id, limit=limit)


@trace("memory_append")
def append_turn(
    tenant_id: str,
    conversation_id: str,
    user_message: str,
    assistant_message: str,
) -> None:
    """Persist one complete user + assistant exchange in a single atomic DB transaction."""
    with connection() as conn:
        save_chat_message(conn, tenant_id, conversation_id, "user", user_message)
        save_chat_message(conn, tenant_id, conversation_id, "assistant", assistant_message)
