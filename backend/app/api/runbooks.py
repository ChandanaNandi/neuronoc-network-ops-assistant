"""Read-only runbook search.

Phase 17A shipped deterministic keyword retrieval. Phase 25 adds an
embedding-backed RAG endpoint over the same bundled Markdown runbooks.

Query is built from:
- explicit `q` text, and/or
- the incident row's title + incident_type + summary (when `incident_id` is
  provided). At least one of `q` / `incident_id` is required (400 if both
  missing/empty).

Never executes a command, never opens a device connection.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.models import Incident
from app.db.session import get_db
from app.knowledge.rag import evaluate_runbook_rag, retrieve_runbook_chunks
from app.knowledge.retriever import retrieve_runbooks
from app.schemas.runbooks import (
    RunbookHit,
    RunbookRagEvaluationRead,
    RunbookRagHit,
)

router = APIRouter(prefix="/api/runbooks", tags=["runbooks"])


def _build_query_for_incident(incident: Incident) -> str:
    """Concatenate the fields that carry topical signal. Deterministic: no
    DB lookup beyond the incident row itself, no agent-run side effects."""
    parts: list[str] = [incident.title, incident.incident_type]
    if incident.summary:
        parts.append(incident.summary)
    return " ".join(p for p in parts if p)


@router.get("/search", response_model=list[RunbookHit])
def search_runbooks(
    q: str | None = Query(
        default=None,
        description="Free-text query. Tokenised against title (2x weight) + body.",
    ),
    incident_id: UUID | None = Query(
        default=None,
        description=(
            "Optional incident id; the endpoint derives a query from the "
            "incident's title + incident_type + summary. Combined with `q` "
            "when both are supplied."
        ),
    ),
    limit: int = Query(default=5, ge=1, le=20),
    db: Session = Depends(get_db),
) -> list[RunbookHit]:
    """Search the bundled Markdown runbooks.

    400 if neither `q` nor `incident_id` is provided (or both are empty).
    404 if `incident_id` is supplied but unknown.
    422 for an out-of-range `limit` (FastAPI's default Query validation).
    """
    parts: list[str] = []
    q_trimmed = (q or "").strip()
    if q_trimmed:
        parts.append(q_trimmed)

    if incident_id is not None:
        incident = db.get(Incident, incident_id)
        if incident is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"incident {incident_id} not found",
            )
        derived = _build_query_for_incident(incident).strip()
        if derived:
            parts.append(derived)

    if not parts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="at least one of `q` or `incident_id` must be supplied",
        )

    hits = retrieve_runbooks(" ".join(parts), limit=limit)
    return [
        RunbookHit(
            slug=h.name,
            title=h.title,
            score=h.score,
            excerpt=h.snippet,
            path=h.path,
        )
        for h in hits
    ]


@router.get("/rag/search", response_model=list[RunbookRagHit])
def search_runbooks_rag(
    q: str | None = Query(
        default=None,
        description="Free-text query embedded and searched against runbook chunks.",
    ),
    incident_id: UUID | None = Query(
        default=None,
        description=(
            "Optional incident id; derives the same title/type/summary query "
            "used by the keyword endpoint."
        ),
    ),
    limit: int = Query(default=5, ge=1, le=20),
    db: Session = Depends(get_db),
) -> list[RunbookRagHit]:
    """Embedding-backed runbook chunk search with citation ids."""
    parts: list[str] = []
    q_trimmed = (q or "").strip()
    if q_trimmed:
        parts.append(q_trimmed)

    if incident_id is not None:
        incident = db.get(Incident, incident_id)
        if incident is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"incident {incident_id} not found",
            )
        derived = _build_query_for_incident(incident).strip()
        if derived:
            parts.append(derived)

    if not parts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="at least one of `q` or `incident_id` must be supplied",
        )

    hits = retrieve_runbook_chunks(" ".join(parts), limit=limit)
    return [
        RunbookRagHit(
            slug=h.slug,
            title=h.title,
            score=h.score,
            excerpt=h.snippet,
            path=h.path,
            chunk_index=h.chunk_index,
            citation_id=h.citation_id,
            embedding_backend=h.embedding_backend,
            embedding_model=h.embedding_model,
        )
        for h in hits
    ]


@router.get("/rag/evaluate", response_model=RunbookRagEvaluationRead)
def evaluate_runbooks_rag(
    top_k: int = Query(default=3, ge=1, le=10),
) -> RunbookRagEvaluationRead:
    """Run the small bundled RAG retrieval evaluation set."""
    result = evaluate_runbook_rag(top_k=top_k)
    return RunbookRagEvaluationRead(
        case_count=result.case_count,
        top_k=result.top_k,
        top_k_accuracy=result.top_k_accuracy,
        source_coverage=result.source_coverage,
        citation_coverage=result.citation_coverage,
        answer_faithfulness_proxy=result.answer_faithfulness_proxy,
        average_latency_ms=result.average_latency_ms,
        embedding_backend=result.embedding_backend,
        embedding_model=result.embedding_model,
        failures=result.failures,
    )
