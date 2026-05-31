"""Phase 6 RCA tests.

NEVER calls the live Ollama daemon - the Ollama function is monkeypatched
inside the explainer module. DB-backed tests use the real-Postgres fixture.
"""

import json
from contextlib import contextmanager
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.knowledge.rag import retrieve_runbook_chunks
from app.knowledge.retriever import retrieve_runbooks
from app.llm.ollama import OllamaUnavailableError
from app.rca import explainer as explainer_module
from app.rca.explainer import build_rca_prompt, generate_rca_explanation
from app.simulator.seed import apply_scenario


# ---------- retriever ----------


def test_retriever_returns_bgp_runbook_for_bgp_query() -> None:
    results = retrieve_runbooks("bgp_neighbor_down routing_failure", limit=3)
    assert results, "expected at least one runbook hit"
    assert results[0].name == "bgp"


def test_retriever_returns_policy_runbook_for_acl_query() -> None:
    results = retrieve_runbooks("acl policy block traffic_denied", limit=3)
    assert results, "expected at least one runbook hit"
    assert results[0].name == "policy_acl"


def test_retriever_empty_query_returns_empty() -> None:
    assert retrieve_runbooks("", limit=3) == []


# ---------- prompt ----------


def test_build_rca_prompt_constrains_to_provided_evidence() -> None:
    report = {
        "incident_id": "00000000-0000-0000-0000-000000000000",
        "incident_type": "bgp_neighbor_down",
        "severity": "critical",
        "key_findings": ["bgp_neighbor_down_detected"],
        "correlated_signals": ["routing_failure"],
        "validation_summary": ["reachability_loss"],
        "recommended_next_steps": ["check neighbor config"],
        "suspected_root_cause": "session disruption",
        "anomaly_count": 1,
        "requires_human_review": True,
        "confidence": 0.95,
    }
    runbooks = retrieve_runbook_chunks("bgp_neighbor_down", limit=2)
    prompt = build_rca_prompt(report, runbooks)

    lowered = prompt.lower()
    assert "use only the provided evidence" in lowered
    assert "bgp_neighbor_down" in prompt
    # The runbook must end up in the prompt body.
    assert any(rb.title.lower() in lowered or rb.name in prompt for rb in runbooks)
    assert any(rb.citation_id in prompt for rb in runbooks)


# ---------- generate_rca_explanation ----------


def _seed_bgp(db: Session):
    return apply_scenario(db, "bgp_neighbor_down")


