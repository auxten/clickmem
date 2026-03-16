# ClickMem

**Unified memory center for AI coding agents — local-first, LAN-shareable.**

AI coding assistants (Claude Code, Cursor, OpenClaw, etc.) forget everything between sessions. Context compaction discards the preferences you stated, the decisions you made, the names you mentioned. ClickMem gives your agents persistent, searchable memory that runs on your machine and can be shared across all your tools via a single server on the local network.

## Architecture

```
┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐
│ Claude   │  │ Cursor   │  │ OpenClaw │  │ CLI (any machine)│
│ Code     │  │          │  │          │  │                  │
└────┬─────┘  └────┬─────┘  └────┬─────┘  └────────┬─────────┘
     │MCP          │MCP          │MCP/HTTP         │HTTP
     │             │             │                 │
┌────▼─────────────▼─────────────▼─────────────────▼───────────┐
│                ClickMem Server  (single port)                │
│  ┌───────────────────────────┐  ┌──────────────────────────┐ │
│  │ /v1/*  REST API (JSON)    │  │  mDNS Discovery          │ │
│  │ /sse   MCP SSE connection │  │  _clickmem._tcp          │ │
│  └─────────────┬─────────────┘  └──────────────────────────┘ │
│          ┌─────▼─────────────────────────────────────────┐   │
│          │  memory_core — chDB + Qwen3 embeddings        │   │
│          │  hybrid search · LLM upsert · auto-maintain   │   │
│          └───────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

**One server, all your tools.** Start `memory serve` on any machine and every Claude Code session, Cursor workspace, and OpenClaw agent on the LAN shares the same memory — preferences learned once are recalled everywhere. The server keeps embedding and LLM models resident in memory, so recall takes ~0.5s instead of ~11s per cold CLI call.

## How It Works

ClickMem stores memories in [chDB](https://github.com/chdb-io/chdb) (embedded ClickHouse — a full analytical database running in-process) and generates vector embeddings locally with [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B). When your agent starts a conversation, ClickMem automatically recalls relevant memories. When a conversation ends, it captures important information for later.

### Memory Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  L2  Semantic Memory  (long-term knowledge, highest refinement) │
│  "[preference] Prefers SwiftUI over UIKit"                      │
│  "[person] Alice is the backend lead"                           │
│  Recall priority · Refined memories get scoring boost            │
│  Updated only on contradiction or continual refinement           │
├───────── ▲ promote (recurring patterns)  ▲ refine ──────────────┤
│  L1  Episodic Memory  (event timeline)                          │
│  "03-04: Decided on Python core + JS plugin architecture"       │
│  Recalled on demand · Time-decayed · Auto-compressed monthly    │
│  Carries raw_id for lineage tracking to source transcript       │
├───────── ▲ extract (LLM structured extraction) ─────────────────┤
│  Raw Transcripts  (separate table, append-only)                 │
│  Complete conversation text · Not searched during recall         │
│  Enables re-extraction and refinement from original data        │
└─────────────────────────────────────────────────────────────────┘
```

- **Raw** — Complete conversation transcripts stored in a separate table. Append-only, no embeddings. Not searched during recall — only consumed by the extraction and refinement pipelines. Enables "re-extraction" when models or prompts improve.
- **L1 Episodic** — Structured events extracted from raw transcripts. Each entry carries a `raw_id` pointing back to the source transcript. Decays over 120 days, old entries compressed into monthly summaries, recurring patterns promoted to L2.
- **L2 Semantic** — Durable facts, preferences, and people. Never auto-deleted. Smart upsert detects duplicates and merges via LLM. Refined memories (`source=refinement`) get a 1.15x scoring boost during recall.

### Search & Retrieval

