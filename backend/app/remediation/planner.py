"""Phase 7 remediation planner.

Plan-only. The planner generates DRAFT plans for human review and never
executes commands, opens a connection to a device, or pulls in any
remote-execution tooling. A separate test in the suite enforces this
constraint at the import level for every module under this package.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.agents.runner import run_incident_analysis
from app.anomaly.engine import IncidentNotFoundError
from app.db.models import (
    AgentRun,
    ApprovalStatus,
    Incident,
    Operator,
    Recommendation,
)
from app.db.session import SessionLocal
from app.llm.ollama import OllamaUnavailableError
from app.rca.explainer import generate_rca_explanation
from app.remediation.templates import pick_template
from app.schemas.remediation import RemediationPlan


REMEDIATION_PLAN_TYPE = "remediation_plan"


class RecommendationNotFoundError(Exception):
    """Raised when an approve/reject targets an unknown recommendation id."""


class WrongRecommendationTypeError(Exception):
    """Raised when approve/reject targets a recommendation whose
    `recommendation_type` is not `remediation_plan`. The approval workflow
    is scoped to remediation plans only; other recommendation kinds are
    informational and have no approval state."""


class OperatorNotFoundError(Exception):
    """Raised when approve/reject is given an operator_id that does not
    match a row in the operators table."""


def _latest_completed_report(db: Session, incident_id: UUID) -> dict | None:
    latest = db.scalar(
        select(AgentRun)
        .where(AgentRun.incident_id == incident_id)
        .where(AgentRun.status == "completed")
        .order_by(desc(AgentRun.created_at))
        .limit(1)
    )
    if latest is None or not latest.output_payload:
        return None
    return dict(latest.output_payload)


def build_remediation_plan(db: Session, incident_id: UUID) -> RemediationPlan:
    """Pick the appropriate template and fill it with incident context.

    Raises:
        IncidentNotFoundError: if the incident does not exist.
    """
    if db.get(Incident, incident_id) is None:
        raise IncidentNotFoundError(f"incident {incident_id} not found")

    report = _latest_completed_report(db, incident_id)
    if report is None:
        run = run_incident_analysis(db, incident_id)
        report = dict(run.output_payload or {})

    # RCA explanation is best-effort; never required to produce a plan.
    rca = None
    try:
        rca = generate_rca_explanation(db, incident_id, require_llm=False)
    except OllamaUnavailableError:
        rca = None
    except Exception:  # noqa: BLE001 - any failure here must not block planning
        rca = None

    template_fn = pick_template(
        report.get("incident_type"), report.get("correlated_signals", []) or []
    )
    plan = template_fn(incident_id, report, rca)
    # Defense-in-depth: plans MUST require approval; the field is also set in
    # the templates but we re-assert here so a future refactor cannot leak a
    # plan that bypasses human approval.
    if not plan.requires_approval:
        plan = plan.model_copy(update={"requires_approval": True})
    return plan


def _render_details(plan: RemediationPlan) -> str:
    """Build a human-readable + machine-parsable details blob for storage in
    Recommendation.details (text). Keeps the full structured plan accessible
    while remaining readable in a `psql` dump."""
    return (
        f"DRAFT remediation plan ({plan.plan_type}, risk={plan.risk}). "
        f"REQUIRES HUMAN APPROVAL. {plan.summary}\n\n"
        f"```json\n{json.dumps(plan.model_dump(mode='json'), indent=2)}\n```"
    )


def persist_remediation_recommendation(
    db: Session, plan: RemediationPlan
) -> Recommendation:
    """Persist the plan as a Recommendation row tagged `remediation_plan`.

    `requires_approval` is hard-pinned to True regardless of the input plan.
    New rows default to `approval_status="pending"` via the server default.
    """
    rec = Recommendation(
        incident_id=plan.incident_id,
        recommendation_type=REMEDIATION_PLAN_TYPE,
        title=plan.title,
        details=_render_details(plan),
        risk=plan.risk,
        requires_approval=True,
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


def set_recommendation_approval(
    db: Session,
    recommendation_id: UUID,
    status: ApprovalStatus,
    operator_name: str | None = None,
    operator_id: UUID | None = None,
    note: str | None = None,
) -> Recommendation:
    """Phase 10A/13A approval-stub helper.

    Records intent ONLY. Never executes a command, never connects to a device,
    never imports an execution library (a safety test scans this package for
    such imports).

    Exactly one of `operator_id` or `operator_name` must be supplied.
    - `operator_id` resolves an Operator row; `approved_by` is set to that
      operator's display_name and `approved_by_operator_id` is recorded.
    - `operator_name` (legacy, Phase 10A) is persisted verbatim with no FK.

    Idempotent same-state calls are allowed - they update operator/at/note
    so the latest decision is recorded.

    Raises:
        RecommendationNotFoundError: unknown recommendation id (-> 404).
        WrongRecommendationTypeError: type != "remediation_plan" (-> 400).
        OperatorNotFoundError: unknown operator id (-> 404).
        ValueError: not exactly one of operator_id / operator_name provided.
    """
    has_id = operator_id is not None
    has_name = operator_name is not None
    if not has_id and not has_name:
        raise ValueError(
            "exactly one of operator_id or operator_name must be provided "
            "(neither supplied)"
        )
    if has_id and has_name:
        raise ValueError(
            "exactly one of operator_id or operator_name must be provided "
            "(both supplied)"
        )

    rec = db.get(Recommendation, recommendation_id)
    if rec is None:
        raise RecommendationNotFoundError(
            f"recommendation {recommendation_id} not found"
        )
    if rec.recommendation_type != REMEDIATION_PLAN_TYPE:
        raise WrongRecommendationTypeError(
            f"recommendation {recommendation_id} has type "
            f"'{rec.recommendation_type}', only '{REMEDIATION_PLAN_TYPE}' "
            "is approvable"
        )

    if operator_id is not None:
        operator = db.get(Operator, operator_id)
        if operator is None:
            raise OperatorNotFoundError(
                f"operator {operator_id} not found"
            )
        rec.approved_by = operator.display_name
        rec.approved_by_operator_id = operator.id
    else:
        # Legacy free-form name; no FK.
        rec.approved_by = operator_name
        rec.approved_by_operator_id = None

    rec.approval_status = status.value
    rec.approved_at = datetime.now(timezone.utc)
    rec.approval_note = note
    db.commit()
    db.refresh(rec)
    return rec


# ---------- CLI ----------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.remediation.planner",
        description=(
            "Generate a DRAFT remediation plan for an incident. Plan-only - "
            "this command NEVER executes anything and NEVER calls Ansible."
        ),
    )
    parser.add_argument(
        "--incident-id", type=UUID, required=True, help="Incident UUID."
    )
    persist_group = parser.add_mutually_exclusive_group()
    persist_group.add_argument(
        "--persist",
        dest="persist",
        action="store_true",
        help="Persist the plan as a Recommendation row (default).",
    )
    persist_group.add_argument(
        "--no-persist",
        dest="persist",
        action="store_false",
        help="Print the plan but do not persist a Recommendation row.",
    )
    parser.set_defaults(persist=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        try:
            plan = build_remediation_plan(db, args.incident_id)
        except IncidentNotFoundError as exc:
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
            return 1
        if args.persist:
            persist_remediation_recommendation(db, plan)

    print(json.dumps(plan.model_dump(mode="json"), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
