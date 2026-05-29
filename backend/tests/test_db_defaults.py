"""Verify that database-level defaults work even when SQLAlchemy's Python-side
defaults are bypassed (e.g. raw SQL inserts from scripts, future Phase-3
collector pipelines, or psql-driven ops).

These tests issue raw SQL through `text()` and omit the columns that should
have server-side defaults: id, incidents.status, recommendations.requires_approval.
"""

from sqlalchemy import text
from sqlalchemy.orm import Session


def test_incident_db_defaults_via_raw_sql(db_session: Session) -> None:
    row = db_session.execute(
        text(
            "INSERT INTO incidents (title, severity, incident_type) "
            "VALUES (:title, :severity, :incident_type) "
            "RETURNING id, status"
        ),
        {
            "title": "raw SQL default test",
            "severity": "low",
            "incident_type": "probe",
        },
    ).one()

    assert row.id is not None, "expected DB to generate UUID for incidents.id"
    assert row.status == "open", "expected DB-side default 'open' for incidents.status"


def test_recommendation_db_defaults_via_raw_sql(db_session: Session) -> None:
    incident_id = db_session.execute(
        text(
            "INSERT INTO incidents (title, severity, incident_type) "
            "VALUES ('parent for rec', 'low', 'probe') "
            "RETURNING id"
        )
    ).scalar_one()
    assert incident_id is not None

    rec = db_session.execute(
        text(
            "INSERT INTO recommendations "
            "(incident_id, recommendation_type, title, details, risk) "
            "VALUES (:incident_id, 'manual', 'check it', 'just look', 'low') "
            "RETURNING id, requires_approval"
        ),
        {"incident_id": incident_id},
    ).one()

    assert rec.id is not None, "expected DB to generate UUID for recommendations.id"
    assert rec.requires_approval is True, (
        "expected DB-side default true for recommendations.requires_approval"
    )


def test_device_db_default_uuid_via_raw_sql(db_session: Session) -> None:
    row = db_session.execute(
        text(
            "INSERT INTO devices (hostname) VALUES (:hostname) RETURNING id"
        ),
        {"hostname": "raw-sql-device-1"},
    ).one()

    assert row.id is not None, "expected DB to generate UUID for devices.id"
