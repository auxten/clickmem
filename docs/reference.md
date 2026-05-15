# ClickMem Reference

This page keeps the operational details out of the main README. It covers CLI usage, MCP parity, configuration, storage, LAN mode, import/export, and development commands.

## Quick Start

```bash
pip install clickmem
clickmem service install        # background server on :9527
clickmem hooks install          # install hooks for detected agents
clickmem dashboard open         # http://127.0.0.1:9527/dashboard
```

## Core Memory Commands

```bash
clickmem remember "..."
clickmem list --project X --kind principle --json
clickmem show <id>
clickmem edit <id> --content "..." --privacy public --pin
clickmem forget <id> --reason "obsolete after deploy migration"
clickmem pin <id>
clickmem blacklist add "internal-only stuff" --scope global --reason "leaks"
clickmem blacklist add id:abc-123 --reason "outdated"
clickmem conflicts
clickmem resolve <id> --revise <peer_id>    # or --contract / --allow
clickmem recall "your query"
clickmem recall-trace "your query"
clickmem get-raw <session_id> [--last N]
```

## How Memories Enter

### Agent Commit

After a task completes, a connected agent can commit a refined memory through MCP:

```json
{
  "tool": "clickmem_remember",
  "args": {
    "content": "When using chDB inside an asyncio app, wrap every query in asyncio.to_thread because the embedded server is blocking.",
    "kind": "principle",
    "privacy": "public",
    "tags": ["python", "chdb", "async"]
  }
}
```

The agent inherits the current project from `cwd`. Privacy defaults to `private`.

### Curated Doc Import

```bash
clickmem import-docs
clickmem import-docs --path ~/work/main-app
```

The importer walks for `AGENTS.md`, `CLAUDE.md`, `.cursor/rules/*.mdc`, and `.claude/projects/*/memory/*.md`.

It skips likely noise:

- files larger than 8 KB with no `git log` history
- files marked `<!-- generated -->`
- Dream auto-memory files that include a `## Reasoning` block
- bullet-heavy files where the average bullet is over 200 characters

`AGENTS.md` is parsed bullet by bullet. Each bullet becomes a discrete, idempotent memory.

## Managing Memories

| Action | Meaning | CLI |
| --- | --- | --- |
| Add | Store a new memory | `clickmem remember "..."` |
| Edit | Update a memory when new information arrives | `clickmem edit <id> --content "..."` |
| Forget | Mark a memory obsolete | `clickmem forget <id> --reason "..."` |
| Pin | Mark a memory authoritative | `clickmem pin <id>` |
| Blacklist | Refuse future content matching a pattern | `clickmem blacklist add "..." --reason "..."` |

ClickMem also surfaces conflicts automatically. When a new memory is semantically close to an existing one but materially different, both rows are marked conflicted until you resolve them.

## Project And Privacy Scoping

Project is detected from `cwd` to git remote at write time and frozen on the memory.

| Source | Recall multiplier |
| --- | --- |
| Same project | x1.0 |
| Global (`project_id=''`) | x0.9 |
| Other project | x0.0 by default |

If two projects intentionally share memory, link them:

```bash
clickmem project link backend-api mobile-app --reason "shared API contracts"
```

Privacy levels:

- `public`
- `private` (default)
- `confidential`

Recall returns `public` and `private` memories for the current project. `confidential` requires explicit acknowledgement and is excluded from export unless `--include-confidential` is passed.

```bash
clickmem remember "Internal credentials live in 1Password vault 'Eng'" --privacy confidential
```

## Dashboard

The dashboard is served at:

```text
http://127.0.0.1:9527/dashboard
```

Main areas:

- Overview
- Memories
- Conflicts
- Recall Lab
- Raw transcripts
- Agents
- Imports
- Blacklist
- Preferences

The dashboard is bundled into the wheel. No Node install is required at runtime.

## Supported Agents

Every adapter handles raw landing and doc import. Refined memories flow through `clickmem_remember` regardless of agent.

