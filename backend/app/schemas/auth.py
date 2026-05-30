"""Phase 23 auth schemas."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.operators import OperatorRead


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    """Returned by `POST /api/auth/login` on success.

    `token` is the opaque bearer token to send back in subsequent
    requests as `Authorization: Bearer <token>`. `operator` is the
    same shape `GET /api/auth/me` returns so callers don't need a
    second round-trip to learn who they just logged in as.
    """

    model_config = ConfigDict(extra="forbid")

    token: str
    operator: OperatorRead
