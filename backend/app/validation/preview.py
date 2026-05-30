"""Phase 16A read-only validation preview.

Given a persisted remediation plan (a `Recommendation` row tagged
`recommendation_type='remediation_plan'`), surface ONLY the validation
fields - pre/post checks, validation criteria, rollback steps, safety
notes - in a stable, executable=False schema.

This module is plan-only and read-only. It NEVER:
  - opens a connection to a device
  - executes a command
  - imports a remote-execution library (`subprocess`, `ansible_runner`,
    `netmiko`, `napalm`, `paramiko`, `pexpect`, `fabric`, `scrapli`)

The matching `test_validation_package_blocks_execution_library_imports`
test scans every module under `app/validation/` and the API wrapper
`app/api/validation.py` with the Python AST and fails the build if any
import statement pulls one of those in.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.db.models import Recommendation
from app.remediation.planner import REMEDIATION_PLAN_TYPE
from app.schemas.remediation import RemediationPlan
from app.schemas.validation import ValidationPreviewRead


# `(?s)` so `.` spans newlines; `(?:\r?\n)` tolerates CRLF-normalised dumps.
# Producer writes "```json\n{...}\n```" verbatim - this matches a slightly
# wider set of well-formed fences so the helper is reusable.
_FENCED_JSON_RE = re.compile(
    r"```json\s*(?:\r?\n)(.*?)(?:\r?\n)```",
    re.DOTALL,
)


# ---------- exceptions ----------


class RecommendationNotFoundError(Exception):
    """Raised when the requested recommendation id doesn't exist (-> 404)."""


class NotRemediationPlanError(Exception):
    """Raised when the recommendation's type is not `remediation_plan`
    (-> 400). Validation preview is only meaningful for plans."""


class PlanParseError(Exception):
    """Raised when the fenced JSON block in `Recommendation.details` is
    missing, unparseable, or missing required validation fields (-> 400).
    """


# ---------- helpers ----------


def extract_fenced_json(text: str) -> dict | None:
    """Pull the first ```json ... ``` block out of a string and return the
    parsed dict, or `None` if no well-formed fenced JSON block is present.

    Robust to:
      - missing fence (returns None)
      - non-JSON content inside the fence (returns None)
      - JSON that parses to something other than an object (returns None)

    Does NOT raise; callers can layer a more specific error message on top
    of `None`.
    """
    if not text:
        return None
    match = _FENCED_JSON_RE.search(text)
    if match is None:
        return None
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


# ---------- main entry point ----------


def build_validation_preview(
    db: Session, recommendation_id: UUID
) -> ValidationPreviewRead:
    """Look up a recommendation and project its persisted plan JSON into a
    `ValidationPreviewRead`.

    Raises:
        RecommendationNotFoundError: row missing (-> 404).
        NotRemediationPlanError: row exists but isn't a remediation plan
            (-> 400). Other recommendation kinds have no validation surface.
        PlanParseError: row is a plan but `details` is missing the fenced
            JSON block, the JSON is malformed, or required fields fail
            `RemediationPlan` validation (-> 400).
    """
    rec = db.get(Recommendation, recommendation_id)
    if rec is None:
        raise RecommendationNotFoundError(
            f"recommendation {recommendation_id} not found"
        )

    if rec.recommendation_type != REMEDIATION_PLAN_TYPE:
        raise NotRemediationPlanError(
            f"recommendation {recommendation_id} has type "
            f"'{rec.recommendation_type}', only '{REMEDIATION_PLAN_TYPE}' "
            "has a validation preview"
        )

    data = extract_fenced_json(rec.details or "")
    if data is None:
        raise PlanParseError(
            f"recommendation {recommendation_id} details has no parseable "
            "fenced ```json``` block"
        )

    # Re-validate against RemediationPlan so we get a precise field-level
    # error if the persisted JSON is stale or hand-edited.
    try:
        plan = RemediationPlan.model_validate(data)
    except ValidationError as exc:
        raise PlanParseError(
            f"recommendation {recommendation_id} plan JSON failed schema "
            f"validation: {exc.errors()}"
        ) from exc

    return ValidationPreviewRead(
        recommendation_id=rec.id,
        incident_id=rec.incident_id,
        plan_title=rec.title,
        plan_risk=rec.risk,
        pre_checks=plan.pre_checks,
        post_checks=plan.post_checks,
        validation_criteria=plan.validation_criteria,
        rollback_steps=plan.rollback_steps,
        safety_notes=plan.safety_notes,
        generated_at=datetime.now(timezone.utc),
    )