def test_fallback_when_ollama_unavailable(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident = _seed_bgp(db_session)

    def boom(*_args: Any, **_kwargs: Any):
        raise OllamaUnavailableError("simulated daemon down")

    monkeypatch.setattr(explainer_module, "generate_ollama_json", boom)

    explanation = generate_rca_explanation(db_session, incident.id)

    assert explanation.llm_available is False
    assert explanation.model is None
    assert explanation.incident_id == incident.id
    assert explanation.likely_root_cause  # populated from deterministic report
    assert explanation.recommended_next_steps  # carried over from report
    assert explanation.unsafe_actions, "fallback must always include guardrails"
    assert explanation.citations
    assert explanation.retrieval_backend


def test_require_llm_true_raises_when_unavailable(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident = _seed_bgp(db_session)
    monkeypatch.setattr(
        explainer_module,
        "generate_ollama_json",
        lambda *a, **k: (_ for _ in ()).throw(OllamaUnavailableError("down")),
    )
    with pytest.raises(OllamaUnavailableError):
        generate_rca_explanation(db_session, incident.id, require_llm=True)


def test_uses_mocked_ollama_json_when_available(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident = _seed_bgp(db_session)

    fake_llm_payload = {
        "summary": "BGP session toward AS65010 dropped; downstream prefixes lost.",
        "likely_root_cause": "Upstream session reset by neighbor policy change.",
        "supporting_evidence": [
            "bgp_state_change Established -> Idle",
            "route_table_excerpt: 198.51.100.0/24 not in table",
        ],
        "runbook_references": ["BGP neighbor / session flap"],
        "recommended_next_steps": [
            "Capture show bgp neighbor on both sides (no approval needed).",
            "Schedule a 'clear ip bgp soft' only after operator approval.",
        ],
        "unsafe_actions": [
            "Do not bounce the underlying interface without approval.",
        ],
        "confidence": 0.78,
    }

    monkeypatch.setattr(
        explainer_module,
        "generate_ollama_json",
        lambda prompt, model=None, timeout_seconds=60: fake_llm_payload,
    )

    explanation = generate_rca_explanation(
        db_session, incident.id, model="fake-model"
    )
    assert explanation.llm_available is True
    assert explanation.model == "fake-model"
    assert explanation.confidence == pytest.approx(0.78)
    assert any("198.51.100.0/24" in e for e in explanation.supporting_evidence)
    assert "BGP" in explanation.runbook_references[0]
    assert explanation.citations
    assert explanation.citations[0].citation_id


def test_server_owned_keys_in_llm_response_are_ignored(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the model echoes back incident_id / model / llm_available with the
    wrong values, the explainer must ignore them and keep the server-owned ones."""
    incident = _seed_bgp(db_session)

    bogus = {
        # Required fields the schema needs:
        "summary": "real summary",
        "likely_root_cause": "real cause",
        "supporting_evidence": ["e1"],
        "runbook_references": ["BGP neighbor / session flap"],
        "recommended_next_steps": ["step 1"],
        "unsafe_actions": ["nothing dangerous"],
        "confidence": 0.5,
        # Reserved keys the model is NOT allowed to dictate:
        "incident_id": "00000000-0000-0000-0000-000000000001",
        "model": "evil-model-name",
        "llm_available": False,
    }
    monkeypatch.setattr(
        explainer_module,
        "generate_ollama_json",
        lambda *a, **k: bogus,
    )

    explanation = generate_rca_explanation(
        db_session, incident.id, model="real-requested-model"
    )

    assert explanation.incident_id == incident.id
    assert explanation.model == "real-requested-model"
    assert explanation.llm_available is True
    # The legitimate, non-reserved fields from the LLM should still come through.
    assert explanation.likely_root_cause == "real cause"
    assert explanation.confidence == pytest.approx(0.5)


def test_missing_incident_raises_incident_not_found(db_session: Session) -> None:
    from app.anomaly.engine import IncidentNotFoundError

    with pytest.raises(IncidentNotFoundError):
        generate_rca_explanation(db_session, uuid4())


def test_llm_returns_schema_invalid_json_falls_back(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    incident = _seed_bgp(db_session)

    # Missing required keys -> Pydantic ValidationError -> fallback (require_llm=False)
    monkeypatch.setattr(
        explainer_module,
        "generate_ollama_json",
        lambda *a, **k: {"summary": "only this field"},
    )
    explanation = generate_rca_explanation(db_session, incident.id)
    assert explanation.llm_available is False


# ---------- API ----------


def test_api_fallback_returns_200_with_llm_available_false(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident = _seed_bgp(db_session)
    monkeypatch.setattr(
        explainer_module,
        "generate_ollama_json",
        lambda *a, **k: (_ for _ in ()).throw(OllamaUnavailableError("down")),
    )
    response = client.post(f"/api/rca/incidents/{incident.id}/explain")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["llm_available"] is False
    assert body["incident_id"] == str(incident.id)


def test_api_require_llm_returns_503_when_unavailable(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident = _seed_bgp(db_session)
    monkeypatch.setattr(
        explainer_module,
        "generate_ollama_json",
        lambda *a, **k: (_ for _ in ()).throw(OllamaUnavailableError("down")),
    )
    response = client.post(
        f"/api/rca/incidents/{incident.id}/explain",
        params={"require_llm": "true"},
    )
    assert response.status_code == 503


def test_api_404_for_missing_incident(client: TestClient) -> None:
    response = client.post(f"/api/rca/incidents/{uuid4()}/explain")
    assert response.status_code == 404


# ---------- CLI ----------


def test_cli_fallback_prints_explanation_json(
    db_session: Session,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident = _seed_bgp(db_session)

    @contextmanager
    def fake_session_local():
        yield db_session

    monkeypatch.setattr(explainer_module, "SessionLocal", fake_session_local)
    monkeypatch.setattr(
        explainer_module,
        "generate_ollama_json",
        lambda *a, **k: (_ for _ in ()).throw(OllamaUnavailableError("down")),
    )

    exit_code = explainer_module.main(["--incident-id", str(incident.id)])
    assert exit_code == 0

    parsed = json.loads(capsys.readouterr().out)
    assert parsed["llm_available"] is False
    assert parsed["incident_id"] == str(incident.id)


def test_cli_require_llm_exits_nonzero_when_unavailable(
    db_session: Session,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident = _seed_bgp(db_session)

    @contextmanager
    def fake_session_local():
        yield db_session

    monkeypatch.setattr(explainer_module, "SessionLocal", fake_session_local)
    monkeypatch.setattr(
        explainer_module,
        "generate_ollama_json",
        lambda *a, **k: (_ for _ in ()).throw(OllamaUnavailableError("down")),
    )

    exit_code = explainer_module.main(
        ["--incident-id", str(incident.id), "--require-llm"]
    )
    assert exit_code == 2
    err = capsys.readouterr().err
    assert "ollama unavailable" in err
