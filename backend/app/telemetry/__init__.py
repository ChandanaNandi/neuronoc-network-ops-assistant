"""Phase 18A telemetry skeleton.

Interface-only - this package defines normalized telemetry models and
adapter Protocol contracts. It deliberately does NOT:

- open a socket
- import a real SNMP / syslog library (pysnmp, easysnmp, netsnmp, etc.)
- import a remote-execution library (paramiko, netmiko, napalm, scrapli,
  pexpect, ansible_runner, fabric, subprocess)
- run a daemon / scheduler / background loop
- persist anything to the database
- block on I/O

A parallel AST safety scan in `tests/test_telemetry.py` fails the build
if any module under `app/telemetry/` or `app/api/telemetry.py` ever pulls
one of those imports in. Phase 18B+ will plug real adapters into these
interfaces behind a separate review.
"""

from app.telemetry.adapters import SNMPAdapter, SyslogAdapter
from app.telemetry.correlator import (
    TelemetryCorrelationPreview,
    build_correlation_preview,
)
from app.telemetry.events import (
    CollectorType,
    TelemetryEvent,
    TelemetrySeverity,
)
from app.telemetry.normalizer import normalize_manual_event

__all__ = [
    "CollectorType",
    "SNMPAdapter",
    "SyslogAdapter",
    "TelemetryCorrelationPreview",
    "TelemetryEvent",
    "TelemetrySeverity",
    "build_correlation_preview",
    "normalize_manual_event",
]
