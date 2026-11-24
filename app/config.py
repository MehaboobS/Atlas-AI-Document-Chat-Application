from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # AWS S3 Storage (Free Tier: 5GB Standard Storage)
    aws_region: str = "ap-southeast-2"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    s3_bucket: str = ""

    # Google Gemini Chat Model (100% Free via Google AI Studio: https://aistudio.google.com/)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"

    # Hugging Face Open-Source Embeddings (100% Free & Local via FastEmbed)
    hf_embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dim: int = 384

    # Database & Storage
    database_url: str = ""
    max_upload_bytes: int = 10485760  # 10MB

    # Pinecone Vector Database
    pinecone_api_key: str = ""
    pinecone_index_name: str = "atlas-enterprise-ai-index"
    pinecone_cloud: str = "aws"
    pinecone_region: str = "ap-southeast-2"
    pinecone_ready_timeout: int = 180

    # RAG & Application Settings
    min_relevance: float = 0.25
    hitl_enabled: bool = True
    memory_turns: int = 6


@lru_cache
def settings() -> Settings:
    return Settings()
