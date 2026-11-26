# Atlas Enterprise AI — Document Chat Application

An enterprise-grade, multi-tenant **Retrieval-Augmented Generation (RAG)** application powered by **FastAPI**, **LangGraph**, **Pinecone Serverless**, **Render PostgreSQL**, **Google Gemini**, **Hugging Face FastEmbed**, and **GlassFlow Rius** observability — engineered to run **100% on free/open-source tiers** and deploy cleanly to Render.

## Key Features
* **Multi-Tenant ACL Security:** Tenant isolation via Pinecone namespaces (`tenant-{id}`) and document-level access control filtering (`owner_sub` & `allowed_groups`).
* **Deterministic Guardrails & HITL:** Blocks prompt-injection attacks, scrubs leaked secrets from outputs, and routes sensitive queries to a Human-in-the-Loop (HITL) approval queue in PostgreSQL.
* **Persistent Conversation Memory:** Multi-turn chat context persisted per session UUID in PostgreSQL (`chat_messages`).
* **Deployment-Ready Storage:** Uses persistent **Render PostgreSQL blob storage** (`stored_objects`) by default (eliminating AWS S3 billing/CORS issues on deployment), with instant drop-in support for **Cloudflare R2**, **Backblaze B2**, or **AWS S3** via `S3_ENDPOINT_URL`.
* **Privacy-First Observability:** Full LangGraph, LLM, Vector, and Ingestion span tracing via GlassFlow Rius with `capture_content=False`.

## Quick Start
```powershell
# 1. Initialize database tables (including stored_objects)
.venv\Scripts\python scripts/init_db.py

# 2. Start the web server
.venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# 3. Run the end-to-end test suite
.venv\Scripts\python -m pytest tests/ -v
```

See [docs/SETUP.md](docs/SETUP.md) and [docs/RIUS_OBSERVABILITY.md](docs/RIUS_OBSERVABILITY.md) for detailed deployment and configuration guides.
