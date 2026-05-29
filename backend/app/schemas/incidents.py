from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    """Phase 13A approval payload.

    Accepts either:
      - `operator_id` -> resolves an existing Operator row; `approved_by` is
        set to that operator's `display_name` and `approved_by_operator_id`
        is recorded for audit-trail integrity.
      - `operator_name` (legacy, Phase 10A) -> persisted verbatim into
        `approved_by`; no FK is set. Kept for backward compatibility so
        existing CLI / script callers don't break.

    Exactly ONE must be provided. `note` is optional in both cases."""

    operator_id: UUID | None = None
    operator_name: str | None = Field(
        default=None, min_length=1, max_length=128
    )
    note: str | None = None

    @model_validator(mode="after")
    def _require_one_identity_field(self) -> "ApprovalRequest":
        has_id = self.operator_id is not None
        has_name = self.operator_name is not None
        if not has_id and not has_name:
            raise ValueError(
                "approval payload must include exactly one of "
                "operator_id or operator_name"
            )
        if has_id and has_name:
            raise ValueError(
                "approval payload must include exactly one of "
                "operator_id or operator_name (both supplied)"
            )
        return self
