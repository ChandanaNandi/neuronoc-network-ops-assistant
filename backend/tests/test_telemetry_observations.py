"""Phase 22A telemetry persistence tests.

Three contracts pinned here:
1. The new `POST /api/telemetry/observations` writes one row per call,
   and the GET endpoints read it back faithfully.
2. JSONB round-trip — nested structures in the payload survive a
   POST/GET cycle.
3. Backward compatibility — the existing Phase 18A `/validate` and
   Phase 18B `/correlate/preview` endpoints continue to NOT touch the
   database, including the new `telemetry_observations` table.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Incident, TelemetryObservation
from app.telemetry.events import TelemetryEvent
from app.telemetry.persistence import persist_telemetry_observation


def _valid_payload(**overrides) -> dict:
    base = {
        "source": "snmp:edge-1",
        "collector_type": "snmp",
        "hostname": "edge-1",
        "mgmt_ip": "10.0.0.11",
        "device_hint": "edge-1.lab",
        "observed_at": datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc).isoformat(),
        "event_type": "bgp_neighbor_down",
        "severity": "critical",
        "message": "BGP neighbor 10.0.0.21 transitioned to Idle",
        "labels": {"neighbor": "10.0.0.21", "vrf": "default"},
        "raw": {"trap_oid": "1.3.6.1.4.1.9.9.187.0.1", "peer_state": "idle"},
    }
    base.update(overrides)
    return base


# ---------- direct helper ----------


def test_persist_telemetry_observation_writes_row(db_session: Session) -> None:
    event = TelemetryEvent.model_validate(_valid_payload())
    obs = persist_telemetry_observation(db_session, event)

    assert obs.id is not None
    assert obs.source == "snmp:edge-1"
    assert obs.observation_type == "bgp_neighbor_down"
    assert obs.received_at is not None
    assert obs.created_incident_id is None  # Phase 22A: no auto-correlation
    # payload round-trip: full event dict landed in JSONB
    assert obs.payload["source"] == "snmp:edge-1"
    assert obs.payload["severity"] == "critical"
    assert obs.payload["labels"]["neighbor"] == "10.0.0.21"


def test_persist_extracts_vendor_from_labels(db_session: Session) -> None:
    event = TelemetryEvent.model_validate(
        _valid_payload(labels={"vendor": "acme-net", "site": "dc-a"})
    )
    obs = persist_telemetry_observation(db_session, event)
    assert obs.vendor == "acme-net"


def test_persist_with_no_vendor_label_leaves_vendor_null(
    db_session: Session,
) -> None:
    event = TelemetryEvent.model_validate(_valid_payload(labels={}))
    obs = persist_telemetry_observation(db_session, event)
    assert obs.vendor is None


# ---------- POST /api/telemetry/observations ----------


def test_api_post_observations_persists_and_returns_201(
    client: TestClient, db_session: Session
) -> None:
    response = client.post(
        "/api/telemetry/observations", json=_valid_payload()
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert "id" in body
    assert body["source"] == "snmp:edge-1"
    assert body["observation_type"] == "bgp_neighbor_down"
    assert body["created_incident_id"] is None
    assert "received_at" in body
    # Server-stamped timestamp is parseable.
    datetime.fromisoformat(body["received_at"])

    # The row actually landed in the table.
    count = db_session.scalar(
        select(func.count()).select_from(TelemetryObservation)
    )
    assert count == 1


def test_api_post_422_on_invalid_event(client: TestClient) -> None:
    response = client.post(
        "/api/telemetry/observations",
        json=_valid_payload(severity="catastrophic"),  # bad enum value
    )
    assert response.status_code == 422


def test_api_post_does_not_auto_create_incident(
    client: TestClient, db_session: Session
) -> None:
    """Phase 22A contract: persisting an observation must NOT auto-create
    an Incident. The `created_incident_id` FK stays null; the Incident
    table stays at the same row count."""
    incidents_before = db_session.scalar(
        select(func.count()).select_from(Incident)
    )
    response = client.post(
        "/api/telemetry/observations", json=_valid_payload()
    )
    assert response.status_code == 201
    incidents_after = db_session.scalar(
        select(func.count()).select_from(Incident)
    )
    assert incidents_after == incidents_before
    assert response.json()["created_incident_id"] is None


def test_api_post_payload_round_trips_through_jsonb(
    client: TestClient, db_session: Session
) -> None:
    """Nested labels + raw structures survive a POST then GET cycle."""
    rich_raw = {
        "trap_oid": "1.3.6.1.4.1.9.9.187.0.1",
        "peer_state": "idle",
        "metrics": "k=v,a=b",  # value is a string per schema
        "uptime_seconds": "3600",
    }
    rich_labels = {
        "neighbor": "10.0.0.21",
        "vrf": "default",
        "vendor": "frr",
    }
    response = client.post(
        "/api/telemetry/observations",
        json=_valid_payload(labels=rich_labels, raw=rich_raw),
    )
    assert response.status_code == 201, response.text
    obs_id = response.json()["id"]

    # Read it back through the GET endpoint.
    read = client.get(f"/api/telemetry/observations/{obs_id}")
    assert read.status_code == 200, read.text
    body = read.json()
    assert body["payload"]["labels"] == rich_labels
    assert body["payload"]["raw"] == rich_raw
    # Vendor was lifted from labels at persist time.
    assert body["vendor"] == "frr"


# ---------- GET /api/telemetry/observations ----------


def test_api_get_observations_returns_newest_first(
    client: TestClient, db_session: Session
) -> None:
    """Insert three rows directly with explicit, ascending received_at
    timestamps so the test can assert true newest-first ordering. The
    API path is exercised by the other tests; here we just need
    monotonically-increasing timestamps, which the savepoint-based
    fixture's shared `now()` can't give us via the API."""
    from datetime import timedelta

    base_time = datetime(2026, 5, 30, 12, 0, 0, tzinfo=timezone.utc)
    ids_in_insert_order: list[str] = []
    for i in range(3):
        obs = TelemetryObservation(
            source=f"snmp:edge-{i}",
            observation_type="bgp_neighbor_down",
            payload={"i": i},
            received_at=base_time + timedelta(seconds=i),
        )
        db_session.add(obs)
        db_session.flush()
        ids_in_insert_order.append(str(obs.id))

    response = client.get("/api/telemetry/observations")
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) >= 3
    returned_ids = [row["id"] for row in body[:3]]
    # Newest received_at first => insertion order reversed.
    assert returned_ids == list(reversed(ids_in_insert_order))


def test_api_get_observations_respects_limit(
    client: TestClient, db_session: Session
) -> None:
    for i in range(5):
        client.post(
            "/api/telemetry/observations",
            json=_valid_payload(message=f"obs-{i}"),
        )
    response = client.get("/api/telemetry/observations?limit=2")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_api_get_observations_rejects_out_of_range_limit(
    client: TestClient,
) -> None:
    assert client.get("/api/telemetry/observations?limit=0").status_code == 422
    assert client.get("/api/telemetry/observations?limit=101").status_code == 422


# ---------- GET /api/telemetry/observations/{id} ----------


def test_api_get_observation_404_for_missing_id(client: TestClient) -> None:
    response = client.get(f"/api/telemetry/observations/{uuid4()}")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_api_get_observation_returns_persisted_row(
    client: TestClient, db_session: Session
) -> None:
    post_resp = client.post(
        "/api/telemetry/observations", json=_valid_payload()
    )
    obs_id = post_resp.json()["id"]

    read = client.get(f"/api/telemetry/observations/{obs_id}")
    assert read.status_code == 200
    body = read.json()
    assert body["id"] == obs_id
    assert body["source"] == "snmp:edge-1"
    assert body["observation_type"] == "bgp_neighbor_down"


# ---------- Backward compat: validate + preview still non-persisting ----------


def test_validate_endpoint_remains_non_persisting_after_phase22a(
    client: TestClient, db_session: Session
) -> None:
    """Phase 22A backward-compat pin: `/validate` MUST NOT write any row
    to `telemetry_observations`. Phase 18A guarantee preserved."""
    before = db_session.scalar(
        select(func.count()).select_from(TelemetryObservation)
    )
    response = client.post(
        "/api/telemetry/validate", json=_valid_payload()
    )
    assert response.status_code == 200
    after = db_session.scalar(
        select(func.count()).select_from(TelemetryObservation)
    )
    assert after == before


def test_correlate_preview_remains_non_persisting_after_phase22a(
    client: TestClient, db_session: Session
) -> None:
    """Phase 22A backward-compat pin: `/correlate/preview` MUST NOT
    write any row to `telemetry_observations`. Phase 18B guarantee
    preserved, including `persisted: false` on the response."""
    before = db_session.scalar(
        select(func.count()).select_from(TelemetryObservation)
    )
    response = client.post(
        "/api/telemetry/correlate/preview", json=_valid_payload()
    )
    assert response.status_code == 200
    body = response.json()
    assert body["persisted"] is False
    after = db_session.scalar(
        select(func.count()).select_from(TelemetryObservation)
    )
    assert after == before


# ============================================================
# Phase 22B - correlate persisted observations into incidents
# ============================================================


from app.db.models import IncidentEvent  # noqa: E402


def test_correlate_persisted_bgp_observation_creates_incident(
    client: TestClient, db_session: Session
) -> None:
    """Phase 22B: a persisted BGP-shaped observation correlated via
    POST /api/telemetry/observations/{id}/correlate must produce ONE
    Incident with the suggested incident_type / title / severity from
    the existing Phase 18B preview."""
    post_resp = client.post(
        "/api/telemetry/observations", json=_valid_payload()
    )
    obs_id = post_resp.json()["id"]
    incidents_before = db_session.scalar(
        select(func.count()).select_from(Incident)
    )

    response = client.post(
        f"/api/telemetry/observations/{obs_id}/correlate"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["observation_id"] == obs_id
    assert body["correlated"] is True
    assert body["incident_created"] is True
    assert body["incident_id"] is not None
    assert body["suggested_incident_type"] == "bgp_neighbor_down"
    assert body["rationale"]  # non-empty

    # Exactly one new Incident with the expected shape.
    incidents_after = db_session.scalar(
        select(func.count()).select_from(Incident)
    )
    assert incidents_after == incidents_before + 1
    incident = db_session.get(Incident, body["incident_id"])
    assert incident is not None
    assert incident.incident_type == "bgp_neighbor_down"
    assert "BGP neighbor down" in incident.title or "BGP" in incident.title
    assert incident.severity == "critical"  # critical TelemetrySeverity -> critical


def test_correlate_creates_one_incident_event_with_suggested_fields(
    client: TestClient, db_session: Session
) -> None:
    post_resp = client.post(
        "/api/telemetry/observations", json=_valid_payload()
    )
    obs_id = post_resp.json()["id"]
    resp = client.post(
        f"/api/telemetry/observations/{obs_id}/correlate"
    )
    incident_id = resp.json()["incident_id"]

    events = db_session.scalars(
        select(IncidentEvent).where(IncidentEvent.incident_id == incident_id)
    ).all()
    assert len(events) == 1
    e = events[0]
    assert e.event_type == "bgp_state_change"  # Phase 18B suggested for BGP
    assert e.source == "snmp:edge-1"  # suggested_event_source = original source
    # Payload nests under "telemetry" key per Phase 18B contract.
    assert e.payload is not None
    assert "telemetry" in e.payload
    assert e.payload["telemetry"]["source"] == "snmp:edge-1"


def test_correlate_sets_created_incident_id_on_observation(
    client: TestClient, db_session: Session
) -> None:
    post_resp = client.post(
        "/api/telemetry/observations", json=_valid_payload()
    )
    obs_id = post_resp.json()["id"]
    resp = client.post(
        f"/api/telemetry/observations/{obs_id}/correlate"
    )
    incident_id = resp.json()["incident_id"]

    # Reload via GET to verify the FK was persisted, not just returned in-memory.
    refreshed = client.get(f"/api/telemetry/observations/{obs_id}")
    assert refreshed.json()["created_incident_id"] == incident_id


def test_correlating_the_same_observation_twice_is_idempotent(
    client: TestClient, db_session: Session
) -> None:
    """Re-running correlate on a row with `created_incident_id` already
    set must return the SAME incident_id, NOT create a duplicate
    Incident, and surface `incident_created=False`."""
    post_resp = client.post(
        "/api/telemetry/observations", json=_valid_payload()
    )
    obs_id = post_resp.json()["id"]

    first = client.post(f"/api/telemetry/observations/{obs_id}/correlate")
    incidents_after_first = db_session.scalar(
        select(func.count()).select_from(Incident)
    )
    assert first.json()["incident_created"] is True

    second = client.post(f"/api/telemetry/observations/{obs_id}/correlate")
    assert second.status_code == 200
    second_body = second.json()
    assert second_body["incident_created"] is False
    assert second_body["correlated"] is True
    assert second_body["incident_id"] == first.json()["incident_id"]

    # No new Incident row.
    incidents_after_second = db_session.scalar(
        select(func.count()).select_from(Incident)
    )
    assert incidents_after_second == incidents_after_first

    # And no second IncidentEvent either.
    events = db_session.scalars(
        select(IncidentEvent).where(
            IncidentEvent.incident_id == first.json()["incident_id"]
        )
    ).all()
    assert len(events) == 1


def test_correlate_unknown_observation_does_not_create_incident(
    client: TestClient, db_session: Session
) -> None:
    """Phase 18B generic-fallback path: an event with no rule-keyword
    match maps to `telemetry_observation` with `would_create_incident=False`.
    Correlation must respect that: no Incident, no IncidentEvent,
    `created_incident_id` stays null."""
    # Same shape as the Phase 19A "unknown vendor" fixture: no BGP /
    # interface / latency / route / ACL keywords.
    unknown_payload = _valid_payload(
        event_type="vendor_proprietary_trap",
        message="device emitted a vendor-specific diagnostic",
        labels={"vendor": "acme-net", "trap_kind": "diag-notify"},
        raw={"trap_oid": "1.3.6.1.4.1.99999.1.2.3"},
    )
    post_resp = client.post(
        "/api/telemetry/observations", json=unknown_payload
    )
    obs_id = post_resp.json()["id"]
    incidents_before = db_session.scalar(
        select(func.count()).select_from(Incident)
    )

    response = client.post(
        f"/api/telemetry/observations/{obs_id}/correlate"
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["correlated"] is False
    assert body["incident_created"] is False
    assert body["incident_id"] is None
    assert body["suggested_incident_type"] == "telemetry_observation"

    # No Incident row created.
    incidents_after = db_session.scalar(
        select(func.count()).select_from(Incident)
    )
    assert incidents_after == incidents_before

    # FK on the observation row stays null.
    refreshed = client.get(f"/api/telemetry/observations/{obs_id}")
    assert refreshed.json()["created_incident_id"] is None


def test_correlate_404_for_missing_observation_id(client: TestClient) -> None:
    response = client.post(
        f"/api/telemetry/observations/{uuid4()}/correlate"
    )
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_correlate_endpoint_does_not_call_llm_or_remediation(
    client: TestClient, db_session: Session, monkeypatch
) -> None:
    """Pin that correlation runs deterministically: no LLM (Ollama), no
    remediation planner. Stub both at the module level so any accidental
    invocation would raise instead of silently calling out."""
    from app.llm import ollama as ollama_module
    from app.remediation import planner as planner_module

    def _explode_ollama(*a, **kw):
        raise AssertionError(
            "correlation must not call Ollama / generate_ollama_json"
        )

    def _explode_remediation(*a, **kw):
        raise AssertionError(
            "correlation must not call remediation planner"
        )

    monkeypatch.setattr(
        ollama_module, "generate_ollama_json", _explode_ollama
    )
    monkeypatch.setattr(
        planner_module, "build_remediation_plan", _explode_remediation
    )

    post_resp = client.post(
        "/api/telemetry/observations", json=_valid_payload()
    )
    obs_id = post_resp.json()["id"]
    resp = client.post(
        f"/api/telemetry/observations/{obs_id}/correlate"
    )
    assert resp.status_code == 200
    assert resp.json()["incident_created"] is True


def test_preview_endpoint_remains_non_persisting_after_phase22b(
    client: TestClient, db_session: Session
) -> None:
    """Phase 22B backward-compat pin: `/correlate/preview` is unchanged.
    The Phase 22A pin still holds AND no Incident row appears either."""
    obs_before = db_session.scalar(
        select(func.count()).select_from(TelemetryObservation)
    )
    inc_before = db_session.scalar(
        select(func.count()).select_from(Incident)
    )
    response = client.post(
        "/api/telemetry/correlate/preview", json=_valid_payload()
    )
    assert response.status_code == 200
    assert response.json()["persisted"] is False
    assert db_session.scalar(
        select(func.count()).select_from(TelemetryObservation)
    ) == obs_before
    assert db_session.scalar(
        select(func.count()).select_from(Incident)
    ) == inc_before
