"""Phase 6 RCA explainer.

Wraps the Phase 5 deterministic incident analysis with an OPTIONAL local-LLM
explanation layer (via Ollama) plus a small bundled runbook knowledge base.

Hard rules in this module:
- The LLM is told to use ONLY the provided evidence + runbook snippets.
- If Ollama is unreachable / misbehaves, we DO NOT fail by default; we return a
  deterministic fallback explanation built from the Phase 5 report.
- Callers that need the LLM (`require_llm=True`) get a clean exception they can
  translate to HTTP 503.
- This module never executes a remediation action.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.agents.runner import run_incident_analysis
from app.anomaly.engine import IncidentNotFoundError
from app.core.config import settings
from app.db.models import AgentRun, Incident
from app.db.session import SessionLocal
from app.knowledge.retriever import RetrievedRunbook, retrieve_runbooks
from app.llm.ollama import OllamaUnavailableError, generate_ollama_json

UNSAFE_DEFAULT = (
    "Do not execute remediation actions automatically; every change requires "
    "explicit human approval and a documented rollback plan."
)


class RCAExplanation(BaseModel):
    model_config = ConfigDict(extra="ignore")

    incident_id: UUID
    summary: str
    likely_root_cause: str
    supporting_evidence: list[str] = Field(default_factory=list)
    runbook_references: list[str] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)
    unsafe_actions: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    model: str | None = None
    llm_available: bool


# ---------- prompt ----------

_PROMPT_TEMPLATE = """\
You are NeuroNOC's RCA assistant. You will receive a deterministic incident
analysis (already computed by a rule engine) and a small set of operator
runbook snippets.

Rules you MUST follow:
- Use ONLY the provided evidence and runbook snippets. Do not invent commands,
  hostnames, interfaces, prefixes, vendors, or AS numbers that are not present
  in the inputs.
- Do not propose actions that would execute remediation automatically.
- Anything that touches a production device MUST be listed in
  recommended_next_steps with the precondition that human approval is required,
  OR in unsafe_actions if it is risky.

Output STRICT JSON matching exactly this schema (no markdown fences, no prose,
no comments inside the JSON):

{{
  "summary": string,
  "likely_root_cause": string,
  "supporting_evidence": [string, ...],
  "runbook_references": [string, ...],
  "recommended_next_steps": [string, ...],
  "unsafe_actions": [string, ...],
  "confidence": float between 0.0 and 1.0
}}

Each entry in "runbook_references" must be a title or id from the RUNBOOK
SNIPPETS list below; do not invent runbook names.

# DETERMINISTIC INCIDENT ANALYSIS
{report_block}

# RUNBOOK SNIPPETS
{runbook_block}

