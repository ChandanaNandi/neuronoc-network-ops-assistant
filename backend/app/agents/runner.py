"""Runner for the Phase 5 incident-analysis workflow.

Creates an `AgentRun` row, executes the LangGraph workflow, and either marks
the run completed (with the final report as `output_payload`) or failed (with
the error captured). Steps are persisted by the workflow nodes themselves.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.agents.workflow import build_workflow
from app.anomaly.engine import IncidentNotFoundError
from app.db.models import AgentRun, Incident
from app.db.session import SessionLocal

WORKFLOW_NAME = "incident_analysis_v1"


def run_incident_analysis(db: Session, incident_id: UUID) -> AgentRun:
    """Run the analysis workflow against an incident and return the persisted run.

    Raises `IncidentNotFoundError` before touching the database if the incident
    does not exist - the caller is expected to translate that to a 404.
    """
    if db.get(Incident, incident_id) is None:
        raise IncidentNotFoundError(f"incident {incident_id} not found")

    run = AgentRun(
        incident_id=incident_id,
        workflow_name=WORKFLOW_NAME,
        status="running",
        input_payload={"incident_id": str(incident_id)},
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    workflow = build_workflow()
    try:
        final_state = workflow.invoke(
            {"incident_id": incident_id},
            config={"configurable": {"db": db, "run_id": run.id}},
        )
    except Exception as exc:  # noqa: BLE001 - we want to surface any failure
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(run)
        raise

    run.status = "completed"
    run.output_payload = final_state.get("final_report")
    run.completed_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return run


# ---------- CLI ----------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.agents.runner",
        description=(
            "Run the Phase 5 deterministic LangGraph incident-analysis "
            "workflow against an incident and print the final report as JSON."
        ),
    )
    parser.add_argument(
        "--incident-id",
        type=UUID,
        required=True,
        help="Incident UUID to analyze.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        try:
            run = run_incident_analysis(db, args.incident_id)
        except IncidentNotFoundError as exc:
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
            return 1

    print(json.dumps(run.output_payload, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
