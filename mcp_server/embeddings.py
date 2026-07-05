"""Embedding and semantic search utilities for LifeVault.

This module generates vector embeddings for stored documents and search
queries, then scores them with cosine similarity to support semantic
retrieval over encrypted content.
"""

import asyncio
import os
import random
import time
from pathlib import Path

import numpy as np
from dotenv import load_dotenv
from google import genai

# Load .env from the project root so the Gemini API key is always available
_project_root = Path(__file__).resolve().parent.parent
load_dotenv(_project_root / ".env")

# ---------------------------------------------------------------------------
# Client setup (lazy singleton — initialized on first use)
# ---------------------------------------------------------------------------

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    """
    Get or create the Gemini API client (singleton pattern).

    The client is created lazily to avoid import-time side effects and
    to allow .env loading to complete first.
    """
    global _client
    if _client is None:
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GOOGLE_API_KEY is not set. Add it to .env or export it "
                "before running LifeVault. See README.md for setup."
            )
        _client = genai.Client(api_key=api_key)
    return _client


# ---------------------------------------------------------------------------
# Retry wrapper — exponential backoff with jitter for Gemini API calls
# ---------------------------------------------------------------------------

MAX_RETRIES = 4           # Total attempts = MAX_RETRIES + 1 (initial)
BASE_DELAY = 2.0          # Starting delay in seconds
MAX_DELAY = 30.0          # Cap to avoid absurdly long waits
JITTER_FACTOR = 0.5       # ±50% randomization to spread out retries


async def _retry_api_call(func, *args, **kwargs):
    """
    Execute a Gemini API call with exponential backoff + jitter.

    On 429 (RESOURCE_EXHAUSTED) or 503 (SERVICE_UNAVAILABLE), waits
    and retries up to MAX_RETRIES times. Other exceptions propagate
    immediately since retrying won't help.

    Args:
        func: Callable (sync) to invoke — e.g. client.models.embed_content
        *args, **kwargs: Forwarded to func

    Returns:
        Whatever func returns on success

    Raises:
        The last exception if all retries are exhausted
    """
    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            # The google.genai SDK methods are synchronous; call directly
            return func(*args, **kwargs)

        except Exception as e:
            last_error = e
            error_str = str(e).lower()

            # Only retry on rate-limit (429) or transient server errors (503)
            is_retryable = (
                "resource_exhausted" in error_str
                or "429" in error_str
                or "503" in error_str
                or "service_unavailable" in error_str
            )

            if not is_retryable or attempt == MAX_RETRIES:
                raise  # Non-retryable or final attempt — propagate

            # Exponential backoff: 2s, 4s, 8s, 16s (capped at MAX_DELAY)
            delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
            # Add jitter to prevent thundering herd
            jitter = delay * JITTER_FACTOR * (2 * random.random() - 1)
            actual_delay = max(0.5, delay + jitter)

            print(
                f"  [embeddings] Rate-limited (attempt {attempt + 1}/{MAX_RETRIES + 1}). "
                f"Retrying in {actual_delay:.1f}s..."
            )
            await asyncio.sleep(actual_delay)

    # Should not reach here, but just in case
    raise last_error  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Embedding generation
# ---------------------------------------------------------------------------

# Gemini embedding model — produces 768-dim vectors by default
EMBEDDING_MODEL = "gemini-embedding-001"
EMBEDDING_DIMENSION = 1536


async def generate_embedding(text: str) -> list[float]:
    """
    Generate a semantic embedding for the given text using Gemini.

    The embedding captures the semantic meaning of the text so that
    similar documents cluster together in vector space. Used at
    document-store time to enable later semantic search.

    Args:
        text: Text to embed (truncated to 8000 chars for model limits)

    Returns:
        List of floats representing the embedding vector
    """
    # Truncate to stay within the model's context window
    truncated = text[:8000] if len(text) > 8000 else text

    client = _get_client()

    # Use retry wrapper to handle free-tier rate limits gracefully
    result = await _retry_api_call(
        client.models.embed_content,
        model=EMBEDDING_MODEL,
        contents=truncated,
    )
    return list(result.embeddings[0].values)


async def generate_query_embedding(query: str) -> list[float]:
    """
    Generate an embedding optimized for search queries.

    Same model as document embeddings (required for meaningful cosine
    similarity), but queries are typically shorter so this is faster.

    Args:
        query: Natural language search query

    Returns:
        Embedding vector for the query
    """
    client = _get_client()

    result = await _retry_api_call(
        client.models.embed_content,
        model=EMBEDDING_MODEL,
        contents=query,
    )
    return list(result.embeddings[0].values)


# ---------------------------------------------------------------------------
# Cosine similarity search
# ---------------------------------------------------------------------------

def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """
    Compute cosine similarity between two vectors.

    Cosine similarity measures the angle between vectors, ignoring
    magnitude. This makes it ideal for comparing text embeddings
    where we care about semantic direction, not vector length.

    Returns:
        Similarity score between -1 and 1 (higher = more similar).
        Typical threshold for "relevant" results is ~0.3.
    """
    a = np.array(vec_a, dtype=np.float32)
    b = np.array(vec_b, dtype=np.float32)

    dot_product = np.dot(a, b)
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)

    # Guard against zero-norm vectors (e.g., empty text embeddings)
    if norm_a == 0 or norm_b == 0:
        return 0.0

    return float(dot_product / (norm_a * norm_b))


def search_embeddings(
    query_embedding: list[float],
    stored_embeddings: list[tuple[str, list[float]]],
    top_k: int = 5,
    threshold: float = 0.3,
) -> list[tuple[str, float]]:
    """
    Find the most similar documents using cosine similarity.

    This is the core semantic search engine. It operates entirely
    in-memory on decrypted embeddings — at ~500 docs this takes <10ms.

    For production scale (10k+ docs), you'd swap this for a vector DB
    (Pinecone, Chroma, etc.), but for a personal vault this is simpler,
    more private, and fast enough.

    Args:
        query_embedding: The search query's embedding vector
        stored_embeddings: List of (doc_id, embedding) from the vault
        top_k: Maximum number of results to return
        threshold: Minimum similarity score (0.3 filters out noise)

    Returns:
        List of (doc_id, similarity_score) tuples, sorted descending
    """
    if not stored_embeddings:
        return []

    # Pre-compute query norm once (used for every comparison)
    query_vec = np.array(query_embedding, dtype=np.float32)
    query_norm = np.linalg.norm(query_vec)

    if query_norm == 0:
        return []

    # Score each stored document against the query
    scores = []
    for doc_id, emb in stored_embeddings:
        doc_vec = np.array(emb, dtype=np.float32)
        doc_norm = np.linalg.norm(doc_vec)

        if doc_norm == 0:
            continue

        # Cosine similarity = dot(a, b) / (||a|| * ||b||)
        similarity = float(np.dot(query_vec, doc_vec) / (query_norm * doc_norm))

        if similarity >= threshold:
            scores.append((doc_id, similarity))

    # Sort by similarity (highest first) and return top_k results
    scores.sort(key=lambda x: x[1], reverse=True)
    return scores[:top_k]
