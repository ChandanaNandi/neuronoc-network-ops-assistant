"""Phase 6 keyword-based runbook retrieval.

This is intentionally NOT a vector store - it's a deterministic keyword
scorer over the bundled Markdown runbooks. Good enough for Phase 6;
real RAG with embeddings lands later if and when the retrieval quality
becomes the bottleneck.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

RUNBOOKS_DIR = Path(__file__).resolve().parent / "runbooks"

_WORD_RE = re.compile(r"[a-zA-Z]{3,}")


@dataclass(frozen=True)
class RetrievedRunbook:
    title: str
    name: str  # filename without extension
    snippet: str
    score: float


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


def _extract_title(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def _make_snippet(content: str, max_chars: int = 280) -> str:
    body = content.strip()
    if len(body) <= max_chars:
        return body
    return body[:max_chars].rsplit(" ", 1)[0] + "..."


def _load_all() -> list[tuple[str, str, str]]:
    """Return list of (name, title, content) tuples for every .md runbook."""
    results: list[tuple[str, str, str]] = []
    if not RUNBOOKS_DIR.exists():
        return results
    for path in sorted(RUNBOOKS_DIR.glob("*.md")):
        content = path.read_text(encoding="utf-8")
        results.append((path.stem, _extract_title(content) or path.stem, content))
    return results


def retrieve_runbooks(
    query: str | Iterable[str], limit: int = 3
) -> list[RetrievedRunbook]:
    """Score every runbook by overlap with the query tokens; return top N.

    Title hits weigh 2x to bias toward topical matches.
    """
    if isinstance(query, str):
        tokens = _tokenize(query)
    else:
        tokens = _tokenize(" ".join(query))
    if not tokens:
        return []

    query_set = set(tokens)
    scored: list[RetrievedRunbook] = []
    for name, title, content in _load_all():
        title_tokens = set(_tokenize(title))
        body_tokens = set(_tokenize(content))
        score = (
            2 * len(query_set & title_tokens) + len(query_set & body_tokens)
        )
        if score == 0:
            continue
        scored.append(
            RetrievedRunbook(
                title=title,
                name=name,
                snippet=_make_snippet(content),
                score=float(score),
            )
        )

    # Sort by score desc, then alphabetical name for deterministic tie-break.
    scored.sort(key=lambda r: (-r.score, r.name))
    return scored[:limit]
