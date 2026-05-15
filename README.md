# ClickMem

![ClickMem — Local memory for AI coding agents](assets/clickmem-banner.png)

**A local memory system for AI coding agents. You decide what your agent remembers — and what it stops remembering.**

ClickMem keeps a small, high-signal store of memories your coding agents share across sessions and tools. No GPU, no LLM in the loop, no auto-extraction — just memories your agent (or you) deliberately committed, fully editable from a dashboard and CLI.

---

## Why ClickMem

Most agent-memory products today try to mine signal out of every transcript and pile up auto-extracted notes. Two things go wrong:

- **Memory garbage.** Low-signal notes accumulate over time and drown out the few items you actually want the agent to remember.
- **Hallucinated memory.** When extracted notes are subtly wrong, stale, or contradict each other, the agent pulls them into context and acts on them confidently — a wrong memory is worse than no memory.

ClickMem skips the auto-extraction entirely. Memories enter the store in exactly two ways:

1. The agent calls `clickmem_remember` after it has *itself* decided a conclusion was worth keeping.
2. You import curated docs (`AGENTS.md`, `CLAUDE.md`, `.cursor/rules/*.mdc`) you've already chosen to keep.

Raw transcripts are still captured for the record, but only as cold storage — they're never read by recall, never auto-injected into context.

## What you get

- **One brain, every agent.** Claude Code, Cursor, Codex, Aider, Continue.dev, Cline, Windsurf, Zed, JetBrains AI — all read from and write to the same memories via REST or MCP.
- **Two storage backends.** Embedded chDB for single-machine / LAN, or ClickHouse Cloud / self-hosted for multi-device. Switch with one env var.
- **Project + privacy aware.** Memories are partitioned by project and privacy level (`public` / `private` / `confidential`). Work memories never leak into a side-project session.
- **Full management UI.** A built-in dashboard at `/dashboard` for browsing, editing, pinning, forgetting, blacklisting, and resolving conflicts.
- **CLI + MCP parity.** Everything the dashboard does is a single CLI command and a single MCP tool.
- **No GPU required.** The server runs no LLM. The only loaded model is the embedding model (Qwen3-Embedding-0.6B, 256 d, CPU-friendly).

## Quick start

```bash
pip install clickmem
clickmem service install        # background server on :9527, no model download
clickmem hooks install          # raw-only landing hooks for every detected agent
clickmem dashboard open         # http://127.0.0.1:9527/dashboard
```

Agents on this machine — and any LAN host pointed at the server — now share one memory store. Open the dashboard to see who's connected and what's being captured.

## How memories enter the store

Two explicit paths. No auto-capture from transcripts.

### 1. Your agent calls `clickmem_remember`

After a task completes, the agent decides what was worth keeping and pushes a refined note via the MCP tool:

```json
{
  "tool": "clickmem_remember",
  "args": {
    "content": "When using chDB inside an asyncio app, wrap every query in asyncio.to_thread — the C++ embedded server is not safe to call from the event loop.",
    "kind": "principle",
    "privacy": "public",
    "tags": ["python", "chdb", "async"]
  }
}
```

The agent inherits your current project from `cwd` automatically. Privacy defaults to `private`.

### 2. You import curated docs

```bash
clickmem import-docs                          # walks current repo
clickmem import-docs --path ~/work/main-app
```

The importer walks for `AGENTS.md`, `CLAUDE.md`, `.cursor/rules/*.mdc`, `.claude/projects/*/memory/*.md` — and skips AI-generated noise:

- files larger than 8 KB with no `git log` history (likely dumped, never reviewed)
- files marked `<!-- generated -->`
- Dream auto-memory files that include a `## Reasoning` block
- bullet-heavy files where the average bullet is over 200 chars

`AGENTS.md` is parsed bullet-by-bullet — each bullet is a discrete memory, idempotent on re-import.

## Managing memories

