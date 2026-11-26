"""
Atlas AI - LangGraph Agentic RAG Workflow
Assembles the stateful graph:
  input_check -> (blocked | hitl_gate | load_memory -> retrieve -> generate -> output_check)

Every node is instrumented with GlassFlow Rius `@trace` spans (without exposing secrets or payloads).
"""

import logging
from functools import lru_cache
from typing import Any, Sequence, TypedDict
from langgraph.graph import END, START, StateGraph
from app.config import settings
from app.db import audit, connection, create_approval, touch_conversation
from app.guardrails import check_input, check_output
from app.memory import append_turn, load_history
from app.observability import trace
from app.rag import answer as generate_rag_answer
from app.rag import embed
from app.vector import search as vector_search

logger = logging.getLogger(__name__)


class GraphState(TypedDict, total=False):
    tenant_id: str
    owner_sub: str
    groups: list[str]
    conversation_id: str
    document_id: str | None
    question: str
    bypass_hitl: bool
    history: list[dict[str, Any]]
    guardrail_blocked: bool
    guardrail_reason: str
    requires_approval: bool
    approval_reason: str
    approval_id: str | None
    matches: list[dict[str, Any]]
    evidence: str
    citations: list[dict[str, Any]]
    raw_answer: str
    answer: str
    status: str


@trace("node_input_check")
def input_check_node(state: GraphState) -> dict[str, Any]:
    """Run deterministic input guardrails (injection check & HITL sensitivity check)."""
    result = check_input(state["question"])
    if not result["allowed"]:
        with connection() as conn:
            audit(
                conn,
                state["owner_sub"],
                state["tenant_id"],
                "guardrail_blocked",
                {"reason": result["reason"]},
            )
        return {
            "guardrail_blocked": True,
            "guardrail_reason": result["reason"],
            "answer": result["reason"],
            "citations": [],
            "status": "blocked",
        }

    needs_hitl = bool(
        result["requires_approval"]
        and settings().hitl_enabled
        and not state.get("bypass_hitl", False)
    )
    return {
        "question": result["sanitized"],
        "guardrail_blocked": False,
        "requires_approval": needs_hitl,
        "approval_reason": result["reason"],
    }


@trace("node_hitl_gate")
def hitl_gate_node(state: GraphState) -> dict[str, Any]:
    """Create a pending Human-in-the-Loop approval record in PostgreSQL and pause response."""
    with connection() as conn:
        row = create_approval(
            conn,
            state["tenant_id"],
            state["owner_sub"],
            state["question"],
            document_id=state.get("document_id"),
        )
        approval_id = str(row["id"])
        audit(
            conn,
            state["owner_sub"],
            state["tenant_id"],
            "hitl_requested",
            {"approval_id": approval_id},
        )

    msg = (
        f"This query involves sensitive topics and requires supervisor approval before answering.\n"
        f"Approval Request ID: {approval_id}\n"
        f"Please review and approve it in the Approvals tab."
    )
    return {
        "approval_id": approval_id,
        "answer": msg,
        "citations": [],
        "status": "pending_approval",
    }


@trace("node_load_memory")
def load_memory_node(state: GraphState) -> dict[str, Any]:
    """Load recent conversation turns from PostgreSQL memory."""
    history = load_history(state["tenant_id"], state["conversation_id"])
    return {"history": history}


@trace("node_retrieve")
def retrieve_node(state: GraphState) -> dict[str, Any]:
    """Embed the query locally and perform private single-document similarity search in Pinecone."""
    cfg = settings()
    query_vec = embed(state["question"])
    raw_matches = vector_search(
        tenant=state["tenant_id"],
        vector=query_vec,
        owner=state["owner_sub"],
        document_id=state.get("document_id"),
        limit=5,
    )

    filtered = [m for m in raw_matches if m.get("score", 0.0) >= cfg.min_relevance]

    evidence_blocks = []
    citations = []
    for idx, match in enumerate(filtered, start=1):
        meta = match.get("metadata", {})
        title = meta.get("title", "Document")
        page = meta.get("page", 1)
        text = meta.get("text", "")
        evidence_blocks.append(f"[{idx}] (Source: {title}, Page {page})\n{text}")
        citations.append({
            "index": idx,
            "document_id": meta.get("document_id", ""),
            "title": title,
            "page": page,
            "score": round(float(match.get("score", 0.0)), 4),
            "snippet": text[:220] + ("..." if len(text) > 220 else ""),
        })

    return {
        "matches": filtered,
        "evidence": "\n\n".join(evidence_blocks),
        "citations": citations,
    }


