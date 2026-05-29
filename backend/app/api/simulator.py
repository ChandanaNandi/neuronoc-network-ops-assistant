from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.simulator.scenarios import ALL_SCENARIO_NAMES, SCENARIOS
from app.simulator.seed import apply_scenario, ensure_devices, reset_simulator_data

router = APIRouter(prefix="/api/simulator", tags=["simulator"])


@router.post("/seed", status_code=status.HTTP_201_CREATED)
def seed(
    scenario: str = Query(default="all"),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    if scenario != "all" and scenario not in SCENARIOS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"unknown scenario '{scenario}'; valid: all, "
                + ", ".join(ALL_SCENARIO_NAMES)
            ),
        )

    new_devices = ensure_devices(db)
    names = ALL_SCENARIO_NAMES if scenario == "all" else [scenario]
    created = [apply_scenario(db, name) for name in names]
    return {
        "new_devices": new_devices,
        "created_incident_ids": [str(incident.id) for incident in created],
    }


@router.post("/reset")
def reset(db: Session = Depends(get_db)) -> dict[str, int]:
    removed = reset_simulator_data(db)
    return {"removed_incidents": removed}
