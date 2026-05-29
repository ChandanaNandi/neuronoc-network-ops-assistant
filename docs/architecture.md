# NeuroNOC architecture (intended)

This document describes the **target** architecture. Phase 1 only implements the scaffold; most components below are placeholders.

## High-level shape

```
┌──────────────────────────────────────────────────────────────────┐
│ Frontend (Vite + React + TS)                          :5173      │
│   Dashboard · Incident Console · Agent Inspector                 │
└───────────────────────────┬──────────────────────────────────────┘
                            │ HTTP / JSON
┌───────────────────────────▼──────────────────────────────────────┐
│ Backend (FastAPI, Python 3.12, uv)                    :8000      │
│ ┌──────────────────────────────────────────────────────────────┐ │
│ │ Agent Supervisor (LangGraph, Phase 5)                        │ │
│ │   ├─ Detector   — flags anomalies from telemetry             │ │
│ │   ├─ RCA        — proposes root-cause hypotheses             │ │
│ │   ├─ Validator  — checks hypotheses (Batfish later)          │ │
│ │   └─ Remediator — drafts playbooks (Ansible --check)         │ │
│ └──────────────────────────────────────────────────────────────┘ │
│ ┌──────────────────────────────────────────────────────────────┐ │
│ │ Anomaly engine (Phase 4) — stats + thresholds → events       │ │
│ │ Collector ingest API (Phase 3) — syslog / SNMP / streaming   │ │
│ │ Incidents data model (Phase 2)                               │ │
│ └──────────────────────────────────────────────────────────────┘ │
└───────────┬─────────────────────────────────────┬────────────────┘
            │ asyncpg / SQLAlchemy                │ HTTP
┌───────────▼──────────────┐         ┌────────────▼────────────────┐
│ Postgres 16 (Docker)     │         │ Ollama (host)               │
│ host :5433 → ctr :5432   │         │ http://localhost:11434      │
│ neuronoc_postgres_data   │         │ qwen2.5:7b / qwen2.5:14b    │
└──────────────────────────┘         └─────────────────────────────┘
                                                   ▲
                                                   │
                                     ┌─────────────┴─────────────┐
                                     │ Network lab (Phase 8)     │
                                     │ Containerlab + FRR/SR Lnx │
                                     └───────────────────────────┘
```

## Service boundaries and ports

| Service | Where | Port | Notes |
|---|---|---|---|
| Frontend dev | Vite | 5173 | strictPort |
| Backend API | host (uv) | 8000 | FastAPI |
| Postgres | Docker | host 5433 → ctr 5432 | host's pg 14 keeps 5432 |
| Ollama | host daemon | 11434 | Metal GPU; containers reach via `host.docker.internal:11434` |

## Why these choices

- **Postgres in Docker, not on host:** project DB lifecycle (reset/upgrade/wipe) should not touch the user's existing host Postgres on 5432.
- **Ollama on host, not containerized:** containerized Ollama on macOS cannot use the Apple Metal GPU and runs ~10–20× slower.
- **uv over poetry/pipenv:** faster resolver, single lockfile, already installed.
- **Human-in-the-loop remediation:** the Remediator agent will produce Ansible/IaC **plans**, never auto-apply. Approval is a hard gate.

## Agent contract (Phase 5+, sketch only)

Each agent will expose:
- `inputs`: typed Pydantic models describing what it needs
- `tools`: a whitelist of callable side-effects (DB read, LLM call, command preview)
- `outputs`: typed result + a `confidence` field + a `rationale` trail
- `audit_log`: every LLM call and tool invocation persisted to Postgres

The supervisor (LangGraph) routes state between agents; no agent calls another directly.

## Out of scope for Phase 1

- Authentication / RBAC
- LangGraph agents
- ML / transformer code
- Containerlab topology
- Production deployment (k8s/helm)
- Observability stack (Prometheus, Grafana)
