# ClickMem — Design

A one-page architectural overview. For user-facing detail see `README.md`; for the execution sequencing of the v1 rebuild see `.cursor/plans/clickmem-v1-explicit-only-refactor_*.plan.md`.

## Why explicit-only

Two failure modes ship with every "extract everything" agent-memory product, and they compound:

- **Memory garbage.** Auto-extraction accumulates low-signal notes from every transcript. The few items you actually want to recall get drowned out by noise — the system gets *less* useful as it sees more data.
- **Hallucinated memory.** When auto-extracted notes are subtly wrong, stale, or quietly contradict each other, the agent pulls them into context and acts on them confidently. A wrong memory is strictly worse than no memory.

Both come from the same root: append-only knowledge piles with no concept of revising or contracting a belief. ClickMem fixes this at the data-model level. Memories enter the store in exactly two ways:

1. The agent calls `clickmem_remember` *after* it has decided (with its own session model) that a conclusion is worth keeping.
2. The user (or an importer) commits a curated doc (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules/*.mdc`).

Raw transcripts are still captured (cold storage), but they are **never** read by recall. The server itself runs **zero LLM** — any reasoning is delegated to the calling agent.

## The five operations (belief revision)

Every stored memory is something explicitly committed. The system exposes five first-class ops, mapped to standard belief-revision theory:

| Op | Meaning | CLI | MCP |
|----|---------|-----|-----|
| **Expand** | Add a new belief | `clickmem remember` | `clickmem_remember` |
| **Revise** | Replace a belief with a better one | `clickmem edit` | `clickmem_edit` |
| **Contract** | Retract a belief (with reason) | `clickmem forget` | `clickmem_forget` |
| **Reinforce** | Mark a belief authoritative, immune to silent revision | `clickmem pin` / `unpin` | `clickmem_pin` |
| **Refuse** | Reject a pattern from ever entering the store | `clickmem blacklist` | `clickmem_blacklist` |

Inspection helpers (`recall`, `show`, `list`, `conflicts`, `resolve`, `recall-trace`, `get-raw`, `project link`) have CLI ↔ MCP parity.

## Conflict surfacing

The structural fix for hallucinated memory: the store can never silently hold two contradictory memories about the same thing. On every Expand or Revise, `conflicts.check_on_commit` runs a four-step algorithm:

1. Compute the embedding for the new content; vector-search active peers in the same `(project_id, kind)` partition at `cosine ≥ CLICKMEM_CONFLICT_THRESHOLD` (default 0.92).
2. For each candidate, normalise to canonical form (lowercase, whitespace, punctuation collapsed). If the normalised forms match, **merge** — append a history row to the existing memory and return its id.
3. Embeddings close, text materially different → set both rows to `status='conflicted'`, populate each other's `conflict_with`. Return `{status:"conflicted", id, peer_ids}` so the caller can immediately Revise/Contract/escalate instead of silently dropping the commit.
4. **Pinned short-circuit:** if any candidate is pinned, a non-pinned conflicting commit is **rejected** outright. The caller must explicitly `clickmem_edit` the pinned memory to change it.

Unresolved conflicts collect in the dashboard's Conflicts queue; `clickmem_resolve <id> --revise|--contract|--allow <peer>` closes a pair.

## Data model

Six tables, one source of truth in `src/clickmem/schema.py`:

- **`memories`** — the `Memory` entity. Fields: `id`, `content`, `kind ∈ {principle, decision, fact, doc, free}`, `source`, `source_ref`, `project_id`, `privacy ∈ {public, private, confidential}`, `tags`, `embedding`, plus revision metadata (`status ∈ {active, contracted, conflicted}`, `pinned`, `contract_reason`, `revises_id`, `conflict_with`), plus timestamps. Engine: `ReplacingMergeTree(updated_at)`.
- **`memory_history`** — immutable log: `(memory_id, version, content, edited_by, edited_at, op)`. Every Expand/Revise/Contract appends a row. Drives the dashboard's edit-history diff view and `clickmem show <id> --history`.
- **`projects`** — `(id, name, repo_url, kind ∈ {work, personal, global}, allowed_cross_refs, embedding)`. Detected from cwd → git remote and frozen on the memory at commit time.
- **`blacklist`** — `(pattern, scope, reason, hit_count, created_at)`. Enforced both on insert (`enforce_on_insert`) and on recall (`enforce_on_recall`).
- **`raw_transcripts`** — cold storage written by hooks (`POST /v1/raw`). Never read by recall; exposed only via `clickmem_get_raw`.
- **`events`** (`MergeTree`, TTL 30 d) — every API mutation and adapter raw landing. Drives the dashboard activity feed and per-integration health.

## Recall scoring

Embedding-only recall. No keyword pipeline, no LLM rerank — the server is pure embedding lookup + filter:

- **Project multiplier**: same-project ×1.0, global (`project_id=''`) ×0.9, other-project ×0.0. Override with `cross_project=true` or via `projects.allowed_cross_refs`.
- **Tag filter**: callers can pass `tags` with `tag_mode='any'|'all'`; matched tags prefilter candidates before vector ranking and add a small ranking boost.
- **Privacy filter**: default returns `public` + `private` for the same project; `confidential` is excluded from MCP responses unless the caller passes `privacy_ack=true`.
- **Pinned boost**: pinned memories ride above non-pinned at equal cosine.
- **Status filter**: `contracted` memories are excluded; `conflicted` are still returned but surface a warning in the trace.

`POST /v1/recall` and `/v1/recall/trace` default to a roughly 5 second fail-open timeout so startup recall can never freeze an agent. Trace returns the per-candidate breakdown (cosine · project mult · tag boost · privacy verdict · pinned boost · final score) for the Recall Lab page.

## Backend abstraction

One `Backend` protocol in `src/clickmem/backend/__init__.py`:

```python
class Backend(Protocol):
    def query(self, sql: str) -> list[dict]: ...
    def execute(self, sql: str) -> None: ...           # INSERT/ALTER/DDL
    def vector_search(self, table, query_vec,
                      where: str, limit: int) -> list[dict]: ...
    def close(self) -> None: ...
```

Two implementations, selected by `CLICKMEM_BACKEND`:

- **`LocalBackend`** (`backend/local_chdb.py`) — wraps `chdb.session.Session` with a persistent path and a single-process lock retry. Default. Fast, single-machine / LAN.
- **`ClickHouseBackend`** (`backend/clickhouse.py`) — `clickhouse-connect` HTTP client. Reads `CLICKMEM_CH_URL` / `CH_USER` / `CH_PASSWORD` / `CH_DATABASE`. Works against ClickHouse Cloud or any self-hosted cluster. Same DDL as local.

Schema portability: `multiSearchAnyCaseInsensitive`, `cosineDistance`, `FINAL` all work on both. The optional `ANN INDEX vector TYPE annoy(...)` is try-add on ClickHouse, no-op on chDB.

`CLICKMEM_REMOTE=http://mini.local:9527` makes the CLI / MCP a thin client to a LAN server — that server can use either backend independently.

## Adapter framework

```python
class AgentAdapter(Protocol):
    name: str                          # "claude_code", "cursor", ...
    def detect(self) -> bool: ...
    def iter_raw_sessions(self, since): ...
    def iter_doc_paths(self) -> list[Path]: ...
    def install_hooks(self, server_url) -> None: ...
    def export_blob(self, dst_path) -> None: ...
```

Built-ins under `src/clickmem/adapters/`: `claude_code`, `cursor`, `codex`, `aider`, `continue_dev`, `cline`, `windsurf`, `zed`, `jetbrains`, `generic`. Experimental adapters are isolated and ship with one focused parser test using a vendored sample, so an upstream format change breaks a single test instead of the import pipeline.

`clickmem agents list` shows every detected adapter with session counts and hook-install status. The dashboard's Agents page surfaces the same data with one-click install / uninstall / test.

## Dashboard as primary management surface

The CLI is sufficient but not ergonomic for bulk memory hygiene. The dashboard at `/dashboard` (React + Vite + Tailwind + Recharts, bundled into the wheel) is where users:

- Browse memories with filter+search (`project`, `privacy`, `kind`, `pinned`, `status`, `source_agent`, date).
- Resolve conflicts side-by-side with one-click Keep A / Keep B / Revise A from B / Allow.
- Run the Recall Lab to compare queries and inspect scoring.
- Promote raw transcripts to memories, manage the blacklist, configure preferences.
- Watch integration health and per-adapter activity.

CLI and MCP are the same surface for agents and scripts; the dashboard is the same surface for humans. They share `/v1/*` underneath.

## Non-goals

ClickMem deliberately does **not** do:

- **Auto-extraction.** No background LLM mining of raw transcripts into "decisions" or "principles". If the agent didn't refine it, it doesn't enter the store.
- **LLM in the server.** Zero models loaded server-side except the embedding model (Qwen3-Embedding-0.6B, 256 d, CPU). No `mlx-lm`, no `litellm`, no remote-LLM fallback.
- **Automatic context injection.** Hooks land raw transcripts and may issue one SessionStart recall, nothing more. The agent decides when to call `clickmem_recall` — never the server pushing memories unsolicited.
- **Cross-project surfacing by default.** Recall is project-scoped. Cross-project linkage is opt-in (`clickmem project link` or `cross_project=true` per call).
- **Migration from v0.** The v0 CEO Brain auto-extract codebase was wiped wholesale. 1.0.0 is a fresh start; existing users reinstall with `clickmem wipe` and rebuild.
