# ClickMem

Three-layer, self-maintaining, locally searchable memory for OpenClaw agents.

Replaces OpenClaw's native Gemini embedding API with local **Qwen3-0.6B** — zero cost, no rate limits, no data leakage.

## One-line Install

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
3. Runs tests to verify the environment
4. Imports existing OpenClaw history (if `~/.openclaw/` exists)
5. Installs the OpenClaw hook (if `openclaw` CLI is available)

## Usage

```bash
# Store a memory
memory remember "User prefers dark mode" --layer semantic --category preference

# Semantic search
memory recall "UI preferences"

# Browse memories
memory review --layer episodic

# Show statistics
memory status

# Import OpenClaw history manually
memory import-openclaw

# Export context to workspace
memory export-context /path/to/workspace
```

## Architecture

| Layer | Purpose | Retention |
|-------|---------|-----------|
| L0 Working | Current session context | Overwritten per session |
| L1 Episodic | Daily events & decisions | Auto-decays after 120 days |
| L2 Semantic | Long-term knowledge & preferences | Permanent |

**Stack:** chDB (embedded ClickHouse) + Qwen3-Embedding-0.6B (local, 256d vectors) + hybrid search (vector + keyword + MMR)

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