Everything you can do, you can do from the dashboard, the CLI, or over MCP.

| Action | What it means | Command |
|--------|---------------|---------|
| **Add** | Store a new memory | `clickmem remember "..."` |
| **Edit** | Update an existing memory when new info arrives | `clickmem edit <id> --content "..."` |
| **Forget** | Mark a memory obsolete — won't be recalled again | `clickmem forget <id> --reason "..."` |
| **Pin** | Mark a memory authoritative; protect from dedup | `clickmem pin <id>` |
| **Blacklist** | Refuse to ever store content matching a pattern | `clickmem blacklist add "..." --reason "..."` |

ClickMem also surfaces **conflicts** automatically. When a new note is semantically close to an existing one but the content differs in a meaningful way, ClickMem flags both as unresolved instead of silently merging. You (or the agent) resolve them in the Conflicts page or with `clickmem resolve`.

## Project + privacy partitioning

Two independent dimensions, both enforced at recall time.

**Project** is detected from `cwd` → git remote at write time and frozen on the memory.

| Source | Recall multiplier |
|--------|-------------------|
| Same project | ×1.0 |
| Global (`project_id=''`) | ×0.9 |
| Other project | ×0.0 (hidden by default) |

If you regularly cross-reference two projects (e.g. a backend + its mobile client), declare it once:

```bash
clickmem project link backend-api mobile-app --reason "shared API contracts"
```

**Privacy** is `public`, `private` (default), or `confidential`. Recall returns `public + private` for the same project. `confidential` requires explicit acknowledgement at the recall site and is never included in `clickmem export` without `--include-confidential`.

```bash
clickmem remember "Internal credentials live in 1Password vault 'Eng'" --privacy confidential
```

## The dashboard

`http://127.0.0.1:9527/dashboard` (or your LAN URL).

A clean web UI bundled into the wheel. No separate install, no Node required at runtime.

- **Overview** — total memories with growth sparkline, top projects, memories-by-kind donut, privacy × project mix, pinned memories, the live **Recent Memories** feed (every add/edit/forget as it happens), brain-health key metrics, and a footer **integrations health bar** with one traffic-light chip per agent.
- **Memories** — the primary management surface. Filter and search across full-text + semantic. Row click opens a side-drawer to edit content, kind, project, privacy, tags, and pin; see neighbors and recall history; review the full edit log. Bulk actions for reassign / privacy / pin / blacklist / forget. "+ Add Memory" creates one from scratch.
- **Conflicts** — queue of unresolved contradictions. Each row shows both peers side-by-side with diff highlighting; one click to keep one and forget the other, edit one from the other, or allow the divergence.
- **Recall Lab** — try queries with full scoring breakdown (vector similarity, project boost, privacy filter). Compare two queries side-by-side. Curate from results inline.
- **Raw transcripts** — cold-storage browse. "Promote to memory" turns a raw selection into a refined memory via the same drawer.
- **Agents** — one card per detected integration: install state, last event time, 24-hour activity sparkline, error count, and buttons to Install / Reinstall / Uninstall / Test the loop end-to-end.
- **Imports** · **Blacklist** · **Preferences** — self-explanatory.

## Supported agents

Every adapter handles raw landing and doc import. Refined memories flow through `clickmem_remember` (MCP) regardless of agent.