@trace("node_generate")
def generate_node(state: GraphState) -> dict[str, Any]:
    """Synthesize a cited answer with Google Gemini when authorized evidence is found."""
    matches = state.get("matches") or []
    if not matches:
        return {
            "raw_answer": (
                "I cannot find an authorized answer in the uploaded documents for your access permissions. "
                "Please upload a relevant document or verify group permissions."
            )
        }

    generated = generate_rag_answer(
        question=state["question"],
        evidence=state.get("evidence", ""),
        history=state.get("history", []),
    )
    return {"raw_answer": generated}


@trace("node_output_check")
def output_check_node(state: GraphState) -> dict[str, Any]:
    """Sanitize response, persist conversation turn to PostgreSQL memory, and write audit log."""
    matches = state.get("matches") or []
    checked = check_output(state.get("raw_answer", ""), evidence_count=len(matches))
    final_answer = checked["text"]

    append_turn(
        tenant_id=state["tenant_id"],
        conversation_id=state["conversation_id"],
        user_message=state["question"],
        assistant_message=final_answer,
    )

    with connection() as conn:
        if state.get("conversation_id"):
            touch_conversation(conn, state["conversation_id"])
        audit(
            conn,
            state["owner_sub"],
            state["tenant_id"],
            "chat_answered",
            {
                "conversation_id": state["conversation_id"],
                "document_id": state.get("document_id"),
                "citations": len(state.get("citations") or []),
                "redacted": checked["redacted"],
            },
        )

    return {
        "answer": final_answer,
        "status": "completed",
    }


def _route_after_input(state: GraphState) -> str:
    if state.get("guardrail_blocked"):
        return END
    if state.get("requires_approval"):
        return "hitl_gate"
    return "load_memory"


@lru_cache(maxsize=1)
def build_graph():
    """Compile and cache the Atlas LangGraph workflow."""
    workflow = StateGraph(GraphState)

    workflow.add_node("input_check", input_check_node)
    workflow.add_node("hitl_gate", hitl_gate_node)
    workflow.add_node("load_memory", load_memory_node)
    workflow.add_node("retrieve", retrieve_node)
    workflow.add_node("generate", generate_node)
    workflow.add_node("output_check", output_check_node)

    workflow.add_edge(START, "input_check")
    workflow.add_conditional_edges(
        "input_check",
        _route_after_input,
        {
            END: END,
            "hitl_gate": "hitl_gate",
            "load_memory": "load_memory",
        },
    )
    workflow.add_edge("hitl_gate", END)
    workflow.add_edge("load_memory", "retrieve")
    workflow.add_edge("retrieve", "generate")
    workflow.add_edge("generate", "output_check")
    workflow.add_edge("output_check", END)

    return workflow.compile()


@trace("run_chat")
def run_chat(
    tenant_id: str,
    owner_sub: str,
    conversation_id: str,
    question: str,
    document_id: str | None = None,
    groups: Sequence[str] = (),
    bypass_hitl: bool = False,
) -> dict[str, Any]:
    """Execute the full RAG LangGraph pipeline scoped to a single document conversation."""
    app_graph = build_graph()
    result = app_graph.invoke({
        "tenant_id": tenant_id,
        "owner_sub": owner_sub,
        "groups": list(groups),
        "conversation_id": conversation_id,
        "document_id": document_id,
        "question": question,
        "bypass_hitl": bypass_hitl,
    })

    return {
        "status": result.get("status", "completed"),
        "answer": result.get("answer", ""),
        "citations": result.get("citations", []),
        "approval_id": result.get("approval_id"),
        "conversation_id": conversation_id,
        "document_id": document_id,
    }
