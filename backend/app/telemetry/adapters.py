"""Phase 18A adapter PROTOCOLS only.

These are typing contracts, not implementations. NO module imported here
opens a socket, runs a subprocess, or pulls an SNMP/syslog library. The
package's safety scan in `tests/test_telemetry.py` fails the build if
any future change breaks that.

Implementations land in a later phase, behind a separate review, and
MUST live in their own modules so the import graph for `app/telemetry/*`
stays clean.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.telemetry.events import TelemetryEvent


@runtime_checkable
class SNMPAdapter(Protocol):
    """Read-only SNMP poller contract.

    `poll()` is synchronous and pure: an implementation must build the
    returned list deterministically from its inputs, surface per-host
    errors through the result list (or raise a per-impl typed exception),
    and never write to the database directly. Persistence is the caller's
    job, performed in a later phase against existing schema.
    """

    def poll(
        self,
        host: str,
        community: str | None = None,
        oids: list[str] | None = None,
    ) -> list[TelemetryEvent]: ...


@runtime_checkable
class SyslogAdapter(Protocol):
    """Stateless syslog line parser contract.

    `parse()` accepts ONE already-received syslog frame (string form)
    and either returns a normalized `TelemetryEvent` or `None` if the
    frame is unparseable. The transport (UDP listener, TCP/TLS frontend,
    file tail) is intentionally out of scope here - a later phase will
    own that under its own review.
    """

    def parse(
        self,
        line: str,
        *,
        default_hostname: str | None = None,
    ) -> TelemetryEvent | None: ...
