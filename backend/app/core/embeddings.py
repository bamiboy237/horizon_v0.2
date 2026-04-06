"""Embedding helpers for OpenAI vector generation."""

from __future__ import annotations

from functools import lru_cache

from openai import OpenAI

from app.core.config import get_settings


@lru_cache
def get_openai_client() -> OpenAI:
    """Return a cached OpenAI client configured from app settings."""

    settings = get_settings()

    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required to generate embeddings.")

    return OpenAI(api_key=settings.openai_api_key)


def normalize_embedding_text(text: str) -> str:
    """Normalize input text before sending it to the embedding model."""

    return " ".join(text.split())


def get_embedding(text: str) -> list[float]:
    """Generate an embedding vector for the provided text."""

    settings = get_settings()
    normalized_text = normalize_embedding_text(text)

    if not normalized_text:
        raise ValueError("Text to embed cannot be empty.")

    response = get_openai_client().embeddings.create(
        model=settings.openai_embedding_model,
        input=normalized_text,
        dimensions=settings.openai_embedding_dimensions,
    )
    return response.data[0].embedding
