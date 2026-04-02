"""Embedding service: local ONNX model for vector generation.

Uses sentence-transformers' built-in ONNX backend for simplicity.
Based on: research/2026-04-01-arm64-deployment-research.md lines 55-63

Model: dragonkue/multilingual-e5-small-ko (384-dim, Korean-optimized)
- ONNX int8: ~113MB, 5-15ms/query on ARM64
- No external API calls required
"""

import logging
from typing import Any

from jarvis.config import settings

logger = logging.getLogger(__name__)

_model: Any = None


def _get_model() -> Any:
    """Lazy-load sentence-transformers model with ONNX backend."""
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(settings.embedding_model_name, backend="onnx")
        logger.info("Embedding model loaded: %s (ONNX backend)", settings.embedding_model_name)
    return _model


def embed_text(text: str) -> list[float]:
    """Generate embedding vector for a single text.

    Prepends 'query: ' prefix as required by E5 models.
    Returns normalized 384-dim vector.
    """
    model = _get_model()
    vector = model.encode(f"query: {text}", normalize_embeddings=True)
    return vector.tolist()  # type: ignore[no-any-return]


def embed_batch(texts: list[str]) -> list[list[float]]:
    """Generate embeddings for multiple texts."""
    model = _get_model()
    prefixed = [f"query: {t}" for t in texts]
    vectors = model.encode(prefixed, normalize_embeddings=True)
    return vectors.tolist()  # type: ignore[no-any-return]
