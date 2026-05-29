from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AgentStepRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    run_id: UUID
    step_name: str
    status: str
    input_payload: dict | None = None
    output_payload: dict | None = None
    error: str | None = None
    created_at: datetime


class AgentRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    incident_id: UUID
    workflow_name: str
    status: str
    input_payload: dict | None = None
    output_payload: dict | None = None
    error: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    steps: list[AgentStepRead] = Field(default_factory=list)


class IncidentAnalysisReport(BaseModel):
    incident_id: UUID
    incident_type: str
    severity: str
    anomaly_count: int
    key_findings: list[str]
    correlated_signals: list[str]
    suspected_root_cause: str
    validation_summary: list[str]
    recommended_next_steps: list[str]
    requires_human_review: bool
    confidence: float = Field(ge=0.0, le=1.0)
