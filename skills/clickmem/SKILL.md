---
name: clickmem
description: |
  Local memory store for AI coding agents — explicit-only, no auto-extraction.
  Use when:
  - You just refined a conclusion worth keeping across sessions → call `clickmem_remember`
  - Starting a task → derive `project_id` + tags, call `clickmem_recall`, load hits into context, then work
  - The user asks "what did we decide about X" → call `clickmem_recall`
  - A new commit comes back as `conflicted` → review with `clickmem_show` and call `clickmem_resolve`
  - A stored memory is wrong or stale → `clickmem_edit` (Revise) or `clickmem_forget` (Contract)
  - The user marks a memory as canonical → `clickmem_pin`; bad pattern that should never be remembered → `clickmem_blacklist`
  - You need recall scoring details for debugging → `clickmem_recall_trace`
metadata:
  cursor:
    emoji: 🧠
    requires:
      bins:
        - clickmem
    install: "pip install clickmem && clickmem service install && clickmem hooks install"
---

# ClickMem — Explicit Memory for AI Agents

ClickMem is a *belief-revision* memory store. Memories enter only when the agent (or the user) deliberately commits one. There is **no auto-extraction**, no LLM in the server loop, no automatic context injection. You read and write through MCP tools or the `clickmem` CLI; both expose the same operations and back the same chDB (or ClickHouse Cloud) store on `http://127.0.0.1:9527`.

All memory writes must carry explicit management metadata: either a concrete `project_id` such as `auxten/clickmem` or the literal `global`, plus at least one tag. Project and privacy scope are never guessed on write; recall still defaults to the current project + global unless overridden.

---

## When to remember

**Do** call `clickmem_remember` after the *agent's current session* refined a conclusion worth carrying forward:

- A decision with reasoning ("we picked `clickhouse-connect` over the native driver because…")
- A reusable principle ("never log raw API keys, mask them at the edge")
- A non-obvious project fact ("the prod ClickHouse cluster is at `ch-prod.internal:8123`")
- A doc the user wants permanent ("AGENTS.md bullet about commit style")

**Don't** call it after every chat turn. ClickMem is not a transcript dump — the Stop hook already lands raw transcripts in cold storage. Only commit refined memories.

When you remember something, always pass:

- `project_id`: a readable project slug such as `auxten/clickmem`, or `global` for cross-project principles.
- `tags`: at least one short management/search tag, e.g. `workflow`, `deployment`, `security`, `hooks`.

Examples:

```bash
clickmem remember "Use mini as the persistent ClickMem server; local is client-only." \
  --project auxten/clickmem --tag workflow --tag deployment --kind principle

clickmem remember "Never log raw API keys." \
  --global --tag security --kind principle
```

---

## When to recall

- **Start of task** — before planning, editing, or running commands, derive the current `project_id`, infer 2–5 task tags, issue `clickmem_recall`, and load the relevant hits into context.
- **On demand** — whenever the user asks "what did we decide", "what's the convention here", or you need project context you don't have.
- Default scope is the current project + global memories. Pass `cross_project=true` only when the user explicitly asks to look across projects.

Startup protocol:

1. Determine `project_id` from the current repository when possible. Use a stable readable slug such as `auxten/clickmem`; use `global` only for cross-project principles.
2. Infer 2–5 short tags from the task, e.g. `deployment`, `git`, `workflow`, `hooks`, `security`, `storage`.
3. Call `clickmem_recall` with the user's prompt or task summary, `project_id`, those `tags`, `tag_mode="any"`, `limit=10`, and `timeout_seconds=5.0`.
4. If recall returns hits, place a concise `Relevant ClickMem memories` block in your working context before doing the task. Keep memory IDs available for traceability.
5. If recall times out, errors, or returns no hits, continue without memory context. Do not block the user waiting for memory recall.

Example MCP call:

```json
{
  "query": "deploy current project and commit code",
  "project_id": "auxten/clickmem",
  "tags": ["deployment", "git", "workflow"],
  "tag_mode": "any",
  "limit": 10,
  "timeout_seconds": 5.0,
  "agent": "cursor"
}
```