| Agent | Auto-detect | Raw hooks | Doc import | Notes |
| --- | :-: | :-: | :-: | --- |
| Claude Code | yes | yes | `CLAUDE.md`, `.claude/.../memory/*.md` | SessionStart recall + Stop raw landing |
| Cursor | yes | yes | `.cursor/rules/*.mdc`, `~/.cursor/rules/*.mdc` | TS stop hook ships raw, never blocks |
| Codex CLI | yes | yes | `~/.codex/AGENTS.md`, `~/.codex/memories/*.md` | Reuses Claude Code hook endpoint |
| Aider | yes | doc-only | `~/.aider.chat.history.md`, `.aider.conf.yml` | |
| Continue.dev | yes | yes | `.continue/rules/*.md` | `dev_data/*.jsonl` for sessions |
| Cline | yes | doc-only | VS Code workspace storage | Experimental |
| Windsurf | yes | doc-only | `~/.codeium/windsurf/memories/*` | |
| Zed | yes | doc-only | `~/.config/zed/conversations/*.json` | |
| JetBrains AI | yes | doc-only | `aiAssistant/` chat history | Experimental |
| OpenClaw | yes | yes | `~/.openclaw/workspace/memory/*.md` | Managed hook; restart gateway after install |
| Hermes Agent | yes | yes | `~/.hermes/{MEMORY,USER,AGENTS}.md` | Gateway hook; restart gateway after install |
| Generic | n/a | REST/MCP direct | n/a | For anything else |

```bash
clickmem agents
clickmem hooks install
clickmem hooks install --agent claude-code
```

## Storage Backends

Choose the backend at server start with `CLICKMEM_BACKEND`. Schema is identical across both.

| Backend | Use when | Configuration |
| --- | --- | --- |
| chDB (default) | Single-machine or LAN-shared from one host | `CLICKMEM_BACKEND=local`; `CLICKMEM_DB_PATH=~/.clickmem/data` |
| ClickHouse Cloud / self-hosted | Multi-device without a relay host, or larger-than-disk corpora | `CLICKMEM_BACKEND=clickhouse` plus ClickHouse connection variables |

## Import And Export

Move memories between machines, backends, or projects. Embeddings ride along so the destination does not have to recompute them.

```bash
clickmem export --out brain.jsonl
clickmem export --project backend-api --out api.jsonl
clickmem export --format markdown --out brain.md

clickmem import brain.jsonl
clickmem import api.jsonl --remap-project backend-api=mainapi-prod
```

## LAN Mode

Run the server on one host and point every other machine's CLI or MCP client at it.

```bash
# Server host
clickmem service install --host 0.0.0.0
clickmem serve --gen-key

# Other machines
export CLICKMEM_REMOTE=http://mini.local:9527
export CLICKMEM_API_KEY=<key>
clickmem recall "where did we deploy the api"
```

For Claude Code and Cursor MCP, swap the local URL for the LAN URL. The dashboard's Agents page generates the right snippet.

## CLI To MCP Parity

| CLI | MCP tool |
| --- | --- |
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

## REST Surface

The dashboard, CLI, and MCP server share the same `/v1/*` HTTP surface.

Representative endpoints:

- `/v1/memories`
- `/v1/recall`
- `/v1/recall/trace`
- `/v1/conflicts`
- `/v1/blacklist`
- `/v1/projects`
- `/v1/raw`
- `/v1/agents`
- `/v1/events`
- `/v1/stats/*`

## Configuration

| Variable | Default | Description |
| --- | --- | --- |
| `CLICKMEM_SERVER_HOST` | `127.0.0.1` | Bind address |
| `CLICKMEM_SERVER_PORT` | `9527` | HTTP port for REST, MCP SSE, and dashboard |
| `CLICKMEM_REMOTE` | unset | Point CLI/MCP at a LAN server |
| `CLICKMEM_API_KEY` | unset | Bearer token, required for non-loopback bind |
| `CLICKMEM_BACKEND` | `local` | `local` or `clickhouse` |
| `CLICKMEM_DB_PATH` | `~/.clickmem/data` | chDB data dir for local backend |
| `CLICKMEM_CH_URL` | unset | ClickHouse Cloud or self-hosted URL |
| `CLICKMEM_CH_USER` | unset | ClickHouse user |
| `CLICKMEM_CH_PASSWORD` | unset | ClickHouse password |
| `CLICKMEM_CH_DATABASE` | `clickmem` | ClickHouse database |
| `CLICKMEM_EMBEDDING_MODEL` | `Qwen/Qwen3-Embedding-0.6B` | Embedding model |
| `CLICKMEM_CONFLICT_THRESHOLD` | `0.92` | Cosine similarity above which divergent content is flagged as a conflict |
| `CLICKMEM_LOG_LEVEL` | `WARNING` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |

No `CLICKMEM_LLM_*` variables exist.

## Development

```bash
make install
make test
make test-fast
make dashboard
make deploy
make clean
```

Requirements:

- Python 3.10+
- macOS or Linux for chDB
- `pnpm` for dashboard development only

End users get the pre-built dashboard `dist/` in the wheel.
