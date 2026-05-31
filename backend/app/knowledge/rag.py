"""Embedding-based runbook RAG retrieval.

This module adds a real vector-search layer over the bundled Markdown
runbooks while keeping local tests and CI deterministic. It uses FAISS for
nearest-neighbor search and supports two embedding backends:

- ``sentence-transformers`` when explicitly enabled with
  ``RAG_EMBEDDING_BACKEND=sentence-transformers`` and the model is available.
- a deterministic local hashing embedder by default, so RCA/e2e paths never
  depend on a network model download.

The fallback is intentional infrastructure hygiene, not a separate product
path: it keeps the API contract, chunking, vector index, citations, and
evaluation framework testable everywhere.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np

from app.core.config import settings
from app.knowledge.retriever import _extract_title, _load_all

try:  # pragma: no cover - exercised by dependency presence, not core logic
    import faiss  # type: ignore
except Exception:  # pragma: no cover
    faiss = None  # type: ignore


_WORD_RE = re.compile(r"[a-zA-Z0-9_./:-]{2,}")


@dataclass(frozen=True)
class RunbookChunk:
    slug: str
    title: str
    path: str
    chunk_index: int
    text: str

    @property
    def citation_id(self) -> str:
        return f"{self.path}#chunk-{self.chunk_index}"


@dataclass(frozen=True)
class RagRunbookHit:
    slug: str
    title: str
    path: str
    chunk_index: int
    citation_id: str
    snippet: str
    score: float
    embedding_backend: str
    embedding_model: str

    @property
    def name(self) -> str:
        """Compatibility with the old RetrievedRunbook shape."""
        return self.slug


@dataclass(frozen=True)
class RagEvaluationCase:
    name: str
    query: str
    expected_slugs: tuple[str, ...]


@dataclass(frozen=True)
class RagEvaluationResult:
    case_count: int
    top_k: int
    top_k_accuracy: float
    source_coverage: float
    citation_coverage: float
    answer_faithfulness_proxy: float
    average_latency_ms: float
    embedding_backend: str
    embedding_model: str
    failures: list[dict[str, Any]]


RAG_EVAL_CASES: tuple[RagEvaluationCase, ...] = (
    RagEvaluationCase(
        name="bgp_neighbor_down",
        query=(
            "BGP neighbor transitioned Established to Idle, route withdrawal, "
            "prefixes missing after session flap"
        ),
        expected_slugs=("bgp", "route_missing"),
    ),
    RagEvaluationCase(
        name="interface_errors",
        query=(
            "input CRC errors rising on interface, low optic RX power, "
            "packet drops on link"
        ),
        expected_slugs=("interface_errors",),
    ),
    RagEvaluationCase(
        name="latency_loss",
        query=(
            "RTT jumped from baseline, packet loss, traceroute slow hop, "
            "queue drops"
        ),
        expected_slugs=("latency_loss",),
    ),
    RagEvaluationCase(
        name="route_missing",
        query=(
            "prefix not in routing table, outbound prefix-list missing entry, "
            "advertised routes policy"
        ),
        expected_slugs=("route_missing", "bgp"),
    ),
    RagEvaluationCase(
        name="acl_deny",
        query=(
            "ACL deny hits after policy push, source destination flow blocked, "
            "permit rule removed"
        ),
        expected_slugs=("policy_acl",),
    ),
)


def _tokens(text: str) -> list[str]:
    return [m.group(0).lower() for m in _WORD_RE.finditer(text)]


def _normalize_rows(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return (vectors / norms).astype("float32")


def _hash_embedding(text: str, dimensions: int = 384) -> np.ndarray:
    """Deterministic local embedding for offline CI/dev.

    The vector mixes token unigrams and adjacent bigrams into a fixed-size
    signed hashing space, then L2-normalizes. This is not a semantic model, but
    it gives stable vector retrieval over the small runbook corpus and lets the
    FAISS path stay active without a model download.
    """
    vector = np.zeros(dimensions, dtype="float32")
    toks = _tokens(text)
    features = toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]
    for feature in features:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[bucket] += sign
    norm = float(np.linalg.norm(vector))
    if norm:
        vector /= norm
    return vector


@lru_cache(maxsize=1)
def _sentence_transformer_model():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(settings.RAG_EMBEDDING_MODEL)


def _embed_texts(texts: Sequence[str]) -> tuple[np.ndarray, str, str]:
    backend = settings.RAG_EMBEDDING_BACKEND.lower().strip()
    if backend in {"sentence-transformers", "sentence_transformers", "st"}:
        model = _sentence_transformer_model()
        vectors = model.encode(
            list(texts), convert_to_numpy=True, normalize_embeddings=True
        )
        return (
            np.asarray(vectors, dtype="float32"),
            "sentence-transformers",
            settings.RAG_EMBEDDING_MODEL,
        )

    vectors = np.vstack([_hash_embedding(text) for text in texts])
    return vectors.astype("float32"), "local-hash", "hashing-384"


def _chunk_text(content: str, max_chars: int = 900) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    current_len = 0
    for paragraph in [p.strip() for p in content.split("\n\n") if p.strip()]:
        if current and current_len + len(paragraph) + 2 > max_chars:
            blocks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(paragraph)
        current_len += len(paragraph) + 2
    if current:
        blocks.append("\n\n".join(current))
    return blocks or [content.strip()]


@lru_cache(maxsize=1)
def load_runbook_chunks() -> tuple[RunbookChunk, ...]:
    chunks: list[RunbookChunk] = []
    for slug, title, content in _load_all():
        path = f"{slug}.md"
        for idx, chunk in enumerate(_chunk_text(content), start=1):
            chunks.append(
                RunbookChunk(
                    slug=slug,
                    title=title or _extract_title(content) or slug,
                    path=path,
                    chunk_index=idx,
                    text=chunk,
                )
            )
    return tuple(chunks)


@lru_cache(maxsize=1)
def _chunk_index() -> tuple[Any, np.ndarray, tuple[RunbookChunk, ...], str, str]:
    chunks = load_runbook_chunks()
    texts = [f"{c.title}\n{c.text}" for c in chunks]
    vectors, backend, model_name = _embed_texts(texts)
    vectors = _normalize_rows(vectors)
    if faiss is not None:
        index = faiss.IndexFlatIP(vectors.shape[1])
        index.add(vectors)
    else:
        index = None
    return index, vectors, chunks, backend, model_name


def _make_snippet(text: str, max_chars: int = 420) -> str:
    clean = " ".join(text.split())
    if len(clean) <= max_chars:
        return clean
    return clean[:max_chars].rsplit(" ", 1)[0] + "..."


def retrieve_runbook_chunks(
    query: str | Iterable[str], limit: int = 3
) -> list[RagRunbookHit]:
    if isinstance(query, str):
        query_text = query.strip()
    else:
        query_text = " ".join(str(part) for part in query).strip()
    if not query_text:
        return []

    index, vectors, chunks, backend, model_name = _chunk_index()
    query_vector, _, _ = _embed_texts([query_text])
    query_vector = _normalize_rows(query_vector)

    k = min(max(limit, 1), len(chunks))
    if index is not None:
        scores, indices = index.search(query_vector, k)
        ranked = zip(indices[0].tolist(), scores[0].tolist())
    else:
        raw_scores = vectors @ query_vector[0]
        order = np.argsort(-raw_scores)[:k]
        ranked = ((int(i), float(raw_scores[i])) for i in order)

    hits: list[RagRunbookHit] = []
    for idx, score in ranked:
        if idx < 0:
            continue
        chunk = chunks[idx]
        hits.append(
            RagRunbookHit(
                slug=chunk.slug,
                title=chunk.title,
                path=chunk.path,
                chunk_index=chunk.chunk_index,
                citation_id=chunk.citation_id,
                snippet=_make_snippet(chunk.text),
                score=float(score),
                embedding_backend=backend,
                embedding_model=model_name,
            )
        )
    return hits


def evaluate_runbook_rag(
    cases: Sequence[RagEvaluationCase] = RAG_EVAL_CASES, top_k: int = 3
) -> RagEvaluationResult:
    started = time.perf_counter()
    failures: list[dict[str, Any]] = []
    top_k_hits = 0
    expected_total = 0
    expected_found = 0
    citation_hits = 0
    backend = ""
    model_name = ""

    for case in cases:
        hits = retrieve_runbook_chunks(case.query, limit=top_k)
        hit_slugs = {h.slug for h in hits}
        if hits:
            backend = hits[0].embedding_backend
            model_name = hits[0].embedding_model
        expected = set(case.expected_slugs)
        if hit_slugs & expected:
            top_k_hits += 1
        found = len(hit_slugs & expected)
        expected_total += len(expected)
        expected_found += found
        citation_hits += sum(1 for h in hits if h.citation_id and h.snippet)
        if not hit_slugs & expected:
            failures.append(
                {
                    "case": case.name,
                    "expected": sorted(expected),
                    "actual": [h.slug for h in hits],
                }
            )

    elapsed_ms = (time.perf_counter() - started) * 1000
    result_count = len(cases) * max(top_k, 1)
    citation_coverage = citation_hits / result_count if result_count else 0.0
    source_coverage = expected_found / expected_total if expected_total else 0.0
    # Faithfulness proxy: for retrieval-only eval, every answerable RCA prompt
    # must be backed by at least one cited expected source.
    faithfulness = (len(cases) - len(failures)) / len(cases) if cases else 0.0
    return RagEvaluationResult(
        case_count=len(cases),
        top_k=top_k,
        top_k_accuracy=top_k_hits / len(cases) if cases else 0.0,
        source_coverage=source_coverage,
        citation_coverage=citation_coverage,
        answer_faithfulness_proxy=faithfulness,
        average_latency_ms=elapsed_ms / len(cases) if cases else 0.0,
        embedding_backend=backend or "unknown",
        embedding_model=model_name or "unknown",
        failures=failures,
    )


def clear_rag_caches() -> None:
    """Test helper for monkeypatching settings/model paths."""
    _sentence_transformer_model.cache_clear()
    load_runbook_chunks.cache_clear()
    _chunk_index.cache_clear()
