"""Phase 18A normalized telemetry event model.

Bounded payload and label dicts keep validation cheap and stop a
misbehaving upstream from blowing memory. No database side effect.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# Bounds picked to fit the kind of payload an SNMP trap / syslog frame
# realistically carries. A future phase can raise these if there's a
# concrete need, but raising them silently would mask broken upstream
# parsing - keep the limits surfaced.
RAW_PAYLOAD_MAX_KEYS = 64
RAW_PAYLOAD_MAX_VALUE_LEN = 4096
LABELS_MAX_ENTRIES = 32
LABEL_KEY_MAX_LEN = 64
LABEL_VALUE_MAX_LEN = 256


class CollectorType(str, Enum):
    """Where the event was produced. `manual` covers the validate API and
    operator-typed events; SNMP/syslog will land via Phase 18B adapters."""

    snmp = "snmp"
    syslog = "syslog"
    manual = "manual"


class TelemetrySeverity(str, Enum):
    """Five-level severity. Maps roughly onto syslog severities collapsed
    to a useful subset; we don't need the full 0-7 ladder yet."""

    info = "info"
    notice = "notice"
    warning = "warning"
    error = "error"
    critical = "critical"


class TelemetryEvent(BaseModel):
    """One normalized event ready for downstream correlation.

    Construction does NOT persist anything; this is a transport DTO. The
    Phase 18A validate endpoint just round-trips a payload through this
    model and returns it.
    """

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=256)
    collector_type: CollectorType
    # At least one of {hostname, mgmt_ip, device_hint} SHOULD be present in
    # practice, but Phase 18A doesn't enforce that yet - real adapters
    # may only know one of them at parse time, and the correlator (a
    # later phase) is the right place to mandate identity.
    hostname: str | None = Field(default=None, max_length=255)
    mgmt_ip: str | None = Field(default=None, max_length=64)  # IPv6 + zone
    device_hint: str | None = Field(default=None, max_length=255)
    observed_at: datetime
    event_type: str = Field(min_length=1, max_length=64)
    severity: TelemetrySeverity
    message: str = Field(min_length=1, max_length=4096)
    labels: dict[str, str] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("raw")
    @classmethod
    def _bound_raw(cls, v: dict[str, Any]) -> dict[str, Any]:
        if len(v) > RAW_PAYLOAD_MAX_KEYS:
            raise ValueError(
                f"raw payload has {len(v)} keys, "
                f"max is {RAW_PAYLOAD_MAX_KEYS}"
            )
        for k, val in v.items():
            if not isinstance(k, str):
                raise ValueError(
                    f"raw payload keys must be strings, got {type(k).__name__}"
                )
            if isinstance(val, str) and len(val) > RAW_PAYLOAD_MAX_VALUE_LEN:
                raise ValueError(
                    f"raw[{k!r}] is {len(val)} chars, "
                    f"max is {RAW_PAYLOAD_MAX_VALUE_LEN}"
                )
        return v

    @field_validator("labels")
    @classmethod
    def _bound_labels(cls, v: dict[str, str]) -> dict[str, str]:
        if len(v) > LABELS_MAX_ENTRIES:
            raise ValueError(
                f"labels dict has {len(v)} entries, "
                f"max is {LABELS_MAX_ENTRIES}"
            )
        for k, val in v.items():
            if len(k) > LABEL_KEY_MAX_LEN:
                raise ValueError(
                    f"label key {k!r} exceeds {LABEL_KEY_MAX_LEN}-char limit"
                )
            if len(val) > LABEL_VALUE_MAX_LEN:
                raise ValueError(
                    f"label value for {k!r} exceeds "
                    f"{LABEL_VALUE_MAX_LEN}-char limit"
                )
        return v