Memories are found via **hybrid search** combining:
1. **Vector similarity** — 256-dim cosine distance on Qwen3 embeddings
2. **Keyword matching** — word-level hit rate on content, tags, and entities
3. **Time decay** — different strategies per layer (see below)
4. **Popularity boost** — frequently recalled memories score higher
5. **Refinement boost** — memories refined by the continual refinement engine score 1.15x higher
6. **MMR diversity** — re-ranks to avoid returning redundant results

### Time Decay Weights

Different memory layers use fundamentally different decay strategies, reflecting their different roles:

![Decay Weight Curves](docs/decay_weights.png)

**L1 Episodic — Exponential Decay** (left): Events fade quickly over time, like human episodic memory. The half-life is 60 days — a 2-month-old event scores only 50% of a fresh one. At 120 days with zero access, entries are auto-cleaned. Formula: `w = e^(-ln2/T * t)`.

**L2 Semantic — Logarithmic Recency** (right): Long-term knowledge should almost never lose relevance just because it's old. The recency weight uses the Weber-Fechner law — human perception of time differences is logarithmic: the gap between "1 minute ago" and "1 hour ago" feels significant, but "3 months ago" vs "6 months ago" feels nearly identical. The score maps to `[0.8, 1.0]`, acting as a mild tiebreaker rather than a dominant factor. Formula: `w = 0.8 + 0.2 / (1 + k * ln(1 + t/τ))`.

Concrete weight values at different ages:

| Age | L1 Episodic | L2 Semantic |
|-----|-------------|-------------|
| 1 min | 1.000 | 0.981 |
| 1 hour | 0.999 | 0.924 |
| 1 day | 0.989 | 0.896 |
| 7 days | 0.922 | 0.884 |
| 30 days | 0.707 | 0.877 |
| 60 days | 0.500 | 0.874 |
| 90 days | 0.354 | 0.872 |
| 120 days | 0.250 | 0.871 |
| 180 days | 0.125 | 0.870 |
| 1 year | 0.015 | 0.867 |

L1 episodic weight drops by half every 60 days and is nearly zero after a year — old events naturally fade out. L2 semantic weight stays in a narrow band (0.87–0.98) regardless of age, so a fact stored a year ago still scores 87% of a freshly stored one. The only way semantic memories lose relevance is through contradiction-based updates, not time.

### Local LLM for Summarization & Extraction