Return JSON only.
"""


def build_rca_prompt(
    report: dict[str, Any], runbooks: list[RetrievedRunbook]
) -> str:
    """Build the prompt sent to Ollama. Includes the constraint that the model
    must use only provided evidence and runbook snippets."""
    report_block = json.dumps(report, indent=2, sort_keys=True)
    if runbooks:
        runbook_block = "\n\n".join(
            f"## {rb.title} (id: {rb.name}, score: {rb.score})\n{rb.snippet}"
            for rb in runbooks
        )
    else:
        runbook_block = "(no runbook snippets matched)"
    return _PROMPT_TEMPLATE.format(
        report_block=report_block, runbook_block=runbook_block
    )


# ---------- helpers ----------


def _load_or_run_report(db: Session, incident_id: UUID) -> dict[str, Any]:
    """Return the most recent completed AgentRun's output_payload, running the
    Phase 5 workflow first if no completed run exists yet."""
    latest = db.scalar(
        select(AgentRun)
        .where(AgentRun.incident_id == incident_id)
        .where(AgentRun.status == "completed")
        .order_by(desc(AgentRun.created_at))
        .limit(1)
    )
    if latest is not None and latest.output_payload:
        return dict(latest.output_payload)

    # No completed run yet - drive the deterministic workflow synchronously.
    run = run_incident_analysis(db, incident_id)
    if not run.output_payload:
        raise RuntimeError(
            f"AgentRun {run.id} completed without an output_payload; cannot build RCA."
        )
    return dict(run.output_payload)


def _retrieval_query(report: dict[str, Any]) -> list[str]:
    """Build a keyword list for runbook retrieval from the report."""
    tokens: list[str] = []
    if t := report.get("incident_type"):
        tokens.append(str(t))
    for k in report.get("key_findings", []) or []:
        tokens.append(str(k))
    for s in report.get("correlated_signals", []) or []:
        tokens.append(str(s))
    return tokens


def _fallback_explanation(
    incident_id: UUID,
    report: dict[str, Any],
    runbooks: list[RetrievedRunbook],
) -> RCAExplanation:
    """Build an RCAExplanation purely from the Phase 5 report - no LLM."""
    findings = list(report.get("key_findings", []) or [])
    impacts = list(report.get("validation_summary", []) or [])
    signals = list(report.get("correlated_signals", []) or [])

    summary_parts: list[str] = []
    if signals:
        summary_parts.append(f"correlated signals: {', '.join(signals)}")
    if impacts:
        summary_parts.append(f"observed impacts: {', '.join(impacts)}")
    if findings:
        summary_parts.append(f"rule hits: {', '.join(findings)}")
    summary = (
        "Deterministic analysis (LLM unavailable). "
        + ("; ".join(summary_parts) if summary_parts else "no rules triggered.")
    )

    return RCAExplanation(
        incident_id=incident_id,
        summary=summary,
        likely_root_cause=str(
            report.get("suspected_root_cause")
            or "Insufficient signal for a deterministic root cause."
        ),
        supporting_evidence=impacts,
        runbook_references=[rb.title or rb.name for rb in runbooks],
        recommended_next_steps=list(report.get("recommended_next_steps", []) or []),
        unsafe_actions=[UNSAFE_DEFAULT],
        confidence=float(report.get("confidence", 0.0) or 0.0),
        model=None,
        llm_available=False,
    )


# ---------- main entry point ----------


def generate_rca_explanation(
    db: Session,
    incident_id: UUID,
    model: str | None = None,
    require_llm: bool = False,
) -> RCAExplanation:
    """Run the Phase 5 workflow (or reuse its latest output), retrieve runbooks,
    and either consult Ollama or return a deterministic fallback.

    Raises:
        IncidentNotFoundError: if the incident id does not exist (caller -> 404).
        OllamaUnavailableError: only if `require_llm=True` AND Ollama fails.
    """
    if db.get(Incident, incident_id) is None:
        raise IncidentNotFoundError(f"incident {incident_id} not found")

    report = _load_or_run_report(db, incident_id)
    runbooks = retrieve_runbooks(_retrieval_query(report), limit=3)
    prompt = build_rca_prompt(report, runbooks)
    chosen_model = model or settings.OLLAMA_MODEL

    try:
        raw = generate_ollama_json(prompt, model=chosen_model)
    except OllamaUnavailableError:
        if require_llm:
            raise
        return _fallback_explanation(incident_id, report, runbooks)

    # Strip any server-owned keys the model might have echoed back so they
    # cannot collide with the kwargs we set ourselves, and cannot override
    # our authoritative values (incident_id, model, llm_available).
    if isinstance(raw, dict):
        for reserved in ("incident_id", "model", "llm_available"):
            raw.pop(reserved, None)

    try:
        explanation = RCAExplanation(
            incident_id=incident_id,
            llm_available=True,
            model=chosen_model,
            **raw,
        )
    except ValidationError as exc:
        if require_llm:
            raise OllamaUnavailableError(
                f"LLM returned JSON that does not match the RCA schema: {exc}"
            ) from exc
        return _fallback_explanation(incident_id, report, runbooks)

    return explanation


# ---------- CLI ----------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="app.rca.explainer",
        description=(
            "Generate an RCA explanation for an incident. Uses Ollama if "
            "available; otherwise returns a deterministic fallback built from "
            "the Phase 5 incident analysis report."
        ),
    )
    parser.add_argument(
        "--incident-id", type=UUID, required=True, help="Incident UUID."
    )
    parser.add_argument(
        "--model",
        default=None,
        help=f"Ollama model override (default: {settings.OLLAMA_MODEL}).",
    )
    parser.add_argument(
        "--require-llm",
        action="store_true",
        help="Fail with a non-zero exit code if Ollama is unavailable.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    with SessionLocal() as db:
        try:
            explanation = generate_rca_explanation(
                db,
                args.incident_id,
                model=args.model,
                require_llm=args.require_llm,
            )
        except IncidentNotFoundError as exc:
            print(json.dumps({"error": str(exc)}), file=sys.stderr)
            return 1
        except OllamaUnavailableError as exc:
            print(
                json.dumps({"error": f"ollama unavailable: {exc}"}),
                file=sys.stderr,
            )
            return 2

    print(json.dumps(explanation.model_dump(mode="json"), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
