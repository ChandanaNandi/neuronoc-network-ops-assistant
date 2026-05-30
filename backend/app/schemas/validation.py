from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ValidationPreviewRead(BaseModel):
    """Phase 16A: read-only "what would I validate?" view derived from a
    persisted remediation plan.

    Nothing in this payload is executable. `executable` is hard-pinned to
    False and the field set is intentionally a SUBSET of `RemediationPlan`
    (the operationally-relevant check lists) - no `proposed_commands`, no
    `proposed_ansible_playbook`.
    """

    model_config = ConfigDict(extra="forbid")

    recommendation_id: UUID
    incident_id: UUID
    plan_title: str
    plan_risk: str
    validation_source: Literal["remediation_plan"] = "remediation_plan"
    pre_checks: list[str] = Field(default_factory=list)
    post_checks: list[str] = Field(default_factory=list)
    validation_criteria: list[str] = Field(default_factory=list)
    rollback_steps: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    # Permanent contract; no field/payload combination can flip this to True.
    executable: Literal[False] = False
    generated_at: datetime
