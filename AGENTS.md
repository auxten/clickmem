## Learned User Preferences

- All config (host, port, paths) must be env-var configurable; never hardcode IPs or machine-specific values
- CLI must use the API server when running; never directly access chDB from CLI
- All repo content (docs, comments, code) in English; no Chinese in the repository
- Prefer event-driven hooks (session boundaries) over periodic cron for maintenance triggers
- Avoid unnecessary complexity; revert speculative changes promptly
- Coordinate parallel sessions: if another session implements a feature, wait for merge before continuing
- Plan and discuss architecture before coding; don't jump to implementation
- Deliver plans in current chat mode; don't switch to structured planning mode unless asked
- Hooks source code lives in project source tree (e.g. `cursor-hooks/`), not under `.cursor/`
- Keep docs user-focused; don't document internal implementation details that don't help users
- Use `asyncio.to_thread()` for all blocking calls (chDB, embedding, LLM) inside the async server

## Learned Workspace Facts

- ClickMem is a local-first memory system shared by Claude Code, Cursor, OpenClaw via MCP + REST API
- Three-layer memory: Raw (separate table, append-only) → L1 Episodic → L2 Semantic (most refined at top)
- L1 episodic memories carry `raw_id` pointing to `raw_transcripts` table for lineage tracking
- Storage: chDB (embedded ClickHouse), single-process lock per data directory; all access through one server process
- Table engine: ReplacingMergeTree(updated_at); all SELECTs use FINAL; updates via INSERT, no ALTER mutations in hot paths
- Server: REST + MCP SSE on single port 9527; MCP stdio for local; mDNS `_clickmem._tcp` for LAN discovery; Bearer auth
- Local LLM: Qwen3.5-2B (default), 4B, or 9B (MLX on Apple Silicon) for extraction/summary, switchable via `CLICKMEM_LOCAL_MODEL`; Qwen3-Embedding-0.6B (256d) for vectors
- Retrieval: vector + keyword hybrid search, MMR dedup (threshold 0.92), semantic boost 1.3x, refinement boost 1.15x
- Config env vars: `CLICKMEM_SERVER_HOST`, `CLICKMEM_SERVER_PORT` (default 127.0.0.1:9527), `CLICKMEM_REMOTE`, `CLICKMEM_LLM_MODE`, `CLICKMEM_LOCAL_MODEL`, `CLICKMEM_REFINE_THRESHOLD` (default 1)
- Deploy target: Mac Mini (`mini.local`) via Tailscale; rsync to `~/clickmem`
- Tests use MockEmbeddingEngine and MockLLMComplete to avoid loading real models; all tests use in-memory chDB
- PyPI package name: `clickmem`
