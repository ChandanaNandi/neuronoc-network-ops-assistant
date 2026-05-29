from fastapi.testclient import TestClient


def _create_incident(client: TestClient, **overrides: object) -> dict:
    payload: dict[str, object] = {
        "title": "BGP session flap on edge-1",
        "severity": "high",
        "incident_type": "bgp_flap",
        "summary": "Repeated BGP session resets toward AS65001",
    }
    payload.update(overrides)
    response = client.post("/api/incidents", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_incident_defaults_status_to_open(client: TestClient) -> None:
    created = _create_incident(client)
    assert created["status"] == "open"
    assert created["severity"] == "high"
    assert created["incident_type"] == "bgp_flap"
    assert created["id"]
    assert created["created_at"]


def test_list_incidents_returns_created(client: TestClient) -> None:
    created = _create_incident(client, title="Latency spike on core-2")
    response = client.get("/api/incidents")
    assert response.status_code == 200
    bodies = response.json()
    ids = [item["id"] for item in bodies]
    assert created["id"] in ids


def test_list_incidents_respects_limit(client: TestClient) -> None:
    for i in range(3):
        _create_incident(client, title=f"incident-{i}", incident_type="probe")
    response = client.get("/api/incidents?limit=2")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_list_incidents_rejects_oversized_limit(client: TestClient) -> None:
    response = client.get("/api/incidents?limit=500")
    assert response.status_code == 422


def test_get_incident_by_id(client: TestClient) -> None:
    created = _create_incident(client)
    response = client.get(f"/api/incidents/{created['id']}")
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


def test_get_incident_404(client: TestClient) -> None:
    response = client.get("/api/incidents/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert response.json()["detail"] == "incident not found"


def test_add_event_to_incident(client: TestClient) -> None:
    incident = _create_incident(client)
    event_payload = {
        "event_type": "syslog",
        "source": "edge-1",
        "message": "GigabitEthernet0/1 changed state to down",
        "payload": {"iface": "Gi0/1", "reason": "link-down"},
    }
    response = client.post(
        f"/api/incidents/{incident['id']}/events", json=event_payload
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["incident_id"] == incident["id"]
    assert body["event_type"] == "syslog"
    assert body["payload"] == {"iface": "Gi0/1", "reason": "link-down"}


def test_add_event_to_missing_incident_returns_404(client: TestClient) -> None:
    event_payload = {
        "event_type": "syslog",
        "source": "edge-1",
        "message": "irrelevant",
    }
    response = client.post(
        "/api/incidents/00000000-0000-0000-0000-000000000000/events",
        json=event_payload,
    )
    assert response.status_code == 404


def test_add_evidence_to_incident(client: TestClient) -> None:
    incident = _create_incident(client)
    evidence_payload = {
        "evidence_type": "command_output",
        "source": "edge-2",
        "content": "show ip route\n10.0.0.0/8 via 1.1.1.1",
    }
    response = client.post(
        f"/api/incidents/{incident['id']}/evidence", json=evidence_payload
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["evidence_type"] == "command_output"
    assert body["incident_id"] == incident["id"]


def test_add_recommendation_to_incident(client: TestClient) -> None:
    incident = _create_incident(client)
    rec_payload = {
        "recommendation_type": "ansible_playbook",
        "title": "Clear stuck BGP session",
        "details": "Run `clear ip bgp 1.1.1.1 soft in` on edge-1; capture before/after.",
        "risk": "medium",
        "requires_approval": True,
    }
    response = client.post(
        f"/api/incidents/{incident['id']}/recommendations", json=rec_payload
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["risk"] == "medium"
    assert body["requires_approval"] is True
    assert body["incident_id"] == incident["id"]
