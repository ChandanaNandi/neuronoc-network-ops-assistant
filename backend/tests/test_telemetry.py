"""Phase 18A telemetry skeleton tests.

Three contracts to defend:
1. `TelemetryEvent` validates / normalizes shape + enforces bounds.
2. The validate endpoint is read-only - exercises the schema and never
   writes to the database (asserted with a before/after row count).
3. The telemetry package + its HTTP wrapper never imports a network /
   execution library. Forbidden set covers SNMP libs, syslog libs,
   raw sockets, asyncio, and the existing execution-lib set.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AgentRun,
    AgentStep,
    Incident,
    IncidentEvent,
    IncidentEvidence,
    Recommendation,
)
from app.telemetry import (
    SNMPAdapter,
    SyslogAdapter,
    TelemetryCorrelationPreview,
    TelemetryEvent,
    build_correlation_preview,
    normalize_manual_event,
)
from app.telemetry.events import (
    LABELS_MAX_ENTRIES,
    RAW_PAYLOAD_MAX_KEYS,
    RAW_PAYLOAD_MAX_VALUE_LEN,
    CollectorType,
    TelemetrySeverity,
)


# ---------- helpers ----------


def _valid_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "source": "snmp:core-1",
        "collector_type": "snmp",
        "hostname": "core-1",
        "mgmt_ip": "10.0.0.1",
        "device_hint": "core-1.lab",
        "observed_at": datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc).isoformat(),
        "event_type": "interface_down",
        "severity": "warning",
        "message": "GigabitEthernet0/1 transitioned to down",
        "labels": {"interface": "Gi0/1", "site": "dc-a"},
        "raw": {"oid": "1.3.6.1.2.1.2.2.1.8.1", "value": "2"},
    }
    base.update(overrides)
    return base


# ---------- TelemetryEvent: happy path ----------


def test_telemetry_event_roundtrips_a_well_formed_snmp_payload() -> None:
    event = TelemetryEvent.model_validate(_valid_payload())
    assert event.source == "snmp:core-1"
    assert event.collector_type is CollectorType.snmp
    assert event.severity is TelemetrySeverity.warning
    assert event.hostname == "core-1"
    assert event.observed_at.tzinfo is not None
    assert event.labels == {"interface": "Gi0/1", "site": "dc-a"}
    assert event.raw == {"oid": "1.3.6.1.2.1.2.2.1.8.1", "value": "2"}


def test_telemetry_event_defaults_keep_optional_fields_empty() -> None:
    minimal = TelemetryEvent.model_validate(
        _valid_payload(
            hostname=None,
            mgmt_ip=None,
            device_hint=None,
            labels={},
            raw={},
        )
    )
    assert minimal.hostname is None
    assert minimal.mgmt_ip is None
    assert minimal.device_hint is None
    assert minimal.labels == {}
    assert minimal.raw == {}


# ---------- TelemetryEvent: enum + required-field validation ----------


def test_telemetry_event_rejects_unknown_severity() -> None:
    with pytest.raises(ValidationError):
        TelemetryEvent.model_validate(_valid_payload(severity="catastrophic"))


def test_telemetry_event_rejects_unknown_collector_type() -> None:
    with pytest.raises(ValidationError):
        TelemetryEvent.model_validate(_valid_payload(collector_type="kafka"))


def test_telemetry_event_rejects_empty_required_strings() -> None:
    for empty_field in ("source", "event_type", "message"):
        with pytest.raises(ValidationError):
            TelemetryEvent.model_validate(_valid_payload(**{empty_field: ""}))


def test_telemetry_event_rejects_extra_fields() -> None:
    """extra='forbid' so the schema is the contract - upstream parsers
    can't sneak unbounded fields in by accident."""
    with pytest.raises(ValidationError):
        TelemetryEvent.model_validate(
            _valid_payload(extraneous_field="should fail")
        )


def test_telemetry_event_requires_observed_at() -> None:
    payload = _valid_payload()
    payload.pop("observed_at")
    with pytest.raises(ValidationError):
        TelemetryEvent.model_validate(payload)


# ---------- TelemetryEvent: bounds ----------


def test_telemetry_event_raw_payload_capped_at_key_count() -> None:
    too_many = {f"k{i}": "v" for i in range(RAW_PAYLOAD_MAX_KEYS + 1)}
    with pytest.raises(ValidationError, match="max is"):
        TelemetryEvent.model_validate(_valid_payload(raw=too_many))


