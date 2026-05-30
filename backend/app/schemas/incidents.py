from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import (
    ApprovalStatus,
    IncidentSeverity,
    IncidentStatus,
    RecommendationRisk,
)


class DeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    hostname: str
    role: str | None = None
    management_ip: str | None = None
    vendor: str | None = None
    platform: str | None = None
    created_at: datetime
    updated_at: datetime


class IncidentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    severity: IncidentSeverity
    incident_type: str = Field(min_length=1, max_length=64)
    status: IncidentStatus = IncidentStatus.open
    summary: str | None = None
    root_cause: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class IncidentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    status: IncidentStatus
    severity: IncidentSeverity
    incident_type: str
    summary: str | None = None
    root_cause: str | None = None
    confidence: float | None = None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None


class IncidentEventCreate(BaseModel):
    event_type: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1)
    payload: dict | None = None


class IncidentEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    incident_id: UUID
    event_type: str
    source: str
    message: str
    payload: dict | None = None
    created_at: datetime


class IncidentEvidenceCreate(BaseModel):
    evidence_type: str = Field(min_length=1, max_length=64)
    source: str = Field(min_length=1, max_length=128)
    content: str = Field(min_length=1)
    payload: dict | None = None


class IncidentEvidenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    incident_id: UUID
    evidence_type: str
    source: str
    content: str
    payload: dict | None = None
    created_at: datetime


class RecommendationCreate(BaseModel):
    recommendation_type: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    details: str = Field(min_length=1)
    risk: RecommendationRisk
    requires_approval: bool = True


class RecommendationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    incident_id: UUID
    recommendation_type: str
    title: str
    details: str
    risk: RecommendationRisk
    requires_approval: bool
    created_at: datetime
    approval_status: ApprovalStatus = ApprovalStatus.pending
    approved_by: str | None = None
    approved_by_operator_id: UUID | None = None
    approved_at: datetime | None = None
    approval_note: str | None = None


class ApprovalRequest(BaseModel):
    """Phase 23 approval payload — body carries `note` only.

    The approving operator's identity comes from the authenticated
    bearer-token session (`get_current_operator`), NOT from a
    submitted field. Any stale caller sending `operator_id` or
    `operator_name` will fail validation here because `extra="forbid"`
    is now in effect — that's a deliberate transition signal.
    """

    model_config = ConfigDict(extra="forbid")

    note: str | None = None
