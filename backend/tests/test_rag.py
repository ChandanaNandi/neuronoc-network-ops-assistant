"""Embedding-based runbook RAG tests.

The production path can use Sentence Transformers when the model is available
and ``RAG_EMBEDDING_BACKEND=sentence-transformers`` is set. Tests intentionally
exercise the deterministic local embedding fallback so CI never downloads a
model, while still validating the FAISS/vector-search/citation/evaluation
contract.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.knowledge.rag import (
    evaluate_runbook_rag,
    retrieve_runbook_chunks,
)
from app.simulator.seed import apply_scenario


def test_rag_retriever_returns_cited_bgp_chunks() -> None:
    hits = retrieve_runbook_chunks(
        "BGP neighbor down, route withdrawal, prefixes missing",
        limit=3,
    )

    assert hits
    assert hits[0].slug == "bgp"
    assert hits[0].citation_id.startswith("bgp.md#chunk-")
    assert hits[0].snippet
    assert hits[0].embedding_backend in {
        "local-hash",
        "sentence-transformers",
    }


def test_rag_retriever_finds_expected_source_in_top_k() -> None:
    hits = retrieve_runbook_chunks(
        "input CRC errors, optic light low, rx dropped on interface",
        limit=3,
    )

    assert {h.slug for h in hits} & {"interface_errors"}


def test_api_rag_search_returns_citations(client: TestClient) -> None:
    response = client.get(
        "/api/runbooks/rag/search?q=acl+deny+policy+push&limit=3"
    )
    assert response.status_code == 200, response.text

    body = response.json()
    assert body
    assert body[0]["slug"] == "policy_acl"
    assert body[0]["citation_id"].startswith("policy_acl.md#chunk-")
    assert body[0]["embedding_backend"]
    assert body[0]["embedding_model"]


def test_api_rag_search_with_incident_id_derives_query(
    client: TestClient, db_session: Session
) -> None:
    incident = apply_scenario(db_session, "route_missing")

    response = client.get(f"/api/runbooks/rag/search?incident_id={incident.id}")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body
    assert {row["slug"] for row in body[:3]} & {"route_missing"}


def test_rag_evaluation_set_reports_top_k_accuracy() -> None:
    result = evaluate_runbook_rag(top_k=3)

    assert result.case_count == 5
    assert result.top_k == 3
    assert result.top_k_accuracy == 1.0
    assert result.source_coverage > 0.0
    assert result.citation_coverage == 1.0
    assert result.answer_faithfulness_proxy == 1.0
    assert result.failures == []


def test_api_rag_evaluate_returns_metrics(client: TestClient) -> None:
    response = client.get("/api/runbooks/rag/evaluate?top_k=3")
    assert response.status_code == 200, response.text

    body = response.json()
    assert body["case_count"] == 5
    assert body["top_k_accuracy"] == 1.0
    assert body["embedding_backend"]
    assert body["failures"] == []