def test_telemetry_event_raw_payload_capped_at_value_len() -> None:
    big_value = "x" * (RAW_PAYLOAD_MAX_VALUE_LEN + 1)
    with pytest.raises(ValidationError, match="max is"):
        TelemetryEvent.model_validate(_valid_payload(raw={"k": big_value}))


def test_telemetry_event_labels_capped_at_entry_count() -> None:
    too_many = {f"k{i}": "v" for i in range(LABELS_MAX_ENTRIES + 1)}
    with pytest.raises(ValidationError, match="max is"):
        TelemetryEvent.model_validate(_valid_payload(labels=too_many))


# ---------- normalize_manual_event ----------


def test_normalize_manual_event_accepts_dict() -> None:
    event = normalize_manual_event(_valid_payload())
    assert isinstance(event, TelemetryEvent)


def test_normalize_manual_event_passes_existing_model_through() -> None:
    """The helper is a single entry point - callers that already built a
    model don't need to re-validate."""
    built = TelemetryEvent.model_validate(_valid_payload())
    assert normalize_manual_event(built) is built


def test_normalize_manual_event_raises_on_bad_payload() -> None:
    with pytest.raises(ValidationError):
        normalize_manual_event({"source": "missing-everything-else"})


# ---------- adapters: protocols only ----------


def test_adapters_are_protocols_runtime_checkable() -> None:
    """The adapter classes must be `runtime_checkable` Protocols so a
    future implementation can be `isinstance`-checked at integration
    time without import-time coupling."""

    class FakeSnmp:
        def poll(
            self,
            host: str,
            community: str | None = None,
            oids: list[str] | None = None,
        ) -> list[TelemetryEvent]:
            return []

    class FakeSyslog:
        def parse(
            self,
            line: str,
            *,
            default_hostname: str | None = None,
        ) -> TelemetryEvent | None:
            return None

    assert isinstance(FakeSnmp(), SNMPAdapter)
    assert isinstance(FakeSyslog(), SyslogAdapter)

    # And a class that doesn't match the contract fails isinstance.
    class NotAnAdapter:
        pass

    assert not isinstance(NotAnAdapter(), SNMPAdapter)
    assert not isinstance(NotAnAdapter(), SyslogAdapter)


# ---------- API: happy + error paths ----------


def test_api_validate_happy_path_echoes_normalized_event(
    client: TestClient,
) -> None:
    response = client.post("/api/telemetry/validate", json=_valid_payload())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "snmp:core-1"
    assert body["collector_type"] == "snmp"
    assert body["severity"] == "warning"
    assert body["labels"] == {"interface": "Gi0/1", "site": "dc-a"}


def test_api_validate_422_for_unknown_severity(client: TestClient) -> None:
    response = client.post(
        "/api/telemetry/validate",
        json=_valid_payload(severity="catastrophic"),
    )
    assert response.status_code == 422


def test_api_validate_422_for_extra_field(client: TestClient) -> None:
    response = client.post(
        "/api/telemetry/validate",
        json=_valid_payload(rogue_field=True),
    )
    assert response.status_code == 422


def test_api_validate_422_for_oversized_raw_payload(client: TestClient) -> None:
    too_many = {f"k{i}": "v" for i in range(RAW_PAYLOAD_MAX_KEYS + 1)}
    response = client.post(
        "/api/telemetry/validate", json=_valid_payload(raw=too_many)
    )
    assert response.status_code == 422


def test_api_validate_writes_nothing_to_the_database(
    client: TestClient, db_session: Session
) -> None:
    """Phase 18A's whole point is read-only / no-persistence. Validate a
    payload, then assert no domain table grew a row."""

    def _counts() -> dict[str, int]:
        return {
            "incidents": db_session.scalar(
                select(func.count()).select_from(Incident)
            ),
            "events": db_session.scalar(
                select(func.count()).select_from(IncidentEvent)
            ),
            "evidence": db_session.scalar(
                select(func.count()).select_from(IncidentEvidence)
            ),
            "recommendations": db_session.scalar(
                select(func.count()).select_from(Recommendation)
            ),
            "agent_runs": db_session.scalar(
                select(func.count()).select_from(AgentRun)
            ),
            "agent_steps": db_session.scalar(
                select(func.count()).select_from(AgentStep)
            ),
        }

    before = _counts()
    response = client.post("/api/telemetry/validate", json=_valid_payload())
    assert response.status_code == 200
    after = _counts()
    assert before == after, (
        f"telemetry validate must not persist anything; counts diverged: "
        f"before={before} after={after}"
    )


