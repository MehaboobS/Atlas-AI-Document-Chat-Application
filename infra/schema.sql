-- Atlas AI Document Chat Application - PostgreSQL Schema
-- Target Database: PostgreSQL (Render Managed / Aurora compatible)
-- Safe to rerun: uses CREATE ... IF NOT EXISTS

-- Enable UUID extension if not already present
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 1. Documents metadata table
-- Stores document ownership, multi-tenant partitioning, ACL permissions, versioning, and S3 reference
CREATE TABLE IF NOT EXISTS documents (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id text NOT NULL,
    title text NOT NULL,
    owner_sub text NOT NULL,
    allowed_groups text[] NOT NULL DEFAULT '{}',
    s3_key text NOT NULL UNIQUE,
    status text NOT NULL CHECK(status IN ('pending', 'processing', 'ready', 'failed', 'retired')),
    current_version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS documents_acl_idx ON documents(tenant_id, status);
CREATE INDEX IF NOT EXISTS documents_owner_idx ON documents(tenant_id, owner_sub);

-- 2. Feedback table
-- Tracks user ratings (-1 downvote, 0 neutral, 1 upvote) and qualitative notes on AI answers
CREATE TABLE IF NOT EXISTS feedback (
    id bigserial PRIMARY KEY,
    tenant_id text NOT NULL,
    actor text NOT NULL,
    rating integer NOT NULL CHECK(rating BETWEEN -1 AND 1),
    note text NOT NULL DEFAULT '',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS feedback_tenant_idx ON feedback(tenant_id, created_at DESC);

-- 3. Audit events table
-- Append-only regulatory & compliance log capturing all security/data mutations
CREATE TABLE IF NOT EXISTS audit_events (
    id bigserial PRIMARY KEY,
    actor text NOT NULL,
    tenant_id text NOT NULL,
    action text NOT NULL,
    detail jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS audit_tenant_time ON audit_events(tenant_id, created_at DESC);

-- 4. Approvals table
-- Human-in-the-Loop (HITL) gate for sensitive queries or document access workflows
CREATE TABLE IF NOT EXISTS approvals (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id text NOT NULL,
    requester text NOT NULL,
    question text NOT NULL,
    status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected')),
    reviewer text,
    created_at timestamptz NOT NULL DEFAULT now(),
    reviewed_at timestamptz
);
CREATE INDEX IF NOT EXISTS approvals_tenant_idx ON approvals(tenant_id, status);

-- 5. Chat messages table
-- Persistent multi-turn conversation memory partitioned by tenant and session
CREATE TABLE IF NOT EXISTS chat_messages (
    id bigserial PRIMARY KEY,
    tenant_id text NOT NULL,
    conversation_id uuid NOT NULL,
    role text NOT NULL CHECK(role IN ('user', 'assistant')),
    content text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_messages_session_idx ON chat_messages(tenant_id, conversation_id, id DESC);