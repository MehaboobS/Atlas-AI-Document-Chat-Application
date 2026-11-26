"""
Atlas Enterprise AI - FastAPI Web Server & API
Features:
- Email & password signup/login with HS256 JWT Bearer authentication
- 1-to-1 per-document conversation sessions (each document gets its own dedicated chat)
- Strict per-user isolation (no shared groups, private Pinecone namespaces + owner filters)
- Presigned-style PUT uploads backed by persistent PostgreSQL storage (or S3/R2)
- LangGraph RAG chat with guardrails, memory, HITL approvals, and Rius telemetry
"""

from app.observability import initialize

# Initialize Rius telemetry before loading instrumented RAG/Vector/Worker modules
initialize()

import logging
import re
import uuid
from pathlib import Path
from typing import Any
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from app.auth import AuthUser, create_access_token, get_current_user, hash_password, verify_password
from app.aws import get_embeddings_from_s3
from app.config import settings
from app.db import (
    audit,
    connection,
    create_conversation_for_document,
    create_user,
    decide_approval,
    delete_conversation_messages,
    delete_document_record,
    get_conversation,
    get_conversation_by_document,
    get_document,
    get_metrics,
    get_user_by_email,
    get_user_by_id,
    list_approvals,
    list_conversations,
    list_documents,
    register_document,
    save_feedback,
)
from app.graph import run_chat
from app.guardrails import is_delete_action
from app.memory import load_history
from app.storage import create_upload_target, delete_object, put_object
from app.vector import delete_document
from worker.ingest import process_s3_key

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("atlas.api")

BASE_DIR = Path(__file__).resolve().parent
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app = FastAPI(
    title="Atlas Enterprise AI",
    description="Private Per-Document RAG Assistant with JWT Auth, Guardrails, HITL, and Rius Observability",
    version="2.1.0",
)

cors_list = [o.strip() for o in settings().cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


# --- Pydantic Schemas ---

class SignupRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=6, max_length=128)
    full_name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)


class UploadUrlRequest(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    title: str = Field(..., min_length=1, max_length=255)
    content_type: str = "application/octet-stream"


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    conversation_id: str | None = None
    document_id: str | None = None


class FeedbackRequest(BaseModel):
    rating: int = Field(..., ge=-1, le=1)
    note: str = Field(default="", max_length=1000)


class ApprovalDecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")


# --- UI & Health ---

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    """Render the Atlas AI workspace UI."""
    cfg = settings()
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "storage_backend": cfg.storage_backend,
            "llm_model": cfg.gemini_model,
            "embed_model": cfg.hf_embed_model,
        },
    )


@app.get("/health")
def health():
    """Health check verifying database connectivity and active stack configuration."""
    cfg = settings()
    try:
        with connection() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        logger.error("Health check DB failure: %s", exc)
        raise HTTPException(status_code=503, detail="Database unavailable")

    return {
        "status": "ok",
        "database": True,
        "storage_backend": cfg.storage_backend,
        "llm_model": cfg.gemini_model,
        "embed_model": cfg.hf_embed_model,
    }


# --- Authentication Routes (Email + Password + JWT) ---

@app.post("/api/auth/signup", status_code=status.HTTP_201_CREATED)
def signup(payload: SignupRequest):
    """Register a new user account with email and password, returning a JWT access token."""
    email = payload.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Please enter a valid email address.")

    pw_hash = hash_password(payload.password)

    with connection() as conn:
        existing = get_user_by_email(conn, email)
        if existing:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")

        user = create_user(conn, email=email, password_hash=pw_hash, full_name=payload.full_name)
        user_id = str(user["id"])
        audit(conn, email, user_id, "user_signup", {"user_id": user_id})

    token = create_access_token(user_id=user_id, email=user["email"], full_name=user["full_name"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user_id,
            "email": user["email"],
            "full_name": user["full_name"],
        },
    }


@app.post("/api/auth/login")
def login(payload: LoginRequest):
    """Authenticate an existing user by email & password and issue a JWT token."""
    email = payload.email.strip().lower()
    with connection() as conn:
        user = get_user_by_email(conn, email)
        if not user or not verify_password(payload.password, user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid email or password.")

        user_id = str(user["id"])
        audit(conn, email, user_id, "user_login", {"user_id": user_id})

    token = create_access_token(user_id=user_id, email=user["email"], full_name=user["full_name"])
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": user_id,
            "email": user["email"],
            "full_name": user["full_name"],
        },
    }


