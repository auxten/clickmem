# ClickMem

**Local long-term memory for AI coding agents.**

AI coding assistants (OpenClaw, Claude Code, etc.) forget everything between sessions. Context compaction discards the preferences you stated, the decisions you made, the names you mentioned. ClickMem gives your agent persistent, searchable memory that runs entirely on your machine — no API keys, no cloud calls, no data leaving your laptop.

## How It Works

ClickMem stores memories in [chDB](https://github.com/chdb-io/chdb) (embedded ClickHouse — a full analytical database running in-process, no server needed) and generates vector embeddings locally with [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B). When your agent starts a conversation, ClickMem automatically recalls relevant memories and injects them into context. When a conversation ends, it captures important information for later.

### Three-Layer Memory Model

```
┌─────────────────────────────────────────────────────────────────┐
│  L0  Working Memory  (scratchpad)                               │
│  "User is building Phase 2, last discussed HNSW index config"   │
│  Always injected · Overwritten each conversation · ≤500 tokens  │
├─────────────────────────────────────────────────────────────────┤
│  L1  Episodic Memory  (event timeline)                          │
│  "03-04: Decided on Python core + JS plugin architecture"       │
│  Recalled on demand · Time-decayed · Auto-compressed monthly    │
├─────────────────────────────────────────────────────────────────┤
│  L2  Semantic Memory  (long-term knowledge)                     │
│  "[preference] Prefers SwiftUI over UIKit"                      │
│  "[person] Alice is the backend lead"                           │
│  Always injected · Permanent · Updated only on contradiction    │
└─────────────────────────────────────────────────────────────────┘
```

- **L0 Working** — What the agent is doing right now. Overwritten every session.
- **L1 Episodic** — What happened and when. Decays over 120 days, old entries compressed into monthly summaries, recurring patterns promoted to L2.
- **L2 Semantic** — Durable facts, preferences, and people. Never auto-deleted. Smart upsert detects duplicates and merges via LLM.

### Search & Retrieval

Memories are found via **hybrid search** combining:
1. **Vector similarity** — 256-dim cosine distance on Qwen3 embeddings
2. **Keyword matching** — word-level hit rate on content and tags
3. **Time decay** — exponential decay for episodic, recency boost for semantic
4. **MMR diversity** — re-ranks to avoid returning redundant results

### Self-Maintenance

ClickMem maintains itself automatically:
- Stale episodic entries (120+ days, never accessed) are cleaned up
- Old episodic entries are compressed into monthly summaries
- Recurring patterns are promoted from episodic to semantic
- Soft-deleted entries are purged after 7 days
- Semantic memories are periodically reviewed for staleness

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/auxten/clickmem/main/setup.sh | bash
```

Or clone manually:

```bash
git clone https://github.com/auxten/clickmem && cd clickmem && ./setup.sh
```

> Set `CLICKMEM_DIR` to customize the install path (default: `~/clickmem`).

**What `setup.sh` does:**
1. Checks Python >= 3.10 and `uv`
2. Creates venv and installs all dependencies
3. Downloads the Qwen3-Embedding-0.6B model (~350 MB, first run only)
4. Runs tests to verify the environment
5. Imports existing OpenClaw history (if `~/.openclaw/` exists)
6. Installs the OpenClaw plugin hook

**Resource usage:** ~500 MB RAM for the embedding model, ~200 MB disk for chDB data (grows with memory count).

## Usage

```bash
# Store a memory
memory remember "User prefers dark mode" --layer semantic --category preference

# Semantic search
memory recall "UI preferences"

# Delete a memory (by ID, prefix, or content description)
memory forget "dark mode preference"

# Browse memories
memory review --layer semantic

# Show statistics
memory status

# Run maintenance (cleanup, compression, promotion)
memory maintain

# Import OpenClaw history
memory import-openclaw

# Export context to workspace .md files
memory export-context /path/to/workspace
```

All commands support `--json` for machine-readable output.

## Why Not Just Use MEMORY.md?

| | MEMORY.md | ClickMem |
|---|---|---|
| Search | Keyword grep | Hybrid vector + keyword + MMR |
| Structure | Flat text file | Three-layer model with lifecycles |
| Deduplication | Manual | LLM-judged smart upsert |
| Maintenance | Manual | Automatic decay, compression, promotion |
| Embeddings | Remote API (costs money, leaks data) | Local Qwen3 (free, private) |
| Context loss | Gone after compaction | Emergency flush preserves context |

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