# ---------- Phase 18B: correlation preview helper ----------


def _bgp_event(**overrides: object) -> TelemetryEvent:
    return TelemetryEvent.model_validate(
        _valid_payload(
            event_type="bgp_neighbor_down",
            message="BGP neighbor 10.0.0.21 transitioned to Idle",
            severity="critical",
            labels={"neighbor": "10.0.0.21", "vrf": "default"},
            **overrides,
        )
    )


def test_correlate_bgp_event_maps_to_bgp_neighbor_down_with_high_confidence() -> None:
    preview = build_correlation_preview(_bgp_event())
    assert isinstance(preview, TelemetryCorrelationPreview)
    assert preview.suggested_incident_type == "bgp_neighbor_down"
    assert preview.suggested_event_type == "bgp_state_change"
    assert preview.suggested_severity == "critical"
    assert preview.confidence == 0.9  # event_type-driven match
    assert preview.would_create_incident is True
    assert preview.would_create_event is True
    assert preview.persisted is False
    # Correlation key narrows by peer so two neighbors don't collapse.
    assert "core-1" in preview.correlation_key
    assert "peer=10.0.0.21" in preview.correlation_key
    assert "BGP" in preview.suggested_title


def test_correlate_interface_down_maps_to_interface_errors_spike() -> None:
    event = TelemetryEvent.model_validate(
        _valid_payload(
            event_type="interface_down",
            message="GigabitEthernet0/1 transitioned to down",
            severity="error",
            labels={"interface": "Gi0/1"},
        )
    )
    preview = build_correlation_preview(event)
    assert preview.suggested_incident_type == "interface_errors_spike"
    assert preview.suggested_event_type == "interface_down"
    assert preview.suggested_severity == "high"  # error -> high
    assert preview.would_create_incident is True
    # Interface label narrows the correlation key so two failing
    # interfaces on the same router don't dedupe into one incident.
    assert "if=Gi0/1" in preview.correlation_key


def test_correlate_latency_event_maps_to_latency_spike() -> None:
    event = TelemetryEvent.model_validate(
        _valid_payload(
            event_type="latency_spike",
            message="RTT to 10.0.0.21 above 500 ms",
            severity="warning",
        )
    )
    preview = build_correlation_preview(event)
    assert preview.suggested_incident_type == "latency_spike"
    assert preview.suggested_severity == "medium"  # warning -> medium
    assert preview.would_create_incident is True


def test_correlate_route_missing_event_maps_to_route_missing() -> None:
    event = TelemetryEvent.model_validate(
        _valid_payload(
            event_type="route_withdrawn",
            message="prefix 10.0.0.0/24 withdrawn from RIB",
            severity="error",
            labels={"prefix": "10.0.0.0/24"},
        )
    )
    preview = build_correlation_preview(event)
    assert preview.suggested_incident_type == "route_missing"
    assert preview.suggested_event_type == "route_missing"
    # Correlation key narrows by prefix.
    assert "prefix=10.0.0.0/24" in preview.correlation_key


def test_correlate_acl_event_maps_to_acl_blocking_traffic() -> None:
    event = TelemetryEvent.model_validate(
        _valid_payload(
            event_type="acl_deny",
            message="ACL inbound denied 100 packets to 10.0.0.21",
            severity="warning",
        )
    )
    preview = build_correlation_preview(event)
    assert preview.suggested_incident_type == "acl_blocking_traffic"
    assert preview.would_create_incident is True


def test_correlate_unknown_observation_falls_back_to_telemetry_observation() -> None:
    """Generic / unmappable events get the safe fallback. Critically,
    would_create_incident is False - we'd log the event but NOT
    auto-open an incident off something we don't understand."""
    event = TelemetryEvent.model_validate(
        _valid_payload(
            event_type="some_unmapped_thing",
            message="device emitted a vendor proprietary trap",
            severity="info",
            labels={},
            raw={},
        )
    )
    preview = build_correlation_preview(event)
    assert preview.suggested_incident_type == "telemetry_observation"
    assert preview.would_create_incident is False  # << key contract
    assert preview.would_create_event is True
    assert preview.confidence == 0.3
    assert preview.suggested_severity == "low"  # info -> low