@app.get("/api/auth/me")
def me(user: AuthUser = Depends(get_current_user)):
    """Return profile of the currently authenticated user."""
    with connection() as conn:
        db_user = get_user_by_id(conn, user.user_id)
    if not db_user:
        raise HTTPException(status_code=401, detail="User account no longer exists.")
    return {
        "id": str(db_user["id"]),
        "email": db_user["email"],
        "full_name": db_user["full_name"],
    }


# --- Private Per-User Document Upload & Conversation Creation ---

@app.post("/api/documents/upload-url")
def create_document_upload_url(
    payload: UploadUrlRequest,
    user: AuthUser = Depends(get_current_user),
):
    """
    Create a pending private document entry for the authenticated user
    and return a PUT upload target.
    """
    safe_name = Path(payload.filename).name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type '{suffix}'. Allowed: {sorted(ALLOWED_EXTENSIONS)}",
        )

    doc_id = str(uuid.uuid4())
    storage_key = f"documents/{user.tenant_id}/{doc_id}/{safe_name}"

    with connection() as conn:
        register_document(
            conn=conn,
            doc_id=doc_id,
            tenant_id=user.tenant_id,
            title=payload.title.strip(),
            owner_sub=user.user_id,
            allowed_groups=[],
            s3_key=storage_key,
            status="pending",
        )
        audit(
            conn,
            user.email,
            user.tenant_id,
            "upload_initiated",
            {"document_id": doc_id, "filename": safe_name},
        )

    target = create_upload_target(storage_key, content_type=payload.content_type)
    return {
        "document_id": doc_id,
        "storage_key": storage_key,
        **target,
    }


@app.put("/api/storage/upload/{storage_key:path}")
async def upload_blob_endpoint(
    storage_key: str,
    request: Request,
    user: AuthUser = Depends(get_current_user),
):
    """
    Accept authenticated browser PUT uploads for PostgreSQL or Local storage backends,
    enforcing strict ownership verification.
    """
    cfg = settings()
    if cfg.storage_backend.strip().lower() == "s3":
        raise HTTPException(status_code=400, detail="Direct upload disabled when STORAGE_BACKEND=s3")

    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="Uploaded file body is empty")
    if len(data) > cfg.max_upload_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {cfg.max_upload_bytes} bytes",
        )

    with connection() as conn:
        doc = conn.execute(
            "SELECT id, owner_sub, tenant_id FROM documents WHERE s3_key = %s",
            (storage_key,),
        ).fetchone()
    if not doc:
        raise HTTPException(status_code=404, detail="Document upload slot not found")
    if doc["owner_sub"] != user.user_id or doc["tenant_id"] != user.tenant_id:
        raise HTTPException(status_code=403, detail="Not authorized to upload to this document slot")

    content_type = request.headers.get("content-type", "application/octet-stream")
    put_object(storage_key, data, content_type=content_type)

    return {
        "status": "uploaded",
        "storage_key": storage_key,
        "size_bytes": len(data),
    }


@app.post("/api/documents/{document_id}/ingest")
def trigger_ingestion(
    document_id: str,
    background_tasks: BackgroundTasks,
    sync: bool = False,
    user: AuthUser = Depends(get_current_user),
):
    """
    Trigger document ingestion and bind a brand-new dedicated conversation
    to this single document.
    """
    try:
        uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid document UUID")

    with connection() as conn:
        doc = get_document(conn, document_id, user.tenant_id)
        if not doc or doc["owner_sub"] != user.user_id:
            raise HTTPException(status_code=404, detail="Document not found")

    if sync:
        process_s3_key(doc["s3_key"])
        with connection() as conn:
            updated = get_document(conn, document_id, user.tenant_id)
            conv = create_conversation_for_document(
                conn,
                tenant_id=user.tenant_id,
                owner_sub=user.user_id,
                document_id=document_id,
                title=doc["title"],
            )
        return {
            "document_id": document_id,
            "conversation_id": str(conv["id"]),
            "title": conv["title"],
            "status": updated["status"] if updated else "ready",
        }

    with connection() as conn:
        conv = create_conversation_for_document(
            conn,
            tenant_id=user.tenant_id,
            owner_sub=user.user_id,
            document_id=document_id,
            title=doc["title"],
        )
    background_tasks.add_task(process_s3_key, doc["s3_key"])
    return {
        "document_id": document_id,
        "conversation_id": str(conv["id"]),
        "title": conv["title"],
        "status": "processing",
    }


