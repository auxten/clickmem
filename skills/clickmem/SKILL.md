---
name: clickmem
description: |
  CEO Brain memory system for AI agents. Use when:
  - User mentions preferences, decisions, project facts → remember or let auto-capture handle it
  - Need project context, past decisions, or principles → recall or ceo_brief
  - User wants to delete or correct outdated memory → forget
  - Need project overview or decision history → portfolio, decisions, principles
  - User asks about memory stats → status
metadata:
  openclaw:
    emoji: 🧠
    requires:
      bins:
        - memory
    install: "pip install clickmem && memory service install && memory hooks install"
---

# ClickMem — CEO Brain for AI Agents

All commands support `--json` for structured output.

---

## Quick Reference

```bash
# Discovery & Setup
memory discover                     # Detect installed agents + session counts
memory hooks install                # Install hooks for all agents
memory import                       # Import conversation history (async)
memory import --path ~/project      # Import docs from a specific directory

# Search & Recall
memory recall "<query>"             # Hybrid search (legacy + CEO Brain)
memory status                       # Stats + CEO entities + import progress

# CEO Brain
memory portfolio                    # All projects overview
memory brief --project-id <id>      # Detailed project briefing
memory decisions                    # List decisions
memory principles                   # List principles
memory projects                     # List projects

# Memory Operations
memory remember "<content>"         # Store a memory
memory forget <id>                  # Delete by ID
memory maintain                     # Run maintenance + CEO dedup

# Help
memory help [subcmd]                # Help for any command
```

---

## CEO Brain Tools (MCP)

These tools are available via MCP to Claude Code and Cursor:

| Tool | When to use |
|------|-------------|
| `ceo_brief` | Get project context, principles, recent decisions |
| `ceo_decide` | Decision support — find related past decisions and principles |
| `ceo_remember` | Store a structured decision, principle, or episode |
| `ceo_review` | Check a plan against existing principles |
| `ceo_retro` | Retrospective — review decisions and extract new principles |
| `ceo_portfolio` | Cross-project overview |

---

## Remember

**When**: User mentions a preference, decision, or important context.

```bash
memory remember "<content>" --layer semantic --category preference --json
```

Categories: `preference`, `decision`, `knowledge`, `person`, `project`, `insight`
Layers: `working` (session), `episodic` (medium-term), `semantic` (long-term, default)

---

## Recall

**When**: Before starting a task, or when user asks "what did we discuss about X".

```bash
memory recall "<query>" --top-k 5 --json
```

Searches both legacy memories and CEO Brain entities (decisions, principles, episodes).
Results are project-scope-aware — same-project results boosted, other-project results deprioritized.

---

## Status

**When**: User asks about memory stats.

```bash
memory status --json
```

Shows: legacy memory counts, CEO Brain entity counts (projects/decisions/principles/episodes), import progress, LLM config.
