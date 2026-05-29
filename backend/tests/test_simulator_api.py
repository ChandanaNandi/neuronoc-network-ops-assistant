"""HTTP-surface tests for the optional simulator API endpoints.

Uses the same real-Postgres `client` fixture as the rest of the suite — every
request runs inside a transaction that gets rolled back at the end of the test.
"""

from fastapi.testclient import TestClient

from app.simulator.scenarios import ALL_SCENARIO_NAMES


def test_seed_one_scenario_returns_201_and_one_id(client: TestClient) -> None:
    response = client.post(
        "/api/simulator/seed", params={"scenario": "bgp_neighbor_down"}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert isinstance(body["created_incident_ids"], list)
    assert len(body["created_incident_ids"]) == 1

    incident_id = body["created_incident_ids"][0]
    fetched = client.get(f"/api/incidents/{incident_id}")
    assert fetched.status_code == 200
    assert fetched.json()["incident_type"] == "bgp_neighbor_down"


def test_seed_all_returns_201_and_one_id_per_scenario(client: TestClient) -> None:
    response = client.post("/api/simulator/seed", params={"scenario": "all"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert len(body["created_incident_ids"]) == len(ALL_SCENARIO_NAMES)
    # All returned ids should be fetchable.
    for incident_id in body["created_incident_ids"]:
        assert client.get(f"/api/incidents/{incident_id}").status_code == 200


def test_seed_unknown_scenario_returns_404(client: TestClient) -> None:
    response = client.post("/api/simulator/seed", params={"scenario": "bad_name"})
    assert response.status_code == 404
    assert "unknown scenario" in response.json()["detail"]


def test_reset_removes_simulator_incidents_only(client: TestClient) -> None:
    # 1. Create a manual incident via the public incidents API.
    manual = client.post(
        "/api/incidents",
        json={
            "title": "operator-created incident (must survive reset)",
            "severity": "low",
            "incident_type": "manual_test",
            "summary": "no simulator marker — must NOT be deleted by reset",
        },
    ).json()
    manual_id = manual["id"]

    # 2. Seed all simulator scenarios.
    seed = client.post("/api/simulator/seed", params={"scenario": "all"})
    assert seed.status_code == 201, seed.text
    sim_ids = seed.json()["created_incident_ids"]
    assert len(sim_ids) == len(ALL_SCENARIO_NAMES)

    # 3. Reset.
    reset = client.post("/api/simulator/reset")
    assert reset.status_code == 200, reset.text
    assert reset.json()["removed_incidents"] >= len(ALL_SCENARIO_NAMES)

    # 4. None of the simulator incidents are fetchable any more.
    for sim_id in sim_ids:
        gone = client.get(f"/api/incidents/{sim_id}")
        assert gone.status_code == 404, (
            f"simulator incident {sim_id} should have been removed by reset"
        )

    # 5. The manual incident is still there.
    still = client.get(f"/api/incidents/{manual_id}")
    assert still.status_code == 200, "reset must not delete operator-created incidents"