ClickMem can use a local LLM for memory extraction, smart upsert, refinement, and maintenance — no cloud API keys needed. On Apple Silicon, it uses [MLX](https://github.com/ml-explore/mlx) for fast inference; on other platforms, it falls back to HuggingFace Transformers.

**Supported models:**

| Model | Params | RAM | Use case |
|-------|--------|-----|----------|
| `Qwen/Qwen3.5-2B` | 2B | ~1.5 GB | Fast extraction, low-resource machines (default) |
| `Qwen/Qwen3.5-4B` | 4B | ~3 GB | Better extraction quality |
| `Qwen/Qwen3.5-9B` | 9B | ~6 GB | Best quality for refinement and complex tasks |

```bash
# Configure LLM mode
export CLICKMEM_LLM_MODE=local    # local | remote | auto (default: auto)
export CLICKMEM_LOCAL_MODEL=Qwen/Qwen3.5-2B   # default; also supports 4B and 9B
export CLICKMEM_LLM_MODEL=Qwen/Qwen3.5-2B      # remote fallback model (default: same as local)
export CLICKMEM_REFINE_THRESHOLD=1              # auto-refine after N unprocessed raw transcripts

# Install local LLM backend (pick one)
pip install mlx-lm        # macOS Apple Silicon (recommended)
pip install transformers   # cross-platform (already included via sentence-transformers)
```

In `auto` mode, ClickMem tries the local model first and falls back to the remote API if unavailable. The local model handles: conversation extraction, episodic compression, pattern promotion, semantic review, and smart upsert deduplication.

### Continual Refinement

When unprocessed raw transcripts accumulate past a configurable threshold (default: 10), ClickMem automatically triggers a **continual refinement** cycle in a background thread:

1. **Re-extract** — Processes unprocessed raw transcripts that may have been missed or can benefit from improved extraction
2. **Cluster** — Groups similar L2 semantic memories by embedding cosine similarity (threshold > 0.7)
3. **Deduplicate & Merge** — Uses the LLM to detect duplicate memories within each cluster and merge them into single, stronger entries
4. **Quality Gate** — Applies an inclusion bar: memories must be actionable, stable across sessions, and non-sensitive to survive

Refined memories are stored with `source=refinement` and receive a scoring boost during recall. Original memories are soft-deleted but preserved for audit.

```bash
# Run refinement manually
memory refine

# Dry-run to see what would change
memory refine --dry-run

# Only run if unprocessed raw >= N
memory refine --threshold 10
```

### Self-Maintenance

ClickMem maintains itself automatically:
- Stale episodic entries (120+ days, never accessed) are cleaned up
- Old episodic entries are compressed into monthly summaries
- Recurring patterns are promoted from episodic to semantic
- Soft-deleted entries are purged after 7 days
- Semantic memories are periodically reviewed for staleness

## Install

```bash
git clone https://github.com/auxten/clickmem && cd clickmem && ./setup.sh
```

> Set `CLICKMEM_DIR` to customize the install path (default: `~/clickmem`).

For server/MCP features, install the server extras:

```bash
pip install -e ".[server]"    # REST API + MCP + mDNS
pip install -e ".[all]"       # server + LLM support
```

**What `setup.sh` does:**
1. Checks Python >= 3.10 and `uv`
2. Creates venv and installs all dependencies
3. Installs and starts a background service (launchd on macOS, systemd on Linux)
4. Smoke-tests the API server (retries while it starts up)
5. Imports existing OpenClaw history (if `~/.openclaw/` exists)
6. Installs the OpenClaw plugin
7. Installs Claude Code skill (`/clickmem` slash command) + HTTP hooks for auto recall/capture
8. Installs Cursor hooks globally (`~/.cursor/hooks.json`) for auto recall/capture

**Resource usage:** ~500 MB RAM for the embedding model, ~200 MB disk for chDB data (grows with memory count). With a local LLM loaded (~4 GB for Qwen3.5-2B via MLX), total RAM usage is ~4.5 GB.

## Usage

### CLI — Basic Memory Operations

```bash
# Store a memory
memory remember "User prefers dark mode" --layer semantic --category preference

# Ingest raw conversation text (stores raw + extracts memories)
memory ingest "user: I like dark mode\nassistant: Noted" --source cli

# Semantic search
memory recall "UI preferences"

# Delete a memory (by ID, prefix, or content description)
memory forget "dark mode preference"

# Browse memories
memory review --layer semantic

# Show statistics (includes raw transcript counts)
memory status

# Run maintenance (cleanup, compression, promotion)
memory maintain

# Run continual refinement (deduplicate, merge, quality-gate L2)
memory refine

# Import OpenClaw history
memory import-openclaw

# Export context to workspace .md files
memory export-context /path/to/workspace
```

All commands support `--json` for machine-readable output.

### Service — Background Server

ClickMem runs as a background service so the API server (port 9527) is always available.

```bash
# Install and start the service (done automatically by setup.sh)
memory service install

# Manage the service
memory service status          # Check if running
memory service logs -f         # Follow server logs
memory service stop            # Stop the service
memory service start           # Start the service
memory service uninstall       # Remove the service
```

On macOS this creates a launchd user agent (`~/Library/LaunchAgents/com.clickmem.server.plist`); on Linux a systemd user unit (`~/.config/systemd/user/clickmem.service`).

### Server — LAN Memory Sharing

Start the server so all tools on your network share the same memory. A single process serves **both** the REST API and MCP SSE on one port:

```bash
# Generate an API key
memory serve --gen-key
# → Generated API key: a1b2c3d4e5f6...

# Start server (LAN-accessible, REST + MCP SSE on one port)
export CLICKMEM_API_KEY=a1b2c3d4e5f6...
memory serve --host 0.0.0.0 --port 9527

# REST-only (disable MCP SSE)
memory serve --host 0.0.0.0 --no-mcp

# Enable SQL endpoint for debugging
memory serve --host 0.0.0.0 --debug
```

**Endpoints (all on the same port):**

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/v1/health` | Health check (no auth) |
| `POST` | `/v1/recall` | Search memories |
| `POST` | `/v1/remember` | Store a memory |
| `POST` | `/v1/extract` | LLM-extract memories from text |
| `POST` | `/v1/ingest` | Raw-first ingestion (stores transcript + extracts) |
| `DELETE` | `/v1/forget/{id}` | Delete a memory |
| `GET` | `/v1/review` | List memories by layer |
| `GET/POST` | `/v1/status` | Layer and raw transcript statistics |
| `POST` | `/v1/maintain` | Run maintenance |
| `POST` | `/v1/sql` | Raw SQL (debug mode only) |
| `POST` | `/hooks/claude-code` | Claude Code hook handler (auto recall/capture) |
| `GET` | `/sse` | MCP SSE connection |
| `POST` | `/messages/` | MCP message posting |

### Remote CLI

Use any `memory` command against a remote server:

```bash
# Via flags
memory recall "project architecture" --remote http://192.168.1.100:9527 --api-key xxx

# Via environment variables
export CLICKMEM_REMOTE=http://192.168.1.100:9527
export CLICKMEM_API_KEY=xxx
memory recall "project architecture"

# Auto-discover server on LAN via mDNS
memory recall "project architecture" --remote auto
```

### MCP Server — Claude Code & Cursor Integration

ClickMem speaks [MCP (Model Context Protocol)](https://modelcontextprotocol.io/), so Claude Code and Cursor can use it natively as a tool provider.

**Local (stdio) — best for same-machine use:**

```bash
memory mcp
```

**Remote (SSE) — built into `memory serve`:** No separate command needed. `memory serve` exposes MCP SSE at `/sse` on the same port as the REST API.

```bash
memory serve --host 0.0.0.0 --port 9527
# REST API → http://host:9527/v1/...
# MCP SSE  → http://host:9527/sse
```

#### Claude Code Configuration

Add to `~/.claude.json` or project `.mcp.json`:

```json
{
  "mcpServers": {
    "clickmem": {
      "command": "clickmem-mcp",
      "args": []
    }
  }
}
```

For remote (another machine running `memory serve`):

```json
{
  "mcpServers": {
    "clickmem": {
      "url": "http://192.168.1.100:9527/sse"
    }
  }
}
```

#### Claude Code Hooks — Automatic Recall & Capture

`setup.sh` registers [HTTP hooks](https://docs.anthropic.com/en/docs/claude-code/hooks) in `~/.claude/settings.json` that give Claude Code the same auto-recall/capture behavior as Cursor:

- **`SessionStart`** — Recalls relevant memories and injects them as `additionalContext`
- **`UserPromptSubmit`** — Buffers the user prompt for ingestion
- **`Stop`** — Ingests each completed turn (stores raw transcript + extracts structured memories)
- **`SessionEnd`** — Runs lightweight maintenance

Hooks communicate directly with the ClickMem API server via HTTP POST to `http://127.0.0.1:9527/hooks/claude-code`. No external scripts needed — the server handles everything. All errors are fail-open; hooks never block Claude Code.

To manually configure hooks (e.g. for a remote server), add to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [{ "hooks": [{ "type": "http", "url": "http://127.0.0.1:9527/hooks/claude-code", "timeout": 30 }] }],
    "UserPromptSubmit": [{ "hooks": [{ "type": "http", "url": "http://127.0.0.1:9527/hooks/claude-code", "timeout": 30 }] }],
    "Stop": [{ "hooks": [{ "type": "http", "url": "http://127.0.0.1:9527/hooks/claude-code", "timeout": 30 }] }],
    "SessionEnd": [{ "hooks": [{ "type": "http", "url": "http://127.0.0.1:9527/hooks/claude-code", "timeout": 30 }] }]
  }
}
```

#### Cursor Configuration

Add to `.cursor/mcp.json` in your project:

```json
{
  "mcpServers": {
    "clickmem": {
      "command": "clickmem-mcp",
      "args": []
    }
  }
}
```

For remote:

```json
{
  "mcpServers": {
    "clickmem": {
      "url": "http://192.168.1.100:9527/sse"
    }
  }
}
```

#### Cursor Hooks — Automatic Recall & Capture

`setup.sh` installs user-level Cursor hooks (`~/.cursor/hooks.json`) that work across **all** your Cursor projects:

- **`sessionStart`** — Recalls relevant memories and injects them as context
- **`afterAgentResponse`** — Ingests each conversation turn (stores raw transcript + extracts memories)
- **`sessionEnd` / `stop`** — Runs lightweight maintenance

Hooks communicate with the ClickMem API server on `localhost:9527`. All errors are fail-open — hooks never block Cursor.

**MCP Tools available to agents:**

| Tool | Description |
|------|-------------|
| `clickmem_recall` | Search memories by semantic query |
| `clickmem_remember` | Store a new memory |
| `clickmem_extract` | Extract memories from conversation text |
| `clickmem_ingest` | Raw-first ingestion: stores transcript + extracts memories |
| `clickmem_forget` | Delete a memory |
| `clickmem_status` | Show memory statistics (includes raw transcript counts) |
| `clickmem_working` | Get or set working memory (deprecated) |

### LAN Discovery

ClickMem servers advertise themselves via mDNS (`_clickmem._tcp`). Find servers on your network:

```bash
memory discover
# → ✓ 192.168.1.100:9527  v0.1.0  (rest+mcp)
# → To connect: memory recall 'query' --remote http://192.168.1.100:9527
```

## Comparison

| | MEMORY.md | Mem0 | Supermemory | **ClickMem** |
|---|---|---|---|---|
| Runs locally | ✅ file | ❌ cloud API | ❌ cloud API | **✅ fully local** |
| Privacy | ✅ | ❌ data sent to API | ❌ data sent to API | **✅ zero data leaves machine** |
| Embeddings | N/A | Remote (costs $) | Remote (costs $) | **Local Qwen3 (free)** |
| Memory layers | Flat file | Semantic + Episodic | Hierarchical | **Raw + L1 Episodic + L2 Semantic** |
| Search | Keyword grep | Vector + Graph | Hybrid + Relations | **Vector + Keyword + MMR** |
| Time decay | None | None | Smart forgetting | **Per-layer decay (exp + log)** |
| Deduplication | Manual | LLM 4-op upsert | Relational versioning | **LLM 4-op upsert** |
| Self-maintenance | Manual | ❌ | ❌ | **Auto (cleanup/compress/promote/refine)** |
| Multi-tool sharing | ❌ | Cloud only | Cloud only | **✅ LAN server + MCP** |
| Access tracking | ❌ | ❌ | ✅ | **✅ popularity-weighted recall** |
| Result diversity | ❌ | ❌ | ❌ | **✅ MMR re-ranking** |
| MCP support | ❌ | ❌ | ✅ (cloud) | **✅ stdio + SSE** |
| Cost | Free | Pay per API call | Pay per API call | **Free** |

## Development

```bash
make test          # Full test suite
make test-fast     # Skip semantic tests (no model download)
make deploy-test   # rsync to remote + test
make deploy        # rsync to remote + full setup
```

## Requirements

- Python >= 3.10
- [uv](https://docs.astral.sh/uv/) package manager
- ~1 GB disk for model + data
- macOS or Linux (chDB requirement)
- Server extras: `pip install -e ".[server]"` for FastAPI, MCP, mDNS
