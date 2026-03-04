---
name: clickmem
description: |
  Memory management for AI agents. Use when:
  - User mentions preferences, decisions, project facts, or important context → remember
  - Starting a task or user asks "what did we discuss" → recall
  - User wants to delete or correct outdated memory → forget
  - User asks about memory stats or capacity → status
metadata:
  openclaw:
    emoji: 🧠
    requires:
      bins:
        - memory
    install: "git clone https://github.com/auxten/clickmem && cd clickmem && ./setup.sh"
---

# ClickMem — Memory Operations

All commands use `--json` for structured output. See `references/cli-reference.md` for full parameter details.

---

## Remember

**When**: User mentions a preference, decision, project fact, workflow convention, or any important context worth persisting.

```bash
memory remember "<content>" --layer semantic --category knowledge --json
```

Choose the right `--category`:
- `preference` — user likes/dislikes, tool choices
- `decision` — architectural or design decisions
- `knowledge` — facts, definitions, domain info
- `person` — info about people
- `project` — project structure, conventions
- `workflow` — process, CI/CD, deployment patterns
- `insight` — lessons learned, debugging findings
- `context` — session/situational context

Choose the right `--layer`:
- `working` — short-lived, current session only
- `episodic` — medium-term, event-based
- `semantic` — long-term, factual (default)

Optional: `--tags "tag1,tag2"` for extra metadata.

**Example**:
```bash
memory remember "User prefers pytest over unittest for all new test files" --layer semantic --category preference --json
```

---

## Recall

**When**: Before starting a task, search for relevant context. Also when user asks "what did we discuss about X" or "do you remember Y".

```bash
memory recall "<query>" --top-k 5 --json
```

Options:
- `--top-k 5` — max results (default 10)
- `--min-score 0.3` — filter low-relevance matches
- `--layer semantic` — restrict to a specific layer
- `--category preference` — restrict to a category

**Example**:
```bash
memory recall "testing preferences" --top-k 3 --category preference --json
```

---

## Forget

**When**: User asks to delete a memory, correct outdated info, or remove something incorrect.

Two-step process — first find the memory ID, then delete:

```bash
# Step 1: Find the memory
memory recall "<query>" --json

# Step 2: Delete by ID (or ID prefix)
memory forget <id> --json
```

**Example**:
```bash
memory recall "old database convention" --top-k 3 --json
# → returns memories with IDs
memory forget a1b2c3d4 --json
```

---

## Status

**When**: User asks about memory stats, capacity, or how many memories are stored.

```bash
memory status --json
```

Returns counts per layer (Working/Episodic/Semantic) and total.