@app.get("/api/documents")
def get_documents_list(user: AuthUser = Depends(get_current_user)):
    """List private documents belonging strictly to the authenticated user."""
    with connection() as conn:
        rows = list_documents(conn, user.tenant_id, user.user_id)
    return {"documents": rows}


@app.get("/api/conversations")
def get_conversations_list(user: AuthUser = Depends(get_current_user)):
    """List all per-document conversations for the sidebar, ordered by latest activity."""
    with connection() as conn:
        rows = list_conversations(conn, user.tenant_id, user.user_id)
    return {"conversations": rows}


def _purge_document(
    conn,
    doc: dict[str, Any],
    tenant_id: str,
    actor: str,
    audit_action: str = "document_deleted",
) -> None:
    """Delete a document, its conversation messages, Pinecone vectors, and storage blobs."""
    document_id = str(doc["id"])
    conv = get_conversation_by_document(conn, document_id, tenant_id, doc["owner_sub"])
    if conv:
        delete_conversation_messages(conn, tenant_id, str(conv["id"]))

    backup = get_embeddings_from_s3(document_id)
    chunks_count = int(backup.get("chunks_count", 0)) if backup else 0
    vector_ids = [f"{document_id}:{doc['current_version']}:{i}" for i in range(chunks_count)]

    if vector_ids:
        delete_document(tenant_id, vector_ids)

    delete_object(doc["s3_key"])
    delete_object(f"embeddings/{document_id}.json")
    delete_document_record(conn, document_id, tenant_id)
    audit(
        conn,
        actor,
        tenant_id,
        audit_action,
        {"document_id": document_id, "title": doc["title"]},
    )


