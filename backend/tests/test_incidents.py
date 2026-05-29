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


def test_list_events_returns_them_in_stable_sort_order(client: TestClient) -> None:
    """The endpoint sorts by (created_at ASC, id ASC). We can't assert insertion
    order here because every API call in a single test runs inside the same
    DB transaction (test fixture wraps each test in a connection-level txn for
    rollback), and Postgres `now()` returns `transaction_timestamp()` - locked
    for the life of the txn - so all server-defaulted `created_at` values tie.
    Instead, assert what the endpoint contract actually guarantees: the rows
    come back sorted by (created_at, id) ASC, the order is stable across
    repeated calls, and the payload round-trips intact."""
    incident = _create_incident(client)
    inc_id = incident["id"]
    ev1 = client.post(
        f"/api/incidents/{inc_id}/events",
        json={
            "event_type": "syslog",
            "source": "edge-1",
            "message": "first event",
            "payload": {"step": 1},
        },
    ).json()
    ev2 = client.post(
        f"/api/incidents/{inc_id}/events",
        json={
            "event_type": "syslog",
            "source": "edge-1",
            "message": "second event",
            "payload": {"step": 2},
        },
    ).json()

    response = client.get(f"/api/incidents/{inc_id}/events")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 2
    # Set equality - both rows present.
    assert {x["id"] for x in items} == {ev1["id"], ev2["id"]}
    # Sort key matches the endpoint's documented order.
    expected = sorted(items, key=lambda x: (x["created_at"], x["id"]))
    assert items == expected
    # Stable across repeated calls.
    second = client.get(f"/api/incidents/{inc_id}/events").json()
    assert [x["id"] for x in items] == [x["id"] for x in second]
    # Payload round-trips intact.
    by_id = {x["id"]: x for x in items}
    assert by_id[ev1["id"]]["payload"] == {"step": 1}
    assert by_id[ev2["id"]]["payload"] == {"step": 2}


def test_list_events_404_for_missing_incident(client: TestClient) -> None:
    response = client.get(
        "/api/incidents/00000000-0000-0000-0000-000000000000/events"
    )
    assert response.status_code == 404


def test_list_events_rejects_out_of_range_limit(client: TestClient) -> None:
    incident = _create_incident(client)
    assert (
        client.get(f"/api/incidents/{incident['id']}/events?limit=0").status_code
        == 422
    )
    assert (
        client.get(f"/api/incidents/{incident['id']}/events?limit=500").status_code
        == 422
    )


def test_list_evidence_returns_them_in_stable_sort_order(
    client: TestClient,
) -> None:
    """Same rationale as the sibling event test."""
    incident = _create_incident(client)
    inc_id = incident["id"]
    e1 = client.post(
        f"/api/incidents/{inc_id}/evidence",
        json={
            "evidence_type": "command_output",
            "source": "edge-1",
            "content": "show ip route ...",
            "payload": {"step": 1},
        },
    ).json()
    e2 = client.post(
        f"/api/incidents/{inc_id}/evidence",
        json={
            "evidence_type": "command_output",
            "source": "edge-1",
            "content": "show interface eth0",
            "payload": {"step": 2},
        },
    ).json()

    response = client.get(f"/api/incidents/{inc_id}/evidence")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 2
    assert {x["id"] for x in items} == {e1["id"], e2["id"]}
    expected = sorted(items, key=lambda x: (x["created_at"], x["id"]))
    assert items == expected
    second = client.get(f"/api/incidents/{inc_id}/evidence").json()
    assert [x["id"] for x in items] == [x["id"] for x in second]


def test_list_evidence_404_for_missing_incident(client: TestClient) -> None:
    response = client.get(
        "/api/incidents/00000000-0000-0000-0000-000000000000/evidence"
    )
    assert response.status_code == 404


def test_list_evidence_rejects_out_of_range_limit(client: TestClient) -> None:
    incident = _create_incident(client)
    assert (
        client.get(
            f"/api/incidents/{incident['id']}/evidence?limit=0"
        ).status_code
        == 422
    )
    assert (
        client.get(
            f"/api/incidents/{incident['id']}/evidence?limit=500"
        ).status_code
        == 422
    )


def test_list_events_respects_limit(client: TestClient) -> None:
    incident = _create_incident(client)
    inc_id = incident["id"]
    for i in range(5):
        client.post(
            f"/api/incidents/{inc_id}/events",
            json={
                "event_type": "syslog",
                "source": "edge-1",
                "message": f"event {i}",
            },
        )
    response = client.get(f"/api/incidents/{inc_id}/events?limit=3")
    assert response.status_code == 200
    assert len(response.json()) == 3


def test_list_evidence_respects_limit(client: TestClient) -> None:
    incident = _create_incident(client)
    inc_id = incident["id"]
    for i in range(5):
        client.post(
            f"/api/incidents/{inc_id}/evidence",
            json={
                "evidence_type": "command_output",
                "source": "edge-1",
                "content": f"evidence {i}",
            },
        )
    response = client.get(f"/api/incidents/{inc_id}/evidence?limit=3")
    assert response.status_code == 200
    assert len(response.json()) == 3


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
