# Atlas Enterprise AI — Setup & Deployment Guide

## 1. Architecture Overview (100% Free & Deployment-Ready Stack)

| Layer | Technology | Cost / Free Tier |
| :--- | :--- | :--- |
| **Chat / Reasoning LLM** | **Google Gemini** (`gemini-3.5-flash` / `gemini-3.5-flash-lite`) via `google-genai` | Free via Google AI Studio |
| **Embeddings** | **Hugging Face Open-Source** (`BAAI/bge-small-en-v1.5`, 384-dim) via `fastembed` | $0.00 (runs locally on CPU/ONNX) |
| **Vector Database** | **Pinecone Serverless** (`aws` / `us-east-1`, cosine metric) | Free Starter Tier |
| **Database & Object Storage** | **Render Managed PostgreSQL** (`documents`, `chat_messages`, `stored_objects`, etc.) | Free / Managed Tier |
| **Optional External Object Storage** | **Cloudflare R2**, **Backblaze B2**, or **Supabase Storage S3** (`STORAGE_BACKEND=s3`) | 10 GB Free (no AWS S3 needed) |
| **Orchestration** | **LangGraph** (`StateGraph` with guardrails, memory, retrieval, HITL) | Open Source |
| **Observability** | **GlassFlow Rius** (privacy-first span tracing) | Free Tier |

---

## 2. Deployment Storage Alternatives to Amazon S3

Instead of relying on AWS S3 (which requires IAM key management and 12-month expiring free tiers), Atlas uses a pluggable `app/storage.py` layer supporting three modes via `STORAGE_BACKEND`:

### Option A: `STORAGE_BACKEND="postgres"` (Active Default — Best for Render / Railway / Fly.io)
* **How it works:** Stores uploaded files and embedding backups persistently inside the `stored_objects` table in your existing Render PostgreSQL database.
* **Why it works great in deployment:** Ephemeral container disks on Render wipe on restart, whereas Render PostgreSQL persists permanently across deployments.
* **Bonus:** Zero new accounts, zero extra API keys, and zero external S3 CORS configuration needed.

### Option B: `STORAGE_BACKEND="s3"` (Cloudflare R2 / Backblaze B2 / Supabase Storage S3)
When scaling beyond PostgreSQL blob storage, use any **S3-compatible object store** without changing any Python code:

1. **Cloudflare R2 (Recommended for scale — 10 GB/mo free, $0 egress fees):**
   ```ini
   STORAGE_BACKEND="s3"
   S3_ENDPOINT_URL="https://<your-account-id>.r2.cloudflarestorage.com"
   S3_BUCKET="atlas-documents"
   AWS_REGION="auto"
   AWS_ACCESS_KEY_ID="<r2-access-key-id>"
   AWS_SECRET_ACCESS_KEY="<r2-secret-access-key>"
   ```
2. **Backblaze B2 (10 GB free, no credit card required):**
   ```ini
   STORAGE_BACKEND="s3"
   S3_ENDPOINT_URL="https://s3.us-west-004.backblazeb2.com"
   S3_BUCKET="atlas-documents"
   AWS_REGION="us-west-004"
   AWS_ACCESS_KEY_ID="<b2-key-id>"
   AWS_SECRET_ACCESS_KEY="<b2-application-key>"
   ```

#### CORS Configuration (Required only when `STORAGE_BACKEND="s3"` for browser PUT uploads)
Configure your R2 / B2 / S3 bucket CORS policy to allow `PUT` requests from your exact local and deployed origins:
```json
[
  {
    "AllowedOrigins": [
      "http://127.0.0.1:8000",
      "http://localhost:8000",
      "https://your-app.onrender.com"
    ],
    "AllowedMethods": ["PUT", "GET", "HEAD"],
    "AllowedHeaders": ["*"],
    "MaxAgeSeconds": 3600
  }
]
```

---

## 3. Local Setup & Running

1. **Configure Environment:**
   ```powershell
   copy .env.example .env
   ```
   Fill in `DATABASE_URL`, `PINECONE_API_KEY`, `GEMINI_API_KEY`, and optionally `RIUS_API_KEY`.

2. **Initialize PostgreSQL Schema:**
   ```powershell
   .venv\Scripts\python scripts/init_db.py
   ```

3. **Start the FastAPI Application:**
   ```powershell
   .venv\Scripts\uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
   ```
   Open `http://127.0.0.1:8000` in your browser.

4. **Run End-to-End Test Suite:**
   ```powershell
   .venv\Scripts\python -m pytest tests/ -v
   ```

---

## 4. Deploying to Render

This repository includes a ready-to-use `render.yaml` Blueprint:
1. Push your repository to GitHub (confirm `.env` is ignored via `.gitignore`).
2. Create a new **Web Service** or **Blueprint** in Render.
3. Set environment variables in the Render dashboard (`DATABASE_URL`, `PINECONE_API_KEY`, `GEMINI_API_KEY`, `RIUS_API_KEY`, `STORAGE_BACKEND=postgres`, `CORS_ORIGINS=https://your-service.onrender.com`).
4. Build command: `pip install -r requirements.txt && python scripts/init_db.py`
5. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

---

## 5. How Ingestion (`worker/ingest.py`) Runs
Files inside `worker/` do not auto-execute on their own. When a user uploads a file via the UI, `app/main.py` triggers `worker.ingest.process_s3_key(storage_key)` via FastAPI `BackgroundTasks` (or synchronously with `?sync=true`), which downloads the file from storage, parses text, generates 384-dim embeddings, upserts vectors to Pinecone, and marks the document `ready` in PostgreSQL.
