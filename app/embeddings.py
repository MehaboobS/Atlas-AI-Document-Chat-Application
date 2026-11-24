import logging
from typing import Sequence
from fastembed import TextEmbedding
from app.config import settings

logger = logging.getLogger(__name__)

# Cache embedding model instance in memory
_embedding_model: TextEmbedding | None = None


def get_embedding_model() -> TextEmbedding:
    """Get or initialize the local open-source Hugging Face embedding model."""
    global _embedding_model
    if _embedding_model is None:
        cfg = settings()
        logger.info("Loading open-source Hugging Face embedding model: %s", cfg.hf_embed_model)
        _embedding_model = TextEmbedding(model_name=cfg.hf_embed_model)
    return _embedding_model


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    """
    Generate dense vector embeddings for a list of texts using
    the free open-source Hugging Face model locally via FastEmbed.
    """
    if not texts:
        return []

    model = get_embedding_model()
    embeddings_generator = model.embed(texts)
    return [embedding.tolist() for embedding in embeddings_generator]


def embed_query(text: str) -> list[float]:
    """Generate dense vector embedding for a single search query."""
    vectors = embed_texts([text])
    return vectors[0] if vectors else []
