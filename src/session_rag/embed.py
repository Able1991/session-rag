"""Local embeddings via fastembed (ONNX). Nothing leaves the machine after the one-time model download.

SESSION_RAG_MODEL selects the model (any fastembed text model); changing it requires
``session-rag index --rebuild``. SESSION_RAG_MODEL=hash is a dependency-free embedder for tests.
"""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from pathlib import Path

MODEL = os.environ.get("SESSION_RAG_MODEL", "minishlab/potion-multilingual-128M")
# models live next to the index, not in the OS temp dir (which macOS cleans up)
CACHE_DIR = Path(
    os.environ.get(
        "SESSION_RAG_MODELS", Path(os.environ.get("SESSION_RAG_HOME", "~/.local/share/session-rag")) / "models"
    )
).expanduser()
_DIMS = {
    "minishlab/potion-multilingual-128M": 256,
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": 384,
    "sentence-transformers/paraphrase-multilingual-mpnet-base-v2": 768,
    "intfloat/multilingual-e5-large": 1024,
}
DIM = int(os.environ.get("SESSION_RAG_DIM", _DIMS.get(MODEL, 256)))


def _hash_embed(text: str) -> list[float]:
    v = [0.0] * DIM
    for tok in text.lower().split():
        h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "little")
        v[h % DIM] += 1.0 if (h >> 32) & 1 else -1.0
    n = sum(x * x for x in v) ** 0.5 or 1.0
    return [x / n for x in v]


@lru_cache(maxsize=1)
def _model():
    from fastembed import TextEmbedding

    providers = os.environ.get("SESSION_RAG_PROVIDERS")
    kw = {"providers": providers.split(",")} if providers else {}
    # a shared server must not take every core for one query (SESSION_RAG_THREADS=0: library default)
    threads = int(os.environ.get("SESSION_RAG_THREADS", "2"))
    if threads:
        kw["threads"] = threads
    return TextEmbedding(MODEL, cache_dir=str(CACHE_DIR), **kw)


def loaded() -> bool:
    return _model.cache_info().currsize > 0 or _reranker.cache_info().currsize > 0


def unload() -> None:
    """Drop the models (a long-running server frees ~1 GB while idle; the next query loads them again)."""
    import gc

    _model.cache_clear()
    _reranker.cache_clear()
    gc.collect()


RERANKER = os.environ.get("SESSION_RAG_RERANKER", "jinaai/jina-reranker-v2-base-multilingual")


@lru_cache(maxsize=1)
def _reranker():
    from fastembed.rerank.cross_encoder import TextCrossEncoder

    return TextCrossEncoder(RERANKER, cache_dir=str(CACHE_DIR))


def rerank_scores(query: str, docs: list[str]) -> list[float]:
    if MODEL == "hash":
        q = set(query.lower().split())
        return [len(q & set(d.lower().split())) for d in docs]
    return list(_reranker().rerank(query, docs, batch_size=32))


def embed(texts: list[str]) -> list[list[float]]:
    """Embed documents (passages)."""
    if MODEL == "hash":
        return [_hash_embed(t) for t in texts]
    return [v.tolist() for v in _model().passage_embed(texts, batch_size=256)]


def embed_query(text: str) -> list[float]:
    if MODEL == "hash":
        return _hash_embed(text)
    return next(iter(_model().query_embed(text))).tolist()