@app.delete("/api/documents/{document_id}")
def remove_document(
    document_id: str,
    user: AuthUser = Depends(get_current_user),
):
    """Delete a private document and its associated conversation owned by the user."""
    try:
        uuid.UUID(document_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid document UUID")

    with connection() as conn:
        doc = get_document(conn, document_id, user.tenant_id)
        if not doc or doc["owner_sub"] != user.user_id:
            raise HTTPException(status_code=404, detail="Document not found")

        _purge_document(conn, doc, user.tenant_id, user.email)

    return {"status": "deleted", "document_id": document_id}


# --- Document-Scoped Chat, Memory, Feedback, HITL & Metrics ---

@app.post("/api/chat")
def chat(
    payload: ChatRequest,
    user: AuthUser = Depends(get_current_user),
):
    """
    Execute the LangGraph RAG pipeline scoped strictly to the single document
    bound to the selected conversation.
    """
    conv = None
    with connection() as conn:
        if payload.conversation_id:
            try:
                uuid.UUID(payload.conversation_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="conversation_id must be a valid UUID")
            conv = get_conversation(conn, payload.conversation_id, user.tenant_id, user.user_id)
        elif payload.document_id:
            try:
                uuid.UUID(payload.document_id)
            except ValueError:
                raise HTTPException(status_code=400, detail="document_id must be a valid UUID")
            conv = get_conversation_by_document(conn, payload.document_id, user.tenant_id, user.user_id)

    if not conv:
        raise HTTPException(
            status_code=400,
            detail="Please upload or select a document conversation in the sidebar first.",
        )

    return run_chat(
        tenant_id=user.tenant_id,
        owner_sub=user.user_id,
        conversation_id=str(conv["id"]),
        document_id=str(conv["document_id"]),
        question=payload.question,
    )


@app.get("/api/chat/history/{conversation_id}")
def chat_history(
    conversation_id: str,
    user: AuthUser = Depends(get_current_user),
):
    """Fetch persistent conversation history for a user's single-document conversation."""
    try:
        uuid.UUID(conversation_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid conversation UUID")

    with connection() as conn:
        conv = get_conversation(conn, conversation_id, user.tenant_id, user.user_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = load_history(user.tenant_id, conversation_id, turns=25)
    return {
        "conversation_id": conversation_id,
        "document_id": str(conv["document_id"]),
        "title": conv["title"],
        "document_status": conv["document_status"],
        "messages": messages,
    }


@app.post("/api/feedback")
def submit_feedback(
    payload: FeedbackRequest,
    user: AuthUser = Depends(get_current_user),
):
    """Record user feedback rating (-1, 0, +1) and optional note."""
    with connection() as conn:
        fb_id = save_feedback(conn, user.tenant_id, user.email, payload.rating, payload.note)
        audit(
            conn,
            user.email,
            user.tenant_id,
            "feedback_submitted",
            {"feedback_id": fb_id, "rating": payload.rating},
        )
    return {"status": "recorded", "feedback_id": fb_id}


@app.get("/api/approvals")
def get_approvals(user: AuthUser = Depends(get_current_user)):
    """List HITL approval requests for the authenticated user."""
    with connection() as conn:
        rows = list_approvals(conn, user.tenant_id)
    return {"approvals": rows}


@app.post("/api/approvals/{approval_id}/decision")
def decide_hitl_approval(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    user: AuthUser = Depends(get_current_user),
):
    """Approve or reject a pending HITL request and execute document deletion or RAG answer."""
    try:
        uuid.UUID(approval_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid approval UUID")

    with connection() as conn:
        updated = decide_approval(
            conn,
            approval_id=approval_id,
            tenant_id=user.tenant_id,
            status=payload.decision,
            reviewer=user.email,
        )
        if not updated:
            raise HTTPException(status_code=404, detail="Approval request not found")

        audit(
            conn,
            user.email,
            user.tenant_id,
            f"hitl_{payload.decision}",
            {"approval_id": approval_id},
        )

    rag_result = None
    action_result = None
    deleted_document_id = None

    if payload.decision == "approved":
        question_text = updated["question"]
        bound_doc_id = str(updated["document_id"]) if updated.get("document_id") else None

        if is_delete_action(question_text):
            q_lower = question_text.lower()
            with connection() as conn:
                docs = list_documents(conn, user.tenant_id, user.user_id)
                if not docs:
                    action_result = "Approved, but you have no uploaded documents to delete."
                else:
                    matched = [
                        d for d in docs
                        if d["title"].lower() in q_lower
                        or Path(d["s3_key"]).name.lower() in q_lower
                    ]
                    if "all" in q_lower and not matched:
                        targets = docs
                    elif matched:
                        targets = matched
                    elif bound_doc_id:
                        targets = [d for d in docs if str(d["id"]) == bound_doc_id]
                    elif len(docs) == 1:
                        targets = docs
                    else:
                        titles = ", ".join(d["title"] for d in docs)
                        targets = []
                        action_result = (
                            f"Approved, but you have multiple documents ({titles}). "
                            "Please specify the exact document title you want to delete."
                        )

                    if targets:
                        deleted_titles = []
                        for target_doc in targets:
                            _purge_document(
                                conn,
                                target_doc,
                                user.tenant_id,
                                user.email,
                                audit_action="hitl_document_deleted",
                            )
                            deleted_titles.append(target_doc["title"])
                            deleted_document_id = str(target_doc["id"])
                        action_result = (
                            f"Approved and executed: Deleted document(s): {', '.join(deleted_titles)} "
                            "and removed its conversation."
                        )

            rag_result = {
                "status": "completed",
                "answer": action_result,
                "citations": [],
                "conversation_id": str(uuid.uuid4()),
            }
        else:
            target_conv_id = None
            if bound_doc_id:
                with connection() as conn:
                    c = get_conversation_by_document(conn, bound_doc_id, user.tenant_id, user.user_id)
                    if c:
                        target_conv_id = str(c["id"])
            rag_result = run_chat(
                tenant_id=user.tenant_id,
                owner_sub=user.user_id,
                conversation_id=target_conv_id or str(uuid.uuid4()),
                document_id=bound_doc_id,
                question=question_text,
                bypass_hitl=True,
            )

    return {
        "approval": updated,
        "rag_result": rag_result,
        "action_result": action_result,
        "deleted_document_id": deleted_document_id,
    }


@app.get("/api/metrics")
def metrics(user: AuthUser = Depends(get_current_user)):
    """Return aggregated metrics and recent audit events for the authenticated user."""
    with connection() as conn:
        data = get_metrics(conn, user.tenant_id)
    return data
