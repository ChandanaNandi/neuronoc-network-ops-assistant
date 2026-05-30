from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RunbookHit(BaseModel):
    """One scored hit returned by `GET /api/runbooks/search`.

    Mirrors `RetrievedRunbook` (`app/knowledge/retriever.py`) plus a `path`
    that's relative to `app/knowledge/runbooks/`. The endpoint never returns
    file contents - only the bundled excerpt, so the response stays bounded.
    """

    model_config = ConfigDict(extra="forbid")

    slug: str  # filename stem, e.g. "bgp"
    title: str  # the runbook's H1 (or slug fallback)
    score: float = Field(ge=0.0)
    excerpt: str  # first ~280 chars of the runbook body
    path: str  # filename inside app/knowledge/runbooks/, e.g. "bgp.md"
