"""Simulator seed / reset operations.

Functions in this module take an SQLAlchemy `Session` and commit themselves
so they can be driven from both the CLI (`python -m app.simulator.seed`)
and the FastAPI route (`app/api/simulator.py`) with the same code.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import Device, Incident, IncidentEvent, IncidentEvidence, Recommendation
from app.db.session import SessionLocal
from app.simulator.scenarios import (
    ALL_SCENARIO_NAMES,
    DEVICE_SPECS,
    SCENARIOS,
    SIMULATOR_MARKER,
)


def ensure_devices(db: Session) -> int:
    """Insert simulator devices that are not already present (by hostname).

    Returns the number of newly created devices. Safe to call repeatedly.
    """
    created = 0
    for spec in DEVICE_SPECS:
        existing = db.scalar(select(Device).where(Device.hostname == spec["hostname"]))
        if existing is None:
            db.add(Device(**spec))
            created += 1
    db.commit()
    return created


def apply_scenario(db: Session, scenario_name: str) -> Incident:
    """Create one Incident plus its events/evidence/recommendation."""
    if scenario_name not in SCENARIOS:
        raise KeyError(
            f"unknown scenario '{scenario_name}'; valid: {', '.join(ALL_SCENARIO_NAMES)}"
        )
    spec = SCENARIOS[scenario_name]
    incident = Incident(**spec["incident"])
    db.add(incident)
    db.flush()  # populate incident.id for FK on children

    for event_spec in spec["events"]:
        db.add(IncidentEvent(incident_id=incident.id, **event_spec))
    for evidence_spec in spec["evidence"]:
        db.add(IncidentEvidence(incident_id=incident.id, **evidence_spec))
    db.add(Recommendation(incident_id=incident.id, **spec["recommendation"]))

    db.commit()
    db.refresh(incident)
    return incident


def reset_simulator_data(db: Session) -> int:
    """Delete all simulator-created incidents (cascades to events / evidence /
    recommendations via FK ON DELETE CASCADE). Devices are intentionally kept.

    Returns the number of incidents deleted.
    """
    stmt = (
        delete(Incident)
        .where(Incident.summary.like(f"{SIMULATOR_MARKER}%"))
        .execution_options(synchronize_session=False)
    )
    result = db.execute(stmt)
    db.commit()
    return result.rowcount or 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.simulator.seed",
        description=(
            "Seed the NeuroNOC database with deterministic synthetic incident data. "
            "All simulator-created rows are tagged so --reset removes them without "
            "touching operator-created incidents."
        ),
    )
    parser.add_argument(
        "--scenario",
        help=(
            "Scenario to seed. Use 'all' to seed every scenario, or one of: "
            + ", ".join(ALL_SCENARIO_NAMES)
        ),
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help=(
            "Remove every simulator-created incident (and its events / evidence / "
            "recommendations via cascade). Devices are preserved."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if not args.reset and not args.scenario:
        parser.error("specify --reset, --scenario all, or --scenario <name>")

    if args.scenario and args.scenario != "all" and args.scenario not in SCENARIOS:
        parser.error(
            f"unknown scenario '{args.scenario}'; valid: all, "
            + ", ".join(ALL_SCENARIO_NAMES)
        )

    with SessionLocal() as db:
        if args.reset:
            removed = reset_simulator_data(db)
            print(
                f"[simulator] reset: removed {removed} simulator incident(s) "
                "(children cascaded)."
            )

        if args.scenario:
            new_devices = ensure_devices(db)
            if new_devices:
                print(f"[simulator] seeded {new_devices} new device(s).")
            else:
                print("[simulator] devices already seeded (idempotent).")

            names = ALL_SCENARIO_NAMES if args.scenario == "all" else [args.scenario]
            for name in names:
                inc = apply_scenario(db, name)
                print(f"[simulator] created incident '{name}' id={inc.id}")
            print(f"[simulator] done. {len(names)} incident(s) added.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