def test_correlate_severity_mapping_covers_every_telemetry_level() -> None:
    """info+notice -> low, warning -> medium, error -> high, critical -> critical."""
    expected = {
        "info": "low",
        "notice": "low",
        "warning": "medium",
        "error": "high",
        "critical": "critical",
    }
    for tele_sev, incident_sev in expected.items():
        event = TelemetryEvent.model_validate(
            _valid_payload(
                event_type="bgp_neighbor_down",
                severity=tele_sev,
            )
        )
        preview = build_correlation_preview(event)
        assert preview.suggested_severity == incident_sev, (
            f"{tele_sev} should map to {incident_sev}, got {preview.suggested_severity}"
        )


def test_correlate_message_match_uses_lower_confidence_than_event_type_match() -> None:
    """A BGP match by message keywords scores 0.6; the same shape matched
    by event_type scores 0.9. Future correlator code should be able to
    rely on this gap to weight upgrades vs. logs differently."""
    by_message = TelemetryEvent.model_validate(
        _valid_payload(
            event_type="syslog_message",  # generic
            message="BGP neighbor 10.0.0.21 went down",
            severity="critical",
        )
    )
    by_event_type = TelemetryEvent.model_validate(
        _valid_payload(
            event_type="bgp_neighbor_down",  # direct
            message="same shape, different signal source",
            severity="critical",
        )
    )
    assert build_correlation_preview(by_message).confidence == 0.6
    assert build_correlation_preview(by_event_type).confidence == 0.9


def test_correlate_event_payload_preserves_full_telemetry_under_nested_key() -> None:
    """suggested_event_payload nests under a `telemetry` key so any
    future hand-added IncidentEvent.payload fields can coexist without
    collision."""
    event = _bgp_event()
    preview = build_correlation_preview(event)
    payload = preview.suggested_event_payload
    assert set(payload.keys()) == {"telemetry"}
    nested = payload["telemetry"]
    assert nested["source"] == event.source
    assert nested["collector_type"] == event.collector_type.value
    assert nested["severity"] == event.severity.value
    assert nested["labels"] == event.labels
    assert nested["raw"] == event.raw


def test_correlate_device_descriptor_falls_back_through_hostname_hint_ip() -> None:
    """Title + correlation_key need a stable device identity even when
    hostname is missing - device_hint and mgmt_ip are the fallbacks."""
    for ident_field in ("hostname", "device_hint", "mgmt_ip"):
        kwargs: dict[str, object] = {
            "hostname": None,
            "device_hint": None,
            "mgmt_ip": None,
        }
        kwargs[ident_field] = "stub-identifier-7"
        event = TelemetryEvent.model_validate(
            _valid_payload(event_type="bgp_neighbor_down", **kwargs)
        )
        preview = build_correlation_preview(event)
        assert "stub-identifier-7" in preview.correlation_key
        assert "stub-identifier-7" in preview.suggested_title


def test_correlate_preview_persisted_field_is_literal_false() -> None:
    """Type system pin: persisted is Literal[False], so any future caller
    trying to flip it to True fails at construction time."""
    preview = build_correlation_preview(_bgp_event())
    with pytest.raises(ValidationError):
        TelemetryCorrelationPreview.model_validate(
            {**preview.model_dump(mode="python"), "persisted": True}
        )


# ---------- Phase 18B: correlate/preview endpoint ----------


