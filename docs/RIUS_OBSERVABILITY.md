# Atlas Enterprise AI — GlassFlow Rius Observability

## 1. Privacy-First Telemetry Design

Atlas is instrumented with **GlassFlow Rius** (`app/observability.py`) under strict enterprise privacy defaults:
* **No Silent Telemetry:** Tracing is completely disabled if `RIUS_API_KEY` is missing or if `RIUS_DISABLED=true`.
* **Zero Payload Leakage:**
  * `rius.init(..., capture_content=False)`
  * `@rius.observe(name=..., capture_input=False, capture_output=False)`
* This ensures confidential documents, customer queries, API keys, and generated answers are **never sent** to telemetry servers — only span names, execution durations, and error statuses are recorded.

---

## 2. Configuration in `.env`

```ini
RIUS_API_KEY="ri_..."
RIUS_SERVICE_NAME="atlas-ai-enterprise"
RIUS_DISABLED=false
RIUS_CAPTURE_CONTENT=false
```

---

## 3. Instrumented Spans

| Module | Span Names | Purpose |
| :--- | :--- | :--- |
| **`app/graph.py`** | `run_chat`, `node_input_check`, `node_hitl_gate`, `node_load_memory`, `node_retrieve`, `node_generate`, `node_output_check` | Track LangGraph pipeline latencies and node transitions. |
| **`app/rag.py`** | `embed`, `embed_batch`, `answer` | Measure Hugging Face FastEmbed vectorization and Gemini LLM latency. |
| **`app/vector.py`** | `upsert`, `search` | Monitor Pinecone indexing and ACL-filtered vector query performance. |
| **`app/storage.py`** | `storage_put`, `storage_get`, `storage_delete`, `storage_upload_url` | Measure object storage read/write latency across PostgreSQL/R2/B2/S3. |
| **`app/guardrails.py`** | `guardrail_input`, `guardrail_output` | Track deterministic input/output policy check speed. |
| **`app/memory.py`** | `memory_load`, `memory_append` | Monitor PostgreSQL session history latency. |
| **`worker/ingest.py`** | `process_s3_key` | Measure full document parsing, chunking, embedding, and indexing duration. |

---

## 4. Startup Order

`app/observability.initialize()` **must** run at the very top of `app/main.py` and `worker/ingest.py` before instrumented modules execute so OpenTelemetry spans bind cleanly to the configured Rius exporter.

---

## 5. Verifying Traces

Run the integration test suite:
```powershell
.venv\Scripts\python -m pytest tests/ -v
```
Open your **Rius Dashboard** for service `atlas-ai-enterprise` to inspect span trees (`run_chat` -> `node_retrieve` -> `embed` + `search` -> `node_generate` -> `answer`) and confirm latencies without any raw content payloads exposed.
