"""Phase 17A runbook retrieval tests.

Two layers:
1. The Phase 6 `retrieve_runbooks` helper - now extended with a `path`
   field; pin loading + scoring + ordering + the new field.
2. The new `GET /api/runbooks/search` endpoint - happy paths, both ways
   of supplying a query, and the 400/404/422 error surface.

NO embeddings, NO Ollama, NO network. The whole scoring path is the
deterministic in-process keyword scorer.
"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.knowledge.retriever import _load_all, retrieve_runbooks
from app.simulator.seed import apply_scenario


# ---------- retriever helper ----------


def test_load_all_picks_up_every_bundled_runbook() -> None:
    """Five bundled runbooks ship with the repo. New ones added later are
    fine; this test pins the floor so a future refactor that breaks the
    loader (wrong path, glob typo) fails immediately."""
    loaded = _load_all()
    slugs = sorted(name for name, _, _ in loaded)
    for required in (
        "bgp",
        "interface_errors",
        "latency_loss",
        "policy_acl",
        "route_missing",
    ):
        assert required in slugs, f"runbook {required!r} missing from index"


def test_retrieve_runbooks_carries_path_field() -> None:
    """Phase 17A extension: every hit knows its filename relative to
    `app/knowledge/runbooks/` so the API can surface it."""
    hits = retrieve_runbooks("bgp neighbor session", limit=5)
    assert hits, "expected at least the bgp runbook to hit"
    for h in hits:
        assert h.path == f"{h.name}.md"


def test_retrieve_runbooks_returns_bgp_first_for_bgp_query() -> None:
    """Deterministic check: the bgp.md runbook outranks every other for a
    BGP-shaped query. Title-token hits weigh 2x, so the bgp runbook (title
    'BGP neighbor / session flap') dominates."""
    hits = retrieve_runbooks("bgp neighbor session flap", limit=5)
    assert hits, "expected a hit"
    assert hits[0].name == "bgp"


def test_retrieve_runbooks_respects_limit() -> None:
    hits = retrieve_runbooks(
        "bgp neighbor routing interface acl policy route", limit=2
    )
    assert len(hits) <= 2


def test_retrieve_runbooks_empty_query_returns_empty() -> None:
    assert retrieve_runbooks("", limit=5) == []


def test_retrieve_runbooks_unknown_terms_returns_empty() -> None:
    assert retrieve_runbooks("zzzqqq nopematch", limit=5) == []


# ---------- API: happy path ----------


def test_api_search_with_query_returns_bgp_for_bgp_text(
    client: TestClient,
) -> None:
    response = client.get("/api/runbooks/search?q=bgp+neighbor+down&limit=3")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body, "expected at least one hit"
    # bgp.md is the top hit for any BGP-shaped query
    assert body[0]["slug"] == "bgp"
    assert body[0]["path"] == "bgp.md"
    assert body[0]["title"]  # non-empty H1 extracted from the md
    assert body[0]["score"] > 0
    assert body[0]["excerpt"]  # non-empty body excerpt


def test_api_search_with_incident_id_derives_query_from_incident(
    client: TestClient, db_session: Session
) -> None:
    """`incident_id` is enough on its own - the endpoint reads
    title + incident_type + summary off the row to build the query.
    No agent-run side effect, no LLM call."""
    incident = apply_scenario(db_session, "bgp_neighbor_down")
    response = client.get(f"/api/runbooks/search?incident_id={incident.id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body, "expected at least the bgp runbook for a BGP incident"
    assert body[0]["slug"] == "bgp"


def test_api_search_combines_query_and_incident(
    client: TestClient, db_session: Session
) -> None:
    """When both are supplied the endpoint concatenates - the extra signal
    shouldn't change the top match for a BGP incident."""
    incident = apply_scenario(db_session, "bgp_neighbor_down")
    response = client.get(
        f"/api/runbooks/search?q=bgp&incident_id={incident.id}&limit=1"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 1
    assert body[0]["slug"] == "bgp"


def test_api_search_limit_caps_response_size(client: TestClient) -> None:
    response = client.get(
        "/api/runbooks/search?q=bgp+interface+latency+policy+route&limit=2"
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body) <= 2


def test_api_search_returns_empty_list_for_no_matches(client: TestClient) -> None:
    response = client.get("/api/runbooks/search?q=zzzqqq+nopematch")
    assert response.status_code == 200
    assert response.json() == []


# ---------- API: error paths ----------


def test_api_search_400_when_neither_q_nor_incident(client: TestClient) -> None:
    response = client.get("/api/runbooks/search")
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "q" in detail and "incident_id" in detail


def test_api_search_400_when_q_is_only_whitespace_and_no_incident(
    client: TestClient,
) -> None:
    response = client.get("/api/runbooks/search?q=%20%20%20")
    assert response.status_code == 400


def test_api_search_404_for_unknown_incident_id(client: TestClient) -> None:
    response = client.get(f"/api/runbooks/search?incident_id={uuid4()}")
    assert response.status_code == 404


def test_api_search_422_for_out_of_range_limit_high(client: TestClient) -> None:
    response = client.get("/api/runbooks/search?q=bgp&limit=999")
    assert response.status_code == 422


def test_api_search_422_for_out_of_range_limit_low(client: TestClient) -> None:
    response = client.get("/api/runbooks/search?q=bgp&limit=0")
    assert response.status_code == 422


# ---------- response shape ----------


def test_api_search_response_shape_matches_runbookhit(client: TestClient) -> None:
    response = client.get("/api/runbooks/search?q=acl+policy&limit=1")
    assert response.status_code == 200
    hit = response.json()[0]
    assert set(hit.keys()) == {"slug", "title", "score", "excerpt", "path"}
    assert isinstance(hit["slug"], str) and hit["slug"]
    assert isinstance(hit["title"], str) and hit["title"]
    assert isinstance(hit["score"], (int, float)) and hit["score"] > 0
    assert isinstance(hit["excerpt"], str) and hit["excerpt"]
    assert hit["path"] == f"{hit['slug']}.md"
