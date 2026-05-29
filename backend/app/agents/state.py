"""LangGraph state model for the incident-analysis workflow.

State is held in memory only - ORM objects flow between nodes. Persistence
happens out-of-band via AgentRun / AgentStep rows written by each node.
"""

from __future__ import annotations

from typing import Any, TypedDict
from uuid import UUID

from app.anomaly.rules import AnomalyFinding
from app.db.models import Incident, IncidentEvent, IncidentEvidence


class WorkflowState(TypedDict, total=False):
    incident_id: UUID
    incident: Incident
    events: list[IncidentEvent]
    evidence: list[IncidentEvidence]
    anomaly_findings: list[AnomalyFinding]
    evidence_summary: dict[str, Any]
    correlation_summary: dict[str, Any]
    validation_summary: dict[str, Any]
    final_report: dict[str, Any]
    errors: list[str]
