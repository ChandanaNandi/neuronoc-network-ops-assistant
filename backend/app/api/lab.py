from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.lab.collector import (
    LabBgpCollectionSummary,
    LabSnapshotSummary,
    collect_lab_bgp_snapshot,
    collect_lab_snapshot,
)

router = APIRouter(prefix="/api/lab", tags=["lab"])


@router.post(
    "/collect/bgp",
    response_model=LabBgpCollectionSummary,
    status_code=status.HTTP_201_CREATED,
)
def collect_bgp(db: Session = Depends(get_db)) -> LabBgpCollectionSummary:
    """One-shot BGP snapshot from the Phase 8B FRR Compose lab.

    Synchronous; the caller blocks until the four routers have been scraped
    and a fresh Incident + events have been persisted. There is no background
    daemon and no automatic scheduling - this endpoint must be invoked
    explicitly each time you want a fresh snapshot.
    """
    return collect_lab_bgp_snapshot(db)


@router.post(
    "/collect/snapshot",
    response_model=LabSnapshotSummary,
    status_code=status.HTTP_201_CREATED,
)
def collect_snapshot(db: Session = Depends(get_db)) -> LabSnapshotSummary:
    """Phase 21A one-shot full lab snapshot: BGP + interfaces + running-config.

    Creates ONE Incident (incident_type `lab_full_snapshot`, tagged
    `[lab-collector]`) carrying BGP events, per-interface status events,
    and per-router running-config evidence. Same read-only / no-execution
    guarantees as `/collect/bgp` - `_assert_known_router` +
    `_assert_show_command` in the collector enforce that at runtime.
    """
    return collect_lab_snapshot(db)
