# NeuroNOC — Setup Status

Last verified: 2026-05-28

## Host environment

| Item | Value |
|---|---|
| Machine | MacBook M4 Pro, arm64 |
| OS | macOS 26.5 (Darwin 25.5.0) |
| Cores | 12 |
| RAM | 24 GB unified |
| Shell | zsh |
| Homebrew | 5.1.12 @ `/opt/homebrew` |

## Toolchain

| Tool | Version | Notes |
|---|---|---|
| git | 2.50.1 (Apple) | |
| Docker CLI | 28.3.3 | Docker Desktop, aarch64 |
| Docker Engine | 29.1.3 | daemon up |
| Docker Compose plugin | v2.40.3-desktop.1 | |
| Python | 3.12.2 | python.org build |
| uv | 0.7.21 | preferred Python project manager |
| pipx | 1.12.0 | newly installed via brew |
| Node.js | v20.17.0 | via nvm |
| npm | 11.7.0 | |
| corepack | 0.35.0 | upgraded from bundled 0.29.3 |
| pnpm | 10.34.1 | pinned to v10 (pnpm 11+ needs Node ≥ 22.13) |
| Go | 1.25.4 darwin/arm64 | |
| Postgres client | 14.19 (Homebrew) | |
| Postgres server | running on host :5432 | will NOT be used by project |
| Ollama | 0.24.0 | daemon on :11434 |
| kubectl | v1.34.1 | no local cluster |
| Lima | 1.2.1 | available as amd64 fallback |
| Ansible | core 2.20.6 (ansible 13.7.0) | via pipx, Python 3.14.5 venv |

## Ollama models

| Model | Size |
|---|---|
| qwen2.5:14b-instruct | 9.0 GB |
| qwen2.5:7b-instruct | 4.7 GB |
| **Total `~/.ollama/models`** | **17 GB** |

Default routing plan:
- **qwen2.5:7b-instruct** — fast triage, Detector agent
- **qwen2.5:14b-instruct** — Planner, RCA reasoning

## Disk

| Volume | Size | Used | Free |
|---|---|---|---|
| `/System/Volumes/Data` | 460 GB | 391 GB | **42 GB (91% used)** |

Cleanup performed on 2026-05-28:
- `docker image prune -a --force` → reclaimed 5.18 GB (only unused images; pinned by stopped containers were preserved per `prune` semantics).
- `docker builder prune --all --force` → reclaimed 5.97 GB.
- Volumes and Ollama models were **not** touched (rule-protected).
- Net free-space gain on Data volume: **+22 GB** (Docker Desktop's VM disk image compacted beyond the raw bytes pruned).

Docker disk before vs after cleanup:

| Type | Before (Total / Size / Reclaimable) | After (Total / Size / Reclaimable) |
|---|---|---|
| Images | 33 / 27.87 GB / 23.72 GB (85%) | 4 / 5.14 GB / 54 MB (1%) |
| Containers | 9 / 54 MB / 54 MB | 9 / 54 MB / 54 MB (unchanged) |
| Local volumes | 14 / 1.55 GB / 1.43 GB | 14 / 1.55 GB / 1.43 GB (unchanged, preserved) |
| Build cache | 59 / 2.53 GB | 0 / 0 B |

Docker sanity check after cleanup: `docker run --rm hello-world` pulled and ran successfully — engine fully functional.

## Port plan

| Port | Use | State at audit |
|---|---|---|
| 3000 | reserved (future frontend / Next.js) | free |
| 5173 | Vite dev server (frontend) | free |
| 8000 | FastAPI orchestrator | free |
| 5432 | host Postgres 14 (not used by project) | in use by host pg |
| **5433** | **project Postgres (Docker), maps to container :5432** | free |
| 11434 | Ollama (host) | in use by Ollama |

## Architecture decisions (locked in)

- Postgres runs in **Docker**, exposed on host **5433 → container 5432**.
- Host Postgres 14 stays as-is; project never touches it.
- Ollama runs on the **host** (Metal GPU acceleration). Containers reach it via `host.docker.internal:11434`.
- No Terraform, pgvector, containerlab, helm, or kind installed yet — deferred.

## Risks / Apple Silicon notes

- arm64-only image gaps for some network tools (FRR <9, older Cisco/Juniper community images) — use `--platform linux/amd64` (Rosetta) or fall back to Lima.
- Unified-memory pressure with `qwen2.5:14b` + Docker Desktop + dev servers on 24 GB — keep one heavy LLM call at a time.
- Disk at 96% — clean up Docker before Phase 1.
- pnpm pinned to v10 because v11+ requires Node ≥ 22.13. Upgrading Node to 22 LTS would unlock the latest pnpm; not required.

## Install errors encountered (resolved)

1. `corepack prepare pnpm@latest` failed with `Cannot find matching keyid` on bundled corepack 0.29.3 (stale signature key). **Fix:** `npm install -g corepack@latest` → 0.35.0.
2. After upgrade, pnpm@latest (11.4.0) failed with `ERR_UNKNOWN_BUILTIN_MODULE node:sqlite` because it requires Node ≥ 22.13. **Fix:** `corepack prepare pnpm@10 --activate` → pnpm 10.34.1.

## Readiness verdict

✅ **Ready for Phase 1 repo scaffolding.** Disk cleanup complete (42 GB free, meets the 40 GB target).

Next phase will: scaffold the repo skeleton (backend + frontend dirs, `pyproject.toml` via uv, `package.json` via pnpm, `docker-compose.yml` with Postgres on 5433, `.env.example`, base FastAPI app stub) — to be initiated only when you give the go-ahead.