| Agent | Auto-detect | Raw hooks | Doc import | Notes |
|-------|:-:|:-:|:-:|-------|
| Claude Code | ✓ | ✓ | `CLAUDE.md`, `.claude/.../memory/*.md` | SessionStart recall + Stop raw landing |
| Cursor | ✓ | ✓ | `.cursor/rules/*.mdc`, `~/.cursor/rules/*.mdc` | TS stop hook ships raw, never blocks |
| Codex CLI | ✓ | ✓ | `~/.codex/AGENTS.md`, `~/.codex/memories/*.md` | Reuses Claude Code hook endpoint |
| Aider | ✓ | doc-only | `~/.aider.chat.history.md`, `.aider.conf.yml` | |
| Continue.dev | ✓ | ✓ | `.continue/rules/*.md` | `dev_data/*.jsonl` for sessions |
| Cline | ✓ | doc-only | VS Code workspace storage | Experimental |
| Windsurf | ✓ | doc-only | `~/.codeium/windsurf/memories/*` | |
| Zed | ✓ | doc-only | `~/.config/zed/conversations/*.json` | |
| JetBrains AI | ✓ | doc-only | `aiAssistant/` chat history | Experimental |
| Generic | n/a | REST/MCP direct | n/a | For anything not in this list |

```bash
clickmem agents                              # list detected adapters with status
clickmem hooks install                       # install for all detected
clickmem hooks install --agent claude-code   # install for one
```

## Storage backends

Choose at server start with one env var. Schema is identical across both.

| Backend | Use when | Configuration |
|---------|---------|---------------|
| **chDB** (default) | Single-machine or LAN-shared from one host | `CLICKMEM_BACKEND=local`; `CLICKMEM_DB_PATH=~/.clickmem/data` |
| **ClickHouse Cloud / self-hosted** | Multi-device without a relay host, or larger-than-disk corpora | `CLICKMEM_BACKEND=clickhouse` plus `CLICKMEM_CH_URL`, `CLICKMEM_CH_USER`, `CLICKMEM_CH_PASSWORD`, `CLICKMEM_CH_DATABASE` |

## Portable import / export

Move memories between machines, between backends, or share a curated bundle. Embeddings ride along so the destination doesn't have to recompute.

```bash
clickmem export --out brain.jsonl
clickmem export --project backend-api --out api.jsonl
clickmem export --format markdown --out brain.md

clickmem import brain.jsonl                                # idempotent, dedups by content hash + project
clickmem import api.jsonl --remap-project backend-api=mainapi-prod
```

## LAN mode

Run the server on one host (e.g. a Mac Mini), point every other machine's CLI / MCP at it.

```bash
# Server (one-time setup on the host)
clickmem service install --host 0.0.0.0
clickmem serve --gen-key                              # prints API key

# Every other machine
export CLICKMEM_REMOTE=http://mini.local:9527
export CLICKMEM_API_KEY=<key>
clickmem recall "where did we deploy the api"
```

For Claude Code / Cursor MCP, swap the local URL for the LAN one — the dashboard's Agents page generates the right snippet for each.

## Debug and inspect

Everything the dashboard does is also on the CLI and over MCP.

```bash
clickmem list --project X --kind principle --json
clickmem show <id>                          # full content + neighbors + recall history + edit log
clickmem edit <id> --content "..." --privacy public --pin
clickmem forget <id> --reason "obsolete after deploy migration"
clickmem pin <id>                           # protect from dedup / cleanup
clickmem blacklist add "internal-only stuff" --scope global --reason "leaks"
clickmem blacklist add id:abc-123 --reason "outdated"
clickmem get-raw <session_id> [--last N]    # raw transcripts, never auto-recalled
clickmem recall-trace "your query"          # show why each result scored what it did
clickmem conflicts                          # list unresolved contradictions
clickmem resolve <id> --revise <peer_id>    # or --contract / --allow
```

## Architecture

![ClickMem architecture](assets/clickmem-architecture.png)

One process, one port (9527), one memories table. The server runs no LLM. The only loaded model is the embedding model.

## CLI ↔ MCP parity

