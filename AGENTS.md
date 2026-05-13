## Learned User Preferences

**Code quality & process**
- Fix root causes for all users; no case-by-case patches, retries, or workarounds; revert speculative changes promptly
- All config via env vars with sensible defaults; never hardcode IPs or machine-specific values
- Repo code, docs, and comments in English; extracted CEO Brain knowledge preserved in the user's original language
- Batch commits by logical grouping; commit and push only when explicitly asked; release only via git tags + CI

**Architecture & design**
- Plan and discuss architecture before coding; deliver plans in current chat mode — don't switch to Plan mode unless asked
- All interfaces (CLI, MCP tools, plugins) must expose the same capabilities as the HTTP API; never access chDB directly
- Use `asyncio.to_thread()` for all blocking calls (chDB, embedding, LLM) inside the async server
- Prefer event-driven hooks over periodic cron; hooks source code in project tree (`cursor-hooks/`), not `.cursor/`
- Distinguish project-scoped facts from global principles; use project_id + score boosting to prevent cross-project knowledge pollution
- AGENTS.md bullets parsed directly as principles (not re-extracted through LLM); CLAUDE.md goes through LLM extraction

**Workflow**
- Deploy changes AND verify end-to-end yourself; don't tell the user to verify
- Test with representative cases before bulk operations; import data newest-first; don't proactively scan beyond what's specified
- Coordinate parallel sessions: if another session implements a feature, revert speculative changes and wait for merge

## Learned Workspace Facts

- ClickMem: local memory system / belief-revision store for AI coding agents; explicit-only, **no LLM in the server loop**, no auto-extraction
- Single port 9527 serves REST (`/v1/*`), MCP SSE (`/sse`), and the dashboard (`/dashboard`)
- Two storage backends, switchable via `CLICKMEM_BACKEND`: `local` (embedded chDB, default) or `clickhouse` (ClickHouse Cloud or self-hosted via `clickhouse-connect`); all DDL flows through `src/clickmem/schema.py`
- Single entity `Memory` with `kind ∈ {principle, decision, fact, doc, free}` and `status ∈ {active, contracted, conflicted}`; partitioned by `project_id` + `privacy ∈ {public, private, confidential}`
- Conflict surfacing: on Expand/Revise, embeddings at cosine ≥ `CLICKMEM_CONFLICT_THRESHOLD` (default 0.92) inside the same `(project_id, kind)` flag both rows `conflicted`; pinned memories short-circuit (non-pinned conflicting commits are rejected outright)
- Five first-class operations with CLI ↔ MCP parity: **Expand** (`remember`), **Revise** (`edit`), **Contract** (`forget`), **Reinforce** (`pin`), **Refuse** (`blacklist`)
- Embedding-only retrieval: Qwen3-Embedding-0.6B at 256d on **CPU** (never MPS / GPU); no `mlx-lm`, no `litellm`, no `[llm]` extra
- Recall scoring: same-project ×1.0, global (`project_id=''`) ×0.9, other-project ×0.0; privacy filter (`confidential` excluded unless `privacy_ack=true`); pinned boost; `recall_trace` exposes the per-candidate breakdown
- Hooks are slim: Cursor stop hook fires-and-forgets `POST /v1/raw` (<50 ms); Claude Code `hooks.json` has only `SessionStart` (HTTP recall) and `Stop` (HTTP raw landing), both <100 ms — never an LLM call inline
- Adapters live in `src/clickmem/adapters/` behind one `AgentAdapter` protocol; built-ins: Claude Code, Cursor, Codex, Aider, Continue.dev, Cline, Windsurf, Zed, JetBrains AI, generic
- Config env vars: `CLICKMEM_SERVER_HOST` / `CLICKMEM_SERVER_PORT` (9527), `CLICKMEM_REMOTE`, `CLICKMEM_API_KEY`, `CLICKMEM_BACKEND`, `CLICKMEM_DB_PATH`, `CLICKMEM_CH_URL` / `CH_USER` / `CH_PASSWORD` / `CH_DATABASE`, `CLICKMEM_CONFLICT_THRESHOLD`, `CLICKMEM_EMBEDDING_MODEL`, `CLICKMEM_LOG_LEVEL`
- Dashboard at `/dashboard` is the primary user-management surface (React + Vite + Tailwind + Recharts, bundled into the wheel via `tool.hatch.build.targets.wheel.force-include` of `src/clickmem/dashboard/dist`)
- Tests use an in-memory chDB backend + `MockEmbeddingEngine` via `tests/conftest.py`; unit + httpx integration + MCP smoke layers
- Package layout: `src/clickmem/` (Python), `src/clickmem/dashboard/` (TypeScript SPA), `cursor-hooks/`, `claude-hooks/`, `skills/`
- PyPI `clickmem` 1.0.0 is the first release on the rebuilt code; CI on Python 3.10/3.12/3.13; release via `v*` tags + PyPI Trusted Publisher
