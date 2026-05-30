"""Phase 5 LangGraph workflow: deterministic incident analysis.

There are NO LLMs in this graph. Every node is a pure-Python function that
computes its output from the supplied state (and the database session passed
through config). Templates are static strings - no text generation.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.state import WorkflowState
from app.anomaly.engine import IncidentNotFoundError, analyze_incident
from app.db.models import (
    AgentStep,
    Incident,
    IncidentEvent,
    IncidentEvidence,
)
from app.schemas.agents import IncidentAnalysisReport

# Rule-name -> correlation theme.
_THEME_MAP: dict[str, str] = {
    "bgp_neighbor_down_detected": "routing_failure",
    "route_withdrawal_detected": "routing_failure",
    "route_missing_detected": "routing_failure",
    "interface_error_spike_detected": "interface_physical_issue",
    # Phase 21C: lab-only link-down finding routes through the same
    # interface_physical_issue theme so Phase 7's pick_template lands on
    # the existing interface template instead of generic_investigation.
    "link_down_detected": "interface_physical_issue",
    "latency_spike_detected": "latency_or_loss",
    "packet_loss_detected": "latency_or_loss",
    "acl_deny_spike_detected": "policy_block",
}

# Templates for suspected root cause given the dominant theme.
_ROOT_CAUSE_TEMPLATES: dict[str, str] = {
    "routing_failure": (
        "Likely a session or policy disruption on the upstream router preventing "
        "route advertisement or session keepalives."
    ),
    "interface_physical_issue": (
        "Likely a physical-layer fault (optics, fiber, or patch panel) on the "
        "affected interface."
    ),
    "policy_block": (
        "Likely a recent ACL or routing-policy change blocking previously-"
        "permitted traffic."
    ),
    "latency_or_loss": (
        "Likely path congestion or queue drops on an intermediate hop."
    ),
    "unknown": (
        "Insufficient signal to suggest a root cause; deeper investigation required."
    ),
}

_LOSS_METRICS = {"packet_loss_percent", "loss_pct", "loss_percent"}
_LATENCY_METRICS = {"latency_ms", "rtt_ms"}


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _payload(obj: IncidentEvent | IncidentEvidence) -> dict[str, Any]:
    return obj.payload or {}


def _persist_step(
    config: RunnableConfig,
    step_name: str,
    output_payload: dict | None = None,
    error: str | None = None,
    status: str = "completed",
) -> None:
    db: Session = config["configurable"]["db"]
    run_id: UUID = config["configurable"]["run_id"]
    step = AgentStep(
        run_id=run_id,
        step_name=step_name,
        status=status,
        output_payload=output_payload,
        error=error,
    )
    db.add(step)
    db.commit()


# ---------- nodes ----------


def load_incident_node(state: WorkflowState, config: RunnableConfig) -> dict:
    db: Session = config["configurable"]["db"]
    incident_id: UUID = state["incident_id"]

    incident = db.get(Incident, incident_id)
    if incident is None:
        raise IncidentNotFoundError(f"incident {incident_id} not found")

    events = list(
        db.scalars(
            select(IncidentEvent).where(IncidentEvent.incident_id == incident_id)
        ).all()
    )
    evidence = list(
        db.scalars(
            select(IncidentEvidence).where(
                IncidentEvidence.incident_id == incident_id
            )
        ).all()
    )

    _persist_step(
        config,
        "load_incident",
        output_payload={
            "incident_id": str(incident_id),
            "incident_type": incident.incident_type,
            "severity": incident.severity,
            "event_count": len(events),
            "evidence_count": len(evidence),
        },
    )
    return {"incident": incident, "events": events, "evidence": evidence}


def anomaly_detection_node(state: WorkflowState, config: RunnableConfig) -> dict:
    db: Session = config["configurable"]["db"]
    incident_id: UUID = state["incident_id"]

    findings = analyze_incident(db, incident_id)
    _persist_step(
        config,
        "anomaly_detection",
        output_payload={
            "finding_count": len(findings),
            "findings": [f.model_dump(mode="json") for f in findings],
        },
    )
    return {"anomaly_findings": findings}


def evidence_summary_node(state: WorkflowState, config: RunnableConfig) -> dict:
    events = state.get("events", [])
    evidence = state.get("evidence", [])

    devices: set[str] = set()
    for source in (*events, *evidence):
        device = _payload(source).get("device")
        if isinstance(device, str) and device:
            devices.add(device)

    summary = {
        "event_count": len(events),
        "evidence_count": len(evidence),
        "event_types": sorted({e.event_type for e in events}),
        "evidence_types": sorted({v.evidence_type for v in evidence}),
        "devices": sorted(devices),
    }
    _persist_step(config, "evidence_summary", output_payload=summary)
    return {"evidence_summary": summary}


def correlation_node(state: WorkflowState, config: RunnableConfig) -> dict:
    findings = state.get("anomaly_findings", [])
    themes = sorted({_THEME_MAP.get(f.rule_name, "unknown") for f in findings})
    if not themes:
        themes = ["unknown"]
    summary = {
        "themes": themes,
        "findings_per_theme": {
            theme: [
                f.rule_name
                for f in findings
                if _THEME_MAP.get(f.rule_name, "unknown") == theme
            ]
            for theme in themes
        },
    }
    _persist_step(config, "correlation", output_payload=summary)
    return {"correlation_summary": summary}


def validation_node(state: WorkflowState, config: RunnableConfig) -> dict:
    events = state.get("events", [])
    evidence = state.get("evidence", [])

    impacts: set[str] = set()
    for event in events:
        p = _payload(event)
        if event.event_type == "reachability_loss":
            impacts.add("reachability_loss")
        if event.event_type == "route_missing":
            impacts.add("route_missing")
        if event.event_type == "traffic_denied":
            impacts.add("acl_deny")

        metric = p.get("metric_name")
        value = _num(p.get("metric_value")) or _num(p.get("after"))
        if metric in _LOSS_METRICS and (value or 0) > 0:
            impacts.add("packet_loss")
        if metric in _LATENCY_METRICS and (value or 0) > 100:
            impacts.add("high_latency")
        if metric == "acl_deny_hits" and (value or 0) > 0:
            impacts.add("acl_deny")

    for ev in evidence:
        p = _payload(ev)
        if p.get("result") == "not_in_table":
            impacts.add("route_missing")

    summary = {"impacts": sorted(impacts)}
    _persist_step(config, "validation", output_payload=summary)
    return {"validation_summary": summary}


def report_node(state: WorkflowState, config: RunnableConfig) -> dict:
    incident: Incident = state["incident"]
    findings = state.get("anomaly_findings", [])
    themes = state.get("correlation_summary", {}).get("themes", ["unknown"])
    impacts = state.get("validation_summary", {}).get("impacts", [])

    dominant_theme = themes[0] if themes else "unknown"
    suspected = _ROOT_CAUSE_TEMPLATES.get(
        dominant_theme, _ROOT_CAUSE_TEMPLATES["unknown"]
    )

    confidence = (
        sum(f.confidence for f in findings) / len(findings) if findings else 0.0
    )
    requires_review = (
        incident.severity in {"high", "critical"}
        or not findings
        or "unknown" in themes
    )

    report = IncidentAnalysisReport(
        incident_id=incident.id,
        incident_type=incident.incident_type,
        severity=incident.severity,
        anomaly_count=len(findings),
        key_findings=[f.rule_name for f in findings],
        correlated_signals=themes,
        suspected_root_cause=suspected,
        validation_summary=impacts,
        recommended_next_steps=[f.recommended_next_step for f in findings],
        requires_human_review=requires_review,
        confidence=round(confidence, 3),
    )
    report_dict = report.model_dump(mode="json")
    _persist_step(config, "report", output_payload=report_dict)
    return {"final_report": report_dict}


# ---------- graph ----------


def build_workflow():
    graph = StateGraph(WorkflowState)
    graph.add_node("load_incident", load_incident_node)
    graph.add_node("anomaly_detection", anomaly_detection_node)
    graph.add_node("evidence_summary", evidence_summary_node)
    graph.add_node("correlation", correlation_node)
    graph.add_node("validation", validation_node)
    graph.add_node("report", report_node)

    graph.add_edge(START, "load_incident")
    graph.add_edge("load_incident", "anomaly_detection")
    graph.add_edge("anomaly_detection", "evidence_summary")
    graph.add_edge("evidence_summary", "correlation")
    graph.add_edge("correlation", "validation")
    graph.add_edge("validation", "report")
    graph.add_edge("report", END)

    return graph.compile()


NODE_NAMES: list[str] = [
    "load_incident",
    "anomaly_detection",
    "evidence_summary",
    "correlation",
    "validation",
    "report",
]