---

## The five operations

| Op | CLI | MCP tool | When |
|----|-----|----------|------|
| **Expand** | `clickmem remember "..."` | `clickmem_remember` | Commit a new refined memory |
| **Revise** | `clickmem edit <id> --content "..."` | `clickmem_edit` | Existing memory needs correction or scope change |
| **Contract** | `clickmem forget <id> --reason "..."` | `clickmem_forget` | Memory is no longer true; soft-delete with reason |
| **Reinforce** | `clickmem pin <id>` / `unpin <id>` | `clickmem_pin` | User marks a memory authoritative (immune to silent revision) |
| **Refuse** | `clickmem blacklist add "<pattern>"` | `clickmem_blacklist` | Pattern should never enter the store (e.g. PII, internal hostnames) |

Inspection helpers (same parity):

| | CLI | MCP tool |
|---|-----|----------|
| Recall | `clickmem recall "<query>"` | `clickmem_recall` |
| Show + history | `clickmem show <id> --history` | `clickmem_show` |
| List filtered | `clickmem list --project --kind --status` | `clickmem_list` |
| Conflicts queue | `clickmem conflicts` | `clickmem_conflicts` |
| Resolve a conflict | `clickmem resolve <id> --revise <peer>` | `clickmem_resolve` |
| Get cold raw | `clickmem get-raw <session_id>` | `clickmem_get_raw` |
| Recall scoring | `clickmem recall-trace "<query>"` | `clickmem_recall_trace` |

---

## Handling conflicts

When you call `clickmem_remember` and the response is `{status: "conflicted", id, peer_ids}`, the new memory is semantically close to an existing one but the text materially differs. **Stop and review** before the session ends:

1. `clickmem_show <id>` and `clickmem_show <peer_id>` to compare both sides.
2. Decide:
   - The new memory **supersedes** the old → `clickmem_resolve <peer_id> --revise <id>` (Revise).
   - The old memory is still right and the new one was wrong → `clickmem_resolve <id> --contract <peer_id>` (Contract the new one).
   - Both are actually compatible (different scopes, different projects) → `clickmem_resolve <id> --allow <peer_id>`.
3. If you're unsure, surface the pair to the user — never silently keep two contradictory memories.

Pinned memories short-circuit conflict surfacing: a new commit that conflicts with a pinned memory is rejected outright. You must explicitly `clickmem_edit` the pinned memory instead.

---

## Project + privacy partitioning

- `project_id` is frozen on the memory at commit time. Writes require an explicit readable project id (`owner/repo`) or explicit `global`.
- `tags` are required on writes; use them to make operational memories browseable and auditable.
- Recall scoring: same-project ×1.0, global (`project_id=''`) ×0.9, other-project ×0.0 by default.
- `privacy` is a separate dimension: `public`, `private` (default), `confidential`.
- `confidential` memories are excluded from MCP responses unless the caller passes `privacy_ack=true` — only do this if the user explicitly asks for confidential context.

Cross-project surfacing is opt-in: `clickmem project link <a> <b>` whitelists a pair, or pass `cross_project=true` on a single recall.

---

## Quick reference

```bash
# Start the server (idempotent)
clickmem service install
clickmem hooks install                # installs hooks for every detected agent

# Day-to-day
clickmem recall "deploy pipeline"
clickmem remember "Use uv for Python venv management on this repo" --project auxten/clickmem --tag tooling --kind principle
clickmem conflicts                     # any unresolved pairs?
clickmem show <id> --history           # how did this memory evolve?

# Management
clickmem list --project current --kind decision --status active
clickmem pin <id>                      # mark authoritative
clickmem blacklist add "internal-hostname.example.com"
clickmem dashboard open                # opens /dashboard in browser
```

The dashboard at `http://127.0.0.1:9527/dashboard` is the human-in-the-loop surface for browsing, editing, and resolving conflicts — point the user there when they want to clean up the store rather than driving every change through the CLI.
