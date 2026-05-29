from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class RemediationPlan(BaseModel):
    """A DRAFT remediation plan for human review.

    Phase 7 NEVER executes anything. `requires_approval` is hard-pinned to True
    on every plan and the persisted Recommendation row inherits that.
    """

    model_config = ConfigDict(extra="forbid")

    incident_id: UUID
    plan_type: str
    title: str
    risk: str  # low | medium | high
    requires_approval: bool = True
    summary: str
    pre_checks: list[str] = Field(default_factory=list)
    proposed_commands: list[str] = Field(default_factory=list)
    proposed_ansible_playbook: str = ""
    post_checks: list[str] = Field(default_factory=list)
    rollback_steps: list[str] = Field(default_factory=list)
    validation_criteria: list[str] = Field(default_factory=list)
    safety_notes: list[str] = Field(default_factory=list)
    source: str = "phase7_template"
    confidence: float = Field(ge=0.0, le=1.0)