def test_api_correlate_preview_happy_path_returns_full_preview(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/telemetry/correlate/preview",
        json=_valid_payload(
            event_type="bgp_neighbor_down",
            message="BGP neighbor 10.0.0.21 down",
            severity="critical",
            labels={"neighbor": "10.0.0.21"},
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suggested_incident_type"] == "bgp_neighbor_down"
    assert body["would_create_incident"] is True
    assert body["would_create_event"] is True
    assert body["persisted"] is False  # pinned by Literal[False]
    assert body["confidence"] == 0.9
    # The full telemetry_event echo lets the caller round-trip without
    # losing fidelity (validate + correlate in one call).
    assert body["telemetry_event"]["source"] == "snmp:core-1"


def test_api_correlate_preview_unknown_event_signals_no_incident_creation(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/telemetry/correlate/preview",
        json=_valid_payload(
            event_type="some_unmapped_thing",
            message="vendor proprietary trap",
            severity="info",
            labels={},
            raw={},
        ),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suggested_incident_type"] == "telemetry_observation"
    assert body["would_create_incident"] is False
    assert body["would_create_event"] is True
    assert body["persisted"] is False


def test_api_correlate_preview_422_on_bad_payload(client: TestClient) -> None:
    response = client.post(
        "/api/telemetry/correlate/preview",
        json=_valid_payload(severity="catastrophic"),
    )
    assert response.status_code == 422


def test_api_correlate_preview_writes_nothing_to_the_database(
    client: TestClient, db_session: Session
) -> None:
    """Mirror of the Phase 18A no-persistence row-count assertion for the
    new endpoint. Whole point of correlate/preview is preview-only."""

    def _counts() -> dict[str, int]:
        return {
            "incidents": db_session.scalar(
                select(func.count()).select_from(Incident)
            ),
            "events": db_session.scalar(
                select(func.count()).select_from(IncidentEvent)
            ),
            "evidence": db_session.scalar(
                select(func.count()).select_from(IncidentEvidence)
            ),
            "recommendations": db_session.scalar(
                select(func.count()).select_from(Recommendation)
            ),
            "agent_runs": db_session.scalar(
                select(func.count()).select_from(AgentRun)
            ),
            "agent_steps": db_session.scalar(
                select(func.count()).select_from(AgentStep)
            ),
        }

    before = _counts()
    response = client.post(
        "/api/telemetry/correlate/preview",
        json=_valid_payload(
            event_type="bgp_neighbor_down",
            severity="critical",
            labels={"neighbor": "10.0.0.21"},
        ),
    )
    assert response.status_code == 200
    after = _counts()
    assert before == after, (
        f"correlate/preview must not persist anything; counts diverged: "
        f"before={before} after={after}"
    )


# ---------- safety: no network / execution library imports ----------


# Forbidden set spans:
# - real-execution libs (subprocess, paramiko, netmiko, napalm, scrapli,
#   pexpect, ansible_runner, fabric)
# - SNMP libs (pysnmp, easysnmp, netsnmp)
# - raw network primitives (socket)
# - async I/O server primitives (asyncio - flagged at the module level
#   to keep Phase 18A code free of background-loop temptation; a future
#   phase that genuinely needs asyncio can revisit this list).
_FORBIDDEN_LIBS = frozenset(
    {
        "subprocess",
        "ansible_runner",
        "netmiko",
        "napalm",
        "paramiko",
        "pexpect",
        "fabric",
        "scrapli",
        "pysnmp",
        "easysnmp",
        "netsnmp",
        "socket",
        "asyncio",
    }
)


def _root_module(name: str) -> str:
    return name.split(".", 1)[0]


def _scan_files_for_forbidden_imports(files: list[Path]) -> list[str]:
    offenders: list[str] = []
    for py_file in files:
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _root_module(alias.name) in _FORBIDDEN_LIBS:
                        offenders.append(
                            f"{py_file.name}:{node.lineno}: import {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                if _root_module(node.module) in _FORBIDDEN_LIBS:
                    offenders.append(
                        f"{py_file.name}:{node.lineno}: "
                        f"from {node.module} import ..."
                    )
    return offenders


def test_telemetry_package_blocks_network_and_execution_imports() -> None:
    """Phase 18A is adapter-interface-only. Real SNMP / syslog / socket
    code lands in a later phase under separate review, and MUST live in
    its own module - never inside `app/telemetry/` itself.

    This test parses every file under `app/telemetry/` AND
    `app/api/telemetry.py` with the Python AST and fails the build if any
    `Import` or `ImportFrom` node pulls one of the forbidden roots in."""
    app_root = Path(__file__).resolve().parents[1] / "app"
    files: list[Path] = list((app_root / "telemetry").rglob("*.py"))
    files.append(app_root / "api" / "telemetry.py")
    assert files, "no telemetry files found to scan"

    offenders = _scan_files_for_forbidden_imports(files)
    assert not offenders, (
        "Phase 18A telemetry code must not import network or execution "
        "libraries:\n  " + "\n  ".join(offenders)
    )