| CLI | MCP tool |
|-----|----------|
| `clickmem remember` | `clickmem_remember` |
| `clickmem edit` | `clickmem_edit` |
| `clickmem forget` | `clickmem_forget` |
| `clickmem pin` / `unpin` | `clickmem_pin` |
| `clickmem blacklist add/remove/list` | `clickmem_blacklist` |
| `clickmem recall` | `clickmem_recall` |
| `clickmem show` | `clickmem_show` |
| `clickmem conflicts` | `clickmem_conflicts` |
| `clickmem resolve` | `clickmem_resolve` |
| `clickmem get-raw` | `clickmem_get_raw` |
| `clickmem recall-trace` | `clickmem_recall_trace` |
| `clickmem project link` | `clickmem_project` |

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CLICKMEM_SERVER_HOST` | `127.0.0.1` | Bind address |
| `CLICKMEM_SERVER_PORT` | `9527` | HTTP port (REST + MCP SSE + dashboard) |
| `CLICKMEM_REMOTE` | — | Point CLI/MCP at a LAN server |
| `CLICKMEM_API_KEY` | — | Bearer token (required for any non-loopback bind) |
| `CLICKMEM_BACKEND` | `local` | `local` (chDB) or `clickhouse` |
| `CLICKMEM_DB_PATH` | `~/.clickmem/data` | chDB data dir (only when `BACKEND=local`) |
| `CLICKMEM_CH_URL` | — | ClickHouse Cloud / self-hosted URL |
| `CLICKMEM_CH_USER` / `CH_PASSWORD` / `CH_DATABASE` | — / — / `clickmem` | ClickHouse auth + database |
| `CLICKMEM_EMBEDDING_MODEL` | `Qwen/Qwen3-Embedding-0.6B` | Override embedding model |
| `CLICKMEM_CONFLICT_THRESHOLD` | `0.92` | Cosine similarity above which divergent content is flagged as a conflict |
| `CLICKMEM_LOG_LEVEL` | `WARNING` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

No `CLICKMEM_LLM_*` variables exist. The server doesn't host an LLM.

---

## Design philosophy: a belief revision system

This section is for the curious. You don't need to read it to use ClickMem.

The deeper idea behind the design is that a good agent memory is not a *log*, it's a set of *beliefs the agent currently holds*. A log can only grow; a set of beliefs has to support:

- **Expansion** — adding a new belief
- **Revision** — replacing an old belief with a new one when they conflict
- **Contraction** — removing a belief without contradiction

This is classical [belief revision theory](https://plato.stanford.edu/entries/logic-belief-revision/). Most agent-memory products today are pure logs: they can expand, but they can't revise or contract. That's why their recall gets noisier over time, and why they end up handing the agent contradictory snippets that fuel hallucinations.

ClickMem maps belief revision directly onto its primitives:

| Belief revision | ClickMem |
|-----------------|---------|
| Expansion | `clickmem remember` |
| Revision | `clickmem edit` (creates a new version in the edit log, links via `revises_id`) |
| Contraction | `clickmem forget` (records a reason; the agent will not re-believe it) |
| Reinforcement / authority | `clickmem pin` (pinned memories take priority; conflicting commits against a pinned memory are rejected unless the caller explicitly revises) |
| Refusal | `clickmem blacklist` (a pattern that may never become a memory) |

Conflict surfacing is the rule that makes revision actually work: when a new commit's embedding is close to an existing memory but the text materially differs, both are flagged conflicted instead of being silently merged. The agent's `clickmem_remember` response carries this back, so the agent's current-session model can choose to revise, contract, or escalate to you. Unresolved conflicts queue up in the dashboard for the human in the loop.

In short, ClickMem is built on the assumption that **the most important operation in an agent memory is not adding something, it's letting you change your mind**. Everything in the system — the dashboard, the CLI, the MCP surface, the storage schema — is shaped around that.

## Development

```bash
make test                  # full test suite (in-memory chDB)
make test-fast             # skip semantic / slow tests
make dashboard             # build the dashboard SPA into dist/
make deploy                # rsync to LAN host + setup
```

**Requirements:** Python ≥ 3.10, macOS or Linux (chDB), `pnpm` for dashboard development only. End users get the pre-built `dist/` in the wheel.

## License

MIT
