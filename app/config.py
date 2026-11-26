from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Object Storage Configuration (Deployment-ready alternative to AWS S3)
    # Supported backends:
    # - "postgres": Persistent Render PostgreSQL blob storage (zero extra setup/keys)
    # - "s3": Any S3-compatible provider (Cloudflare R2, Backblaze B2, Supabase S3, MinIO, AWS S3)
    # - "local": Local filesystem directory
    storage_backend: str = "postgres"
    s3_endpoint_url: str = ""
    local_storage_dir: str = ".storage"

    # S3-Compatible Credentials (only needed when storage_backend="s3")
    aws_region: str = "us-east-1"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    s3_bucket: str = ""

    # Google Gemini Chat Model (100% Free via Google AI Studio: https://aistudio.google.com/)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"

    # Hugging Face Open-Source Embeddings (100% Free & Local via FastEmbed)
    hf_embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dim: int = 384

    # Database & Upload Limits
    database_url: str = ""
    max_upload_bytes: int = 10485760  # 10MB

    # Pinecone Vector Database
    pinecone_api_key: str = ""
    pinecone_index_name: str = "atlas-enterprise-ai-index"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "us-east-1"
    pinecone_ready_timeout: int = 180

    # RAG, Guardrails, Memory & Auth Settings
    min_relevance: float = 0.25
    hitl_enabled: bool = True
    memory_turns: int = 6
    cors_origins: str = "http://127.0.0.1:8000,http://localhost:8000"
    jwt_secret_key: str = "atlas-dev-jwt-secret-change-in-production-32chars"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 1440


@lru_cache
def settings() -> Settings:
    return Settings()
