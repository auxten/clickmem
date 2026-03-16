## Learned User Preferences

**Code quality & process**
- Fix root causes for all users; no case-by-case patches, retries, or workarounds; revert speculative changes promptly
- All config via env vars with sensible defaults; never hardcode IPs or machine-specific values
- All repo content (docs, comments, code) in English; no Chinese
- Batch commits by logical grouping; commit and push only when explicitly asked; release only via git tags + CI

**Architecture & design**
- Plan and discuss architecture before coding; deliver plans in current chat mode — don't switch to Plan mode unless asked
- All interfaces (CLI, MCP tools, plugins) must expose the same capabilities as the HTTP API; never access chDB directly
- Use `asyncio.to_thread()` for all blocking calls (chDB, embedding, LLM) inside the async server
- Prefer event-driven hooks over periodic cron; hooks source code in project tree (`cursor-hooks/`), not `.cursor/`
- Keep docs user-focused and command-driven; preserve rich metadata when importing agent conversation history

**Workflow**
- Deploy changes AND verify end-to-end yourself; don't tell the user to verify
- Stay focused; don't get sidetracked; don't proactively scan or import beyond what's explicitly specified
- Coordinate parallel sessions: if another session implements a feature, revert speculative changes and wait for merge

## Learned Workspace Facts

- ClickMem: local-first memory system for AI coding agents; shared by Claude Code, Cursor, OpenClaw via MCP + REST API
- CEO Brain: five knowledge entity types — projects, decisions, principles, episodes, raw_transcripts — in separate chDB tables; context engine injects structured context on SessionStart
- chDB (embedded ClickHouse); `ReplacingMergeTree(updated_at)` engine; all SELECTs use `FINAL`; single-process lock per data dir
- Single port 9527: REST `/v1/*` + MCP SSE `/sse` + MCP stdio (`clickmem-mcp`); LAN discovery via mDNS; Bearer auth via `CLICKMEM_API_KEY`
- Local LLM auto-selects by GPU memory: Apple Silicon MLX Qwen3.5 4-bit (8GB→2B, 16GB→4B, 32GB→9B); CUDA full-precision; CPU-only → remote API fallback
- `enable_thinking=False` in Qwen3 chat template for structured JSON output; embedding: Qwen3-Embedding-0.6B (256 dims) on CPU/CUDA only (never PyTorch MPS)
- Unified recall: hybrid vector + keyword search merging old `memories` table + CEO tables; MMR dedup; `since`/`until` time filtering
- Chunked extraction: long conversations split into segments at turn boundaries (max 5 × 4000 chars); dedup merges results across segments
- PyPI package `clickmem`; `pip install clickmem` includes all deps; CI tests on Python 3.10/3.12/3.13; release via `v*` tags + Trusted Publisher
- Config: `CLICKMEM_SERVER_HOST/PORT` (9527), `CLICKMEM_REMOTE`, `CLICKMEM_API_KEY`, `CLICKMEM_DB_PATH`, `CLICKMEM_LLM_MODE/MODEL/LOCAL_MODEL`, `CLICKMEM_REFINE_THRESHOLD`, `CLICKMEM_LOG_LEVEL`
- Tests: `MockEmbeddingEngine` + `MockLLMComplete` via conftest; all tests use in-memory chDB
- Deploy target: Mac Mini M4 32GB (`mini.local`) via Tailscale; launchd service with auto-restart
