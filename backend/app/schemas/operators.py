from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.db.models import OperatorRole


class OperatorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    display_name: str
    role: OperatorRole
    created_at: datetime


class OperatorCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=128)
    role: OperatorRole = OperatorRole.operator
