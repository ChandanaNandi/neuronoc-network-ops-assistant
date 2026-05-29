"""Phase 4 anomaly engine - runs the deterministic rule set against incidents.

The engine is intentionally read-only: it never writes findings back to the
database. Persisting agent / anomaly runs lands in Phase 5.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.anomaly.rules import RULES, AnomalyFinding
from app.db.models import Incident, IncidentEvent, IncidentEvidence
from app.db.session import SessionLocal


class IncidentNotFoundError(Exception):
    """Raised when analyze_incident is called with an unknown incident id."""


def analyze_incident(db: Session, incident_id: UUID) -> list[AnomalyFinding]:
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
            select(IncidentEvidence).where(IncidentEvidence.incident_id == incident_id)
        ).all()
    )

    findings: list[AnomalyFinding] = []
    for rule in RULES:
        findings.extend(rule(incident, events, evidence))
    return findings


def analyze_open_incidents(
    db: Session, limit: int = 50
) -> list[AnomalyFinding]:
    stmt = (
        select(Incident)
        .where(Incident.status == "open")
        .order_by(Incident.created_at.desc())
        .limit(limit)
    )
    incidents = db.scalars(stmt).all()

    findings: list[AnomalyFinding] = []
    for incident in incidents:
        findings.extend(analyze_incident(db, incident.id))
    return findings


def findings_to_json(findings: list[AnomalyFinding]) -> str:
    return json.dumps([f.model_dump(mode="json") for f in findings], indent=2)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.anomaly.engine",
        description=(
            "Run NeuroNOC's deterministic rule set against incidents and "
            "print structured findings as JSON."
        ),
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--incident-id",
        type=UUID,
        help="Analyze a single incident by id.",
    )
    mode.add_argument(
        "--open",
        action="store_true",
        help="Analyze all currently-open incidents (newest first).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Cap on incidents inspected by --open (default 50, max 100). Ignored with --incident-id.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.limit < 1 or args.limit > 100:
        parser.error("--limit must be between 1 and 100")

    with SessionLocal() as db:
        if args.incident_id:
            try:
                findings = analyze_incident(db, args.incident_id)
            except IncidentNotFoundError as exc:
                print(json.dumps({"error": str(exc)}), file=sys.stderr)
                return 1
        else:
            findings = analyze_open_incidents(db, limit=args.limit)

    print(findings_to_json(findings))
    return 0


if __name__ == "__main__":
    sys.exit(main())
