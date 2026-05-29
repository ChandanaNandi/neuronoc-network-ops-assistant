from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.lab.collector import LabBgpCollectionSummary, collect_lab_bgp_snapshot

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
